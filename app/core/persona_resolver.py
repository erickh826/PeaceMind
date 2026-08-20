"""
Persona Resolver（Phase 1 + Phase 2 自動匹配）
決定某次對話要用哪個 persona，並記錄切換歷史（T-Q13/T-Q14）。

優先序（對應 CLINICAL_FRAMEWORK_ARCHITECTURE.md §3.1）：
  1. persona_assignments（治療師手動指派，T-Q15）—— 最高優先，覆寫自動匹配
  2. persona_match_conditions 自動匹配（T-Q12，Phase 2 起生效）—— 依 priority
     排序，比對 user_profiles.year_of_study + profile_topics（達門檻的演化主題）
  3. is_default=true 的系統預設 persona
  4. 都沒接 DB 時，回傳跟原本寫死內容一致的 fallback persona

DATABASE_URL 未設定時（本地快速開發 / 測試環境），直接回傳 FALLBACK_PERSONA，
確保沒有資料庫也能跑。

注意：手動指派目前透過 users.external_ref 對應 session_id（沿用 Phase 0
PostgresConversationStore 的匿名 user 綁定方式）。這只是暫時的身份識別，
之後導入真實帳號系統後應該要改成更明確的使用者識別。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from app.prompts.system_prompt import DEFAULT_PERSONA_FRAGMENT

# 主題累積達到這個次數才視為「已演化」的核心主題，用於自動匹配比對
# （跟 app/core/context_assembler.py 的門檻保持一致，注入 prompt 的主題跟
# 拿來自動匹配 persona 的主題是同一套定義）。
EVOLVED_TOPIC_THRESHOLD = 3


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
    from app.db.models_persona import Persona, PersonaAssignment, PersonaMatchCondition

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

        if persona_row is None and user_client_key:
            persona_row = await _match_persona_by_conditions(db, user_client_key)

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


async def _match_persona_by_conditions(db, user_client_key: str):
    """
    Phase 2.8：依 persona_match_conditions 自動匹配（T-Q12）。
    依 priority 由高到低比對，第一個命中的 persona 勝出；都沒命中回傳 None，
    交回 resolve_persona() 繼續往下 fallback 到系統預設 persona。
    """
    from sqlalchemy import select

    from app.db.models import User
    from app.db.models_persona import Persona, PersonaMatchCondition
    from app.db.models_profile import ProfileTopic, UserProfile

    user_row = await db.scalar(select(User).where(User.external_ref == user_client_key))
    if user_row is None:
        return None

    profile_row = await db.get(UserProfile, user_row.id)

    topic_result = await db.execute(
        select(ProfileTopic.topic).where(
            ProfileTopic.user_id == user_row.id,
            ProfileTopic.mention_count >= EVOLVED_TOPIC_THRESHOLD,
        )
    )
    evolved_topics = set(topic_result.scalars().all())

    result = await db.execute(
        select(PersonaMatchCondition, Persona)
        .join(Persona, Persona.id == PersonaMatchCondition.persona_id)
        .where(Persona.status == "active")
        .order_by(PersonaMatchCondition.priority.desc())
    )
    for condition_row, persona_row in result.all():
        if _condition_matches(condition_row.condition_json, profile_row, evolved_topics):
            return persona_row

    return None


def _condition_matches(condition_json: dict, profile_row, evolved_topics: set[str]) -> bool:
    """
    比對單一 persona_match_conditions.condition_json（例：
    {"year_of_study": "Year 1", "topics_include": ["社交焦慮"]}）。
    所有出現的鍵都必須成立（AND）；空字典或含未知鍵一律視為不匹配（安全預設，
    避免設定打錯字反而意外匹配到所有人）。
    """
    if not condition_json:
        return False

    for key, value in condition_json.items():
        if key == "year_of_study":
            if profile_row is None or profile_row.year_of_study != value:
                return False
        elif key == "topics_include":
            if not evolved_topics.intersection(value or []):
                return False
        else:
            return False  # 未知條件鍵，安全預設不匹配

    return True


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
