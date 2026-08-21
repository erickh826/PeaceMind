"""
Post-processing（Phase 2）—— Profile 動態更新 + 主題演化
對應 docs/Phase2_implement_plan_Antigravity.md（已修正版）。

刻意同步執行，不是 FastAPI BackgroundTasks：這個專案部署在 Vercel，
`api/index.py` 是單純的 ASGI passthrough，沒有 `waitUntil` 這類機制——
HTTP 回應送出後執行環境幾乎立刻會被凍結/回收，BackgroundTasks 排程的工作
不保證真的執行完（本機長駐的 uvicorn 測試會看起來正常，Vercel 上可能悄悄失效）。

為了把「多一次同步 LLM 呼叫」的延遲代價降到最低，profile 抽取與主題分類
合併成同一次 LLM 呼叫（而不是原設計的兩次），由 `process_post_chat_updates()`
在 /chat 回傳回應之前直接 `await`。任何分析失敗都只記 log、不拋出，
確保這裡的錯誤永遠不會讓使用者看到的聊天回應失敗。
"""
from __future__ import annotations

import json
import logging
import os

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.clinical_topics import STANDARD_CLINICAL_TOPICS
from app.core.llm_client import get_azure_client
from app.db import get_session
from app.db.models import ConversationSession
from app.db.models_profile import ProfileChangeLog, ProfileTopic, UserProfile

logger = logging.getLogger(__name__)

VALID_RISK_LEVELS = {"none", "low", "medium", "high"}


async def process_post_chat_updates(session_client_key: str, user_msg: str, assistant_reply: str) -> None:
    """
    在 /chat 的回應送出「之前」同步呼叫（見檔案頂端說明，不是背景任務）。
    DATABASE_URL 未設定時整個 Persona/Profile 系統本來就不會被呼叫到這裡，
    但仍加一層防呆，避免任何情況下拖垮主要對話流程。
    """
    if not os.environ.get("DATABASE_URL"):
        return

    try:
        async with get_session() as db:
            session_row = await db.scalar(
                select(ConversationSession).where(ConversationSession.client_key == session_client_key)
            )
            if not session_row:
                return
            await analyze_and_update_profile(db, session_row.user_id, session_row.id, user_msg, assistant_reply)
    except Exception as e:
        logger.error("Failed post-chat profile/topic update: %s", str(e))


async def analyze_and_update_profile(
    db: AsyncSession, user_id, session_id, user_msg: str, assistant_reply: str
) -> None:
    """單一合併 LLM 呼叫：同時做 profile 抽取（含更正偵測）與主題分類。"""
    profile = await db.get(UserProfile, user_id)
    if not profile:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
        await db.flush()

    topics_list = ", ".join(STANDARD_CLINICAL_TOPICS)
    prompt = f"""Analyze the conversation turn and output a single JSON object covering three tasks:
1. Profile extraction — detect display name, year of study, and any corrections to
   previously known attributes.
2. Topic classification — classify the user's message into standard clinical concern topics.
   Only include topics that are clearly present; return an empty list if none apply.
3. Risk-level maintenance — conservatively assess the student's current clinical risk level.
   Use only: none, low, medium, high, or null when there is no clear update.
   Do not infer "high" from ordinary stress, sadness, or academic pressure alone.

Current User Profile: {profile.profile_json}
Current Name: {profile.display_name}
Current Year of Study: {profile.year_of_study}
Current Risk Level: {profile.risk_level}
Standard Topics: {topics_list}

Turn:
User: {user_msg}
Assistant: {assistant_reply}

Return JSON format:
{{
  "display_name": "extracted_name or null",
  "year_of_study": "extracted_year_of_study or null",
  "risk_level": "none|low|medium|high|null",
  "profile_updates": {{"hobby": "value"}},
  "corrections": [{{"field": "display_name", "old_value": "...", "new_value": "..."}}],
  "topics": ["Topic1", "Topic2"]
}}"""

    data = await _analyze_turn_with_llm(prompt)

    # 1. Apply display name & year updates
    if data.get("display_name"):
        profile.display_name = data["display_name"]
    if data.get("year_of_study"):
        profile.year_of_study = data["year_of_study"]

    # 2. Merge details into profile_json
    if data.get("profile_updates"):
        merged = dict(profile.profile_json)
        merged.update(data["profile_updates"])
        profile.profile_json = merged

    # 3. Log corrections in ProfileChangeLog
    risk_was_user_corrected = False
    for corr in data.get("corrections", []):
        field_path = corr["field"]
        if field_path == "risk_level":
            corrected_risk = _normalize_risk_level(corr.get("new_value"))
            if corrected_risk is None:
                continue
            corr["old_value"] = profile.risk_level
            corr["new_value"] = corrected_risk
            profile.risk_level = corrected_risk
            risk_was_user_corrected = True
        db.add(
            ProfileChangeLog(
                user_id=user_id,
                field_path=field_path,
                old_value=corr.get("old_value"),
                new_value=corr.get("new_value"),
                source="user_correction",
                session_id=session_id,
            )
        )

    # 4. Risk-level maintenance — only accepted values are persisted
    inferred_risk = _normalize_risk_level(data.get("risk_level"))
    if (
        inferred_risk is not None
        and not risk_was_user_corrected
        and inferred_risk != profile.risk_level
    ):
        old_risk = profile.risk_level
        profile.risk_level = inferred_risk
        db.add(
            ProfileChangeLog(
                user_id=user_id,
                field_path="risk_level",
                old_value=old_risk,
                new_value=inferred_risk,
                source="system_inferred",
                session_id=session_id,
            )
        )

    # 5. Topic evolution — only recognized standard topics count
    for t in data.get("topics", []):
        if t not in STANDARD_CLINICAL_TOPICS:
            continue
        topic_row = await db.get(ProfileTopic, (user_id, t))
        if topic_row:
            topic_row.mention_count += 1
            topic_row.last_seen_at = func.now()
        else:
            db.add(ProfileTopic(user_id=user_id, topic=t, mention_count=1))

    # 單一 commit：profile + change log + topics 一起落地
    await db.commit()


async def _analyze_turn_with_llm(prompt: str) -> dict:
    """Run the blocking Azure SDK call in a thread so async routes do not block the event loop."""
    return await run_in_threadpool(_analyze_turn_with_llm_sync, prompt)


def _analyze_turn_with_llm_sync(prompt: str) -> dict:
    client = get_azure_client()
    res = client.chat.completions.create(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    return json.loads(res.choices[0].message.content or "{}")


def _normalize_risk_level(value) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized not in VALID_RISK_LEVELS:
        return None
    return normalized
