"""
Session Summarizer（Phase 2, Q4/Q5）
用 LLM 把一段對話整理成結構化摘要（事件 + 情緒 + 策略），供跨 session
記憶檢索使用（app/core/context_assembler.py）。

`end_session_and_summarize()` 是唯一對外入口，同時被 `/api/v1/reset` 與
`POST /api/v1/chat/end` 呼叫（見 app/routers/chat.py）——兩條路徑都可能
觸發到同一個 session，這裡用 `sessions.ended_at` 當作冪等旗標：已經處理過
的 session 直接 no-op，避免同一個 session 被生成兩筆 session_summaries
（見 docs/Phase2_implement_plan_Antigravity.md §2）。
"""
from __future__ import annotations

import json
import logging
import os

from sqlalchemy import func, select

from app.core.llm_client import get_azure_client
from app.db import get_session
from app.db.models import ConversationSession, Message
from app.db.models_profile import SessionSummary

logger = logging.getLogger(__name__)


async def end_session_and_summarize(session_client_key: str) -> None:
    """
    結束一個 session：若尚未結束過，用 LLM 生成摘要並寫入 session_summaries，
    然後標記 ended_at。已經結束過的 session 直接 no-op（冪等）。

    任何失敗都只記 log、不拋出 —— summarization 失敗不該讓 /reset 或
    /chat/end 本身失敗（使用者仍然應該能順利開始新對話）。
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
            if session_row.ended_at is not None:
                return  # 已經被另一條觸發路徑處理過，冪等 no-op

            messages_result = await db.execute(
                select(Message)
                .where(Message.session_id == session_row.id)
                .order_by(Message.created_at)
            )
            messages = messages_result.scalars().all()

            if messages:
                summary_data = await _generate_summary(messages)
                db.add(
                    SessionSummary(
                        session_id=session_row.id,
                        user_id=session_row.user_id,
                        summary_text=summary_data["summary_text"],
                        key_events=summary_data["key_events"],
                        emotions=summary_data["emotions"],
                        strategies_used=summary_data["strategies_used"],
                    )
                )

            session_row.ended_at = func.now()
            await db.commit()
    except Exception as e:
        logger.error("Failed to end/summarize session %s: %s", session_client_key, str(e))


async def _generate_summary(messages) -> dict:
    transcript = "\n".join(f"{m.role}: {m.content}" for m in messages)
    prompt = f"""Summarize this counseling chat session for clinical continuity. The next session
may reference this summary, so keep it factual and specific enough to be useful.

Transcript:
{transcript}

Return JSON format:
{{
  "summary_text": "concise clinical summary of what was discussed",
  "key_events": [{{"event": "...", "topic": "..."}}],
  "emotions": [{{"emotion": "...", "intensity": "low|medium|high"}}],
  "strategies_used": [{{"strategy": "...", "resource_ids": []}}]
}}"""

    client = get_azure_client()
    res = client.chat.completions.create(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    data = json.loads(res.choices[0].message.content or "{}")
    return {
        "summary_text": data.get("summary_text") or "(no summary generated)",
        "key_events": data.get("key_events", []),
        "emotions": data.get("emotions", []),
        "strategies_used": data.get("strategies_used", []),
    }
