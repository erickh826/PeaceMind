"""
危機分層 Escalation Service（Phase 6，Q10–Q14）

只有 L1a 危機關鍵字命中才算一次觸發（LLM 推論的 risk_level、L3 替換回覆都不算）。
同一個 session 內：第 1 次 tier 1、第 2 次 tier 2、第 3 次以上 tier 3。每次觸發都寫一筆
risk_events；tier >= 2 會送出通知。

身份限制：前端每次載入頁面都是新的匿名 session / user，所以 tier 會因重新整理而歸零，
tier 3 的鎖定也可以被繞過；真正的解法等 Phase 8 帳號系統。

這裡的函式都不丟例外：危機路徑是全系統唯一絕對不能回 500 的路徑，任何失敗只記 error log，
熱線回覆照常送出。
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text

from app.core.crisis_notifier import send_crisis_notification

logger = logging.getLogger(__name__)

SNIPPET_MAX_CHARS = 300
LOCK_TIER = 3


@dataclass
class CrisisOutcome:
    tier: int = 1
    event_id: str | None = None
    notified: bool = False


def _db_enabled() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


async def session_risk_count(session_id: str | None) -> int:
    """這個 session 目前有幾筆風險事件；沒有資料庫或查詢失敗時回傳 0。"""
    if not session_id or not _db_enabled():
        return 0
    try:
        from app.db import get_session
        from app.db.models import ConversationSession
        from app.db.models_risk import RiskEvent

        async with get_session() as db:
            sid = await db.scalar(
                select(ConversationSession.id).where(ConversationSession.client_key == session_id)
            )
            if sid is None:
                return 0
            return int(
                await db.scalar(
                    select(func.count()).select_from(RiskEvent).where(RiskEvent.session_id == sid)
                )
                or 0
            )
    except Exception as e:  # noqa: BLE001
        logger.error("risk count lookup failed: %s", e)
        return 0


async def record_crisis_event(
    session_id: str | None, user_message: str, message_id: str | None = None
) -> CrisisOutcome:
    """寫入 risk_events、決定 tier、必要時通知。任何失敗都吞掉並回傳目前已知的結果。"""
    outcome = CrisisOutcome()
    if not session_id or not _db_enabled():
        return outcome

    try:
        from app.db import get_session
        from app.db.models import ConversationSession
        from app.db.models_risk import RiskEvent

        async with get_session() as db:
            session_row = await db.scalar(
                select(ConversationSession).where(ConversationSession.client_key == session_id)
            )
            if session_row is None:
                logger.error("risk event skipped: session row missing")
                return outcome

            prior = int(
                await db.scalar(
                    select(func.count()).select_from(RiskEvent).where(
                        RiskEvent.session_id == session_row.id
                    )
                )
                or 0
            )
            outcome.tier = min(prior + 1, LOCK_TIER)

            event = RiskEvent(
                user_id=session_row.user_id,
                session_id=session_row.id,
                message_id=uuid.UUID(message_id) if message_id else None,
                trigger_snippet=user_message[:SNIPPET_MAX_CHARS],
                risk_tier=outcome.tier,
            )
            db.add(event)
            await db.flush()
            created_at = await db.scalar(
                select(RiskEvent.created_at).where(RiskEvent.id == event.id)
            )
            await db.commit()
            outcome.event_id = str(event.id)
    except Exception as e:  # noqa: BLE001
        logger.error("risk event write failed: %s", e)
        return outcome

    if outcome.tier >= 2:
        outcome.notified = await send_crisis_notification(
            outcome.event_id, outcome.tier, created_at or datetime.utcnow()
        )
        if outcome.notified:
            try:
                from app.db import get_session

                async with get_session() as db:
                    await db.execute(
                        text(
                            "UPDATE risk_events SET supervisor_notified_at = now() "
                            "WHERE id = :id AND supervisor_notified_at IS NULL"
                        ),
                        {"id": uuid.UUID(outcome.event_id)},
                    )
                    await db.commit()
            except Exception as e:  # noqa: BLE001
                logger.error("risk event notified_at update failed: %s", e)

    return outcome
