"""
Context Assembly Service（Phase 2）
對話開始前，載入該使用者的 Profile（含演化主題）與相關跨 Session 摘要，
組成要注入 system prompt 的文字區塊（見 app/prompts/system_prompt.py）。

DATABASE_URL 未設定，或這個 session_id 還沒有對應的 User 記錄時
（全新 session 的第一句話），回傳空 context，不阻擋對話。

已知取捨（未在本階段修正）：這裡對 users 表做了一次獨立查詢，跟
app/core/persona_resolver.py 各自查一次，沒有共用同一次查詢結果——
正確性無虞，只是多一次 DB round trip，之後若延遲成為問題可以再合併。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select

from app.db import get_session
from app.db.models import User
from app.db.models_profile import ProfileTopic, SessionSummary, UserProfile

# 主題累積達到這個次數才視為「已演化」的核心主題，注入 prompt
EVOLVED_TOPIC_THRESHOLD = 3

MEMORY_TRIGGER_KEYWORDS = [
    "上次", "之前", "上次講", "上次說", "前幾次",
    "last time", "previously", "before", "yesterday",
]


@dataclass(frozen=True)
class AssembledContext:
    profile_text: str | None
    past_summaries_text: str | None


EMPTY_CONTEXT = AssembledContext(None, None)


async def assemble_context(session_id: str | None, user_message: str) -> AssembledContext:
    if not os.environ.get("DATABASE_URL") or not session_id:
        return EMPTY_CONTEXT

    async with get_session() as db:
        user_row = await db.scalar(select(User).where(User.external_ref == session_id))
        if not user_row:
            # 全新 session，還沒有任何 User 記錄（PostgresConversationStore 尚未 append 過）
            return EMPTY_CONTEXT

        profile_row = await db.get(UserProfile, user_row.id)
        if not profile_row:
            # 首次對話：先不預先建立空 profile，交給 profile_service.py 在對話結束後
            # 依實際抽取結果建立——context assembly 只負責「讀」，不負責「寫」。
            profile_row = None

        topic_result = await db.execute(
            select(ProfileTopic)
            .where(ProfileTopic.user_id == user_row.id)
            .order_by(ProfileTopic.mention_count.desc())
        )
        evolved_topics = [
            t.topic for t in topic_result.scalars().all()
            if t.mention_count >= EVOLVED_TOPIC_THRESHOLD
        ]

        profile_text = _format_profile(profile_row, evolved_topics)

        has_trigger = any(kw in user_message for kw in MEMORY_TRIGGER_KEYWORDS)
        # 平時提供最近一則摘要維持連貫性；使用者明確提及「上次」時多撈幾則
        limit = 3 if has_trigger else 1

        summary_result = await db.execute(
            select(SessionSummary)
            .where(SessionSummary.user_id == user_row.id, SessionSummary.deleted_at.is_(None))
            .order_by(SessionSummary.created_at.desc())
            .limit(limit)
        )
        summaries = list(reversed(summary_result.scalars().all()))
        past_summaries_text = _format_summaries(summaries)

        return AssembledContext(profile_text, past_summaries_text)


def _format_profile(profile_row: UserProfile | None, evolved_topics: list[str]) -> str | None:
    if profile_row is None and not evolved_topics:
        return None

    lines = ["[STUDENT PROFILE]"]
    if profile_row is not None:
        lines.append(f"Display Name: {profile_row.display_name or 'Anonymous'}")
        lines.append(f"Year of Study: {profile_row.year_of_study or 'Unknown'}")
        lines.append(f"Clinical Risk Level: {profile_row.risk_level}")
    if evolved_topics:
        lines.append(f"Identified Core Topics: {', '.join(evolved_topics)}")
    if profile_row is not None and profile_row.profile_json:
        lines.append("Additional Background:")
        for k, v in profile_row.profile_json.items():
            lines.append(f"  - {k}: {v}")
    return "\n".join(lines)


def _format_summaries(summaries: list[SessionSummary]) -> str | None:
    if not summaries:
        return None

    lines = ["[RELEVANT PAST SESSIONS CONTEXT]"]
    for s in summaries:
        lines.append(
            f"- Session on {s.created_at.strftime('%Y-%m-%d')}: {s.summary_text}\n"
            f"  * Emotions: {s.emotions}\n"
            f"  * Core Events: {s.key_events}\n"
            f"  * Coping Strategies: {s.strategies_used}"
        )
    return "\n".join(lines)
