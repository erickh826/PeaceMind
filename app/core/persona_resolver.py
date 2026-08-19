"""
Persona Resolver（Phase 1）
決定某次對話要用哪個 persona，並記錄切換歷史（T-Q13/T-Q14）。

優先序（Phase 1 只做前兩層）：
  1. persona_assignments（治療師手動指派，T-Q15）
  2. is_default=true 的系統預設 persona
  3.（Phase 2 才會加入）persona_match_conditions 自動匹配 —— 需要
     user_profiles / profile_topics 這些 Phase 2 才建立的資料，Phase 1 先跳過

DATABASE_URL 未設定時（本地快速開發 / 測試環境），直接回傳跟原本寫死內容
一致的 fallback persona，確保沒有資料庫也能跑。

注意：手動指派目前透過 users.external_ref 對應 session_id（沿用 Phase 0
PostgresConversationStore 的匿名 user 綁定方式）。這只是暫時的身份識別，
Phase 2 導入真實 Profile/帳號系統後應該要改成更明確的使用者識別，
屆時這個模組的查詢邏輯需要一併更新。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from app.prompts.system_prompt import DEFAULT_PERSONA_FRAGMENT


@dataclass(frozen=True)
class ResolvedPersona:
    id: str | None
    name: str
    system_prompt_fragment: str


FALLBACK_PERSONA = ResolvedPersona(
    id=None,
    name="Boon",
    system_prompt_fragment=DEFAULT_PERSONA_FRAGMENT,
)


async def resolve_persona(user_client_key: str | None = None) -> ResolvedPersona:
    """
    解析目前對話應該使用的 persona。

    Args:
        user_client_key: 前端傳入的 session_id，用於查詢是否有治療師手動指派
                          給這個使用者的 persona（透過 users.external_ref 對應）
    """
    if not os.environ.get("DATABASE_URL"):
        return FALLBACK_PERSONA

    from sqlalchemy import select

    from app.db import get_session
    from app.db.models import User
    from app.db.models_persona import Persona, PersonaAssignment

    async with get_session() as db:
        persona_row = None

        if user_client_key:
            result = await db.execute(
                select(Persona)
                .join(PersonaAssignment, PersonaAssignment.persona_id == Persona.id)
                .join(User, User.id == PersonaAssignment.user_id)
                .where(User.external_ref == user_client_key)
                .where(Persona.status == "active")
            )
            persona_row = result.scalar_one_or_none()

        if persona_row is None:
            result = await db.execute(
                select(Persona).where(Persona.is_default.is_(True), Persona.status == "active")
            )
            persona_row = result.scalar_one_or_none()

        if persona_row is None:
            # personas 表存在但沒有任何符合條件的資料（理論上不該發生，因為
            # migration 已種入 default persona）—— 保底退回寫死內容，不讓對話中斷
            return FALLBACK_PERSONA

        return ResolvedPersona(
            id=str(persona_row.id),
            name=persona_row.name,
            system_prompt_fragment=persona_row.system_prompt_fragment,
        )


async def record_persona_usage(session_client_key: str, persona: ResolvedPersona) -> None:
    """
    若該 session 目前記錄的 persona 與這次解析出的不同，寫入 persona_switch_log
    並更新 sessions.persona_id（T-Q13/T-Q14）。

    只有 session 已經存在（即之前至少有過一次 append）且 DB 有接時才會執行；
    全新 session 的第一則訊息不算「切換」，不寫入記錄。
    """
    if not os.environ.get("DATABASE_URL") or persona.id is None:
        return

    import uuid as uuid_mod

    from sqlalchemy import select

    from app.db import get_session
    from app.db.models import ConversationSession
    from app.db.models_persona import PersonaSwitchLog

    async with get_session() as db:
        result = await db.execute(
            select(ConversationSession).where(ConversationSession.client_key == session_client_key)
        )
        session_row = result.scalar_one_or_none()
        if session_row is None:
            return  # 全新 session，還沒有任何 message，交給 append 建立

        new_persona_id = uuid_mod.UUID(persona.id)
        if session_row.persona_id == new_persona_id:
            return  # 沒有切換

        db.add(
            PersonaSwitchLog(
                session_id=session_row.id,
                from_persona_id=session_row.persona_id,
                to_persona_id=new_persona_id,
                trigger_reason="resolver_reassignment",
            )
        )
        session_row.persona_id = new_persona_id
        await db.commit()
