"""
PostgresConversationStore（Phase 0）
取代 InMemoryConversationStore：session 記憶持久化在 Postgres，
不受 Vercel Serverless instance 回收影響。

行為對齊 InMemoryConversationStore：
- get_history 回傳最近 max_messages 則訊息（依 created_at 排序）
- append 忽略非 user/assistant 角色與空字串內容
- reset 清除該 session 的所有訊息（並結束該 session，讓下次 append 開新的一筆）

與現有前端相容：session_id 是任意字串（如 crypto.randomUUID() 或測試固定字串），
不強制要求合法 UUID 格式 —— 對應到 sessions.client_key 欄位，
而不是直接拿來當 Postgres UUID 主鍵。

目前每個新 session_id 會自動建立一個匿名 User（Phase 2 引入真實帳號/Profile 後，
可以把 external_ref 換成真實使用者識別，這裡的自動建立邏輯屆時再收斂）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import ConversationSession, Message, User

# messages 對 personas / rules / rule_versions 有 FK：確保這些 table 已註冊進
# Base.metadata，flush 時才解析得到外鍵（不依賴呼叫端剛好先 import 過）
from app.db import models_persona, models_rule  # noqa: F401
from app.storage.conversation_store import ConversationStore


class PostgresConversationStore(ConversationStore):
    def __init__(self, max_messages: int = 20):
        self.max_messages = max_messages

    async def _get_or_create_session(
        self, db: AsyncSession, client_key: str
    ) -> ConversationSession:
        result = await db.execute(
            select(ConversationSession).where(ConversationSession.client_key == client_key)
        )
        session_row = result.scalar_one_or_none()
        if session_row is not None:
            return session_row

        # 目前 Phase 0 尚未有真實使用者帳號系統，每個新 session_id 對應一個匿名 User。
        # external_ref 用 client_key 本身當佔位識別，Phase 2 導入真實 Profile 後再調整。
        user_stmt = (
            pg_insert(User)
            .values(id=uuid.uuid4(), external_ref=client_key)
            .on_conflict_do_nothing(index_elements=[User.external_ref])
            .returning(User.id)
        )
        result = await db.execute(user_stmt)
        user_id = result.scalar_one_or_none()
        if user_id is None:
            existing = await db.execute(select(User).where(User.external_ref == client_key))
            user_id = existing.scalar_one().id

        session_row = ConversationSession(user_id=user_id, client_key=client_key)
        db.add(session_row)
        await db.flush()
        return session_row

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        async with get_session() as db:
            result = await db.execute(
                select(ConversationSession).where(ConversationSession.client_key == session_id)
            )
            session_row = result.scalar_one_or_none()
            if session_row is None:
                return []

            msg_result = await db.execute(
                select(Message)
                .where(Message.session_id == session_row.id)
                .order_by(Message.created_at.desc())
                .limit(self.max_messages)
            )
            messages = list(reversed(msg_result.scalars().all()))
            return [{"role": m.role, "content": m.content} for m in messages]

    async def ensure_session(self, session_id: str) -> None:
        """
        Phase 4：在 Rule Engine 之前先建立 session。新建的列由 server default 記下當下的
        資料庫快照（sessions.rule_snapshot），Rule Engine 用它判定規則版本是否在 session
        開始前就存在（原本 session 要到回覆後 append() 才建立，首輪沒有判定基準）。
        """
        async with get_session() as db:
            await self._get_or_create_session(db, session_id)
            await db.commit()

    async def append(
        self,
        session_id: str,
        role: str,
        content: str,
        persona_id: str | None = None,
        rule_id: str | None = None,
        rule_version_id: str | None = None,
        output_replaced: bool = False,
    ) -> str | None:
        if role not in ("user", "assistant"):
            return None

        text = content.strip()
        if not text:
            return None

        async with get_session() as db:
            session_row = await self._get_or_create_session(db, session_id)
            message = Message(
                session_id=session_row.id,
                role=role,
                content=text,
                persona_id=uuid.UUID(persona_id) if persona_id else None,
                rule_id=uuid.UUID(rule_id) if rule_id else None,
                rule_version_id=uuid.UUID(rule_version_id) if rule_version_id else None,
                output_replaced=output_replaced,
            )
            db.add(message)
            await db.flush()  # 取得 message.id，供 Phase 3 example_usage_log 記錄是哪一則回覆
            message_id = str(message.id)
            await db.commit()
            return message_id

    async def reset(self, session_id: str) -> None:
        async with get_session() as db:
            result = await db.execute(
                select(ConversationSession).where(ConversationSession.client_key == session_id)
            )
            session_row = result.scalar_one_or_none()
            if session_row is None:
                return
            await db.delete(session_row)  # cascade 刪除底下的 messages
            await db.commit()
