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

    async def append(self, session_id: str, role: str, content: str) -> None:
        if role not in ("user", "assistant"):
            return

        text = content.strip()
        if not text:
            return

        async with get_session() as db:
            session_row = await self._get_or_create_session(db, session_id)
            db.add(Message(session_id=session_row.id, role=role, content=text))
            await db.commit()

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
