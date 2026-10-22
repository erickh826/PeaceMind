"""
Admin API — 風險事件（Phase 6，Q10–Q14）

所有端點都要 X-Admin-Key：回傳內容含學生的觸發片段。
只提供讀取與「處理」：沒有刪除、也沒有修改 tier 的端點（DB trigger 也會擋）。
resolve 只能補 resolved_by / resolution_note，已處理的事件不能覆寫（回 409）。
resolved_by 是呼叫端自報的 therapist id，不是登入身份（Phase 8 才有帳號系統）。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.core.admin_auth import require_admin_key

router = APIRouter(dependencies=[Depends(require_admin_key)])


class RiskEventOut(BaseModel):
    id: str
    user_id: str
    session_id: str | None
    message_id: str | None
    trigger_snippet: str
    risk_tier: int
    supervisor_notified_at: datetime | None
    escalated_to_human_at: datetime | None
    resolved_by: str | None
    resolution_note: str | None
    resolved_at: datetime | None
    created_at: datetime


class ResolveRequest(BaseModel):
    resolved_by: uuid.UUID
    resolution_note: str = Field(..., min_length=1, max_length=2000)


def _require_db():
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL 未設定，風險事件需要資料庫。")


def _out(r) -> RiskEventOut:
    s = lambda v: str(v) if v is not None else None  # noqa: E731
    return RiskEventOut(
        id=str(r.id), user_id=str(r.user_id), session_id=s(r.session_id), message_id=s(r.message_id),
        trigger_snippet=r.trigger_snippet, risk_tier=r.risk_tier,
        supervisor_notified_at=r.supervisor_notified_at, escalated_to_human_at=r.escalated_to_human_at,
        resolved_by=s(r.resolved_by), resolution_note=r.resolution_note, resolved_at=r.resolved_at,
        created_at=r.created_at,
    )


@router.get("/risk-events", response_model=list[RiskEventOut])
async def list_risk_events(
    unresolved: bool = Query(False, description="只列出尚未處理的事件"),
    limit: int = Query(100, ge=1, le=500),
):
    _require_db()
    from app.db import get_session
    from app.db.models_risk import RiskEvent

    stmt = select(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(limit)
    if unresolved:
        stmt = stmt.where(RiskEvent.resolved_by.is_(None))
    async with get_session() as db:
        return [_out(r) for r in (await db.execute(stmt)).scalars().all()]


@router.get("/risk-events/users/{user_id}/timeline", response_model=list[RiskEventOut])
async def user_timeline(user_id: uuid.UUID):
    _require_db()
    from app.db import get_session
    from app.db.models_risk import RiskEvent

    stmt = select(RiskEvent).where(RiskEvent.user_id == user_id).order_by(RiskEvent.created_at)
    async with get_session() as db:
        return [_out(r) for r in (await db.execute(stmt)).scalars().all()]


@router.post("/risk-events/{event_id}/resolve", response_model=RiskEventOut)
async def resolve_risk_event(event_id: uuid.UUID, request: ResolveRequest):
    _require_db()
    from app.db import get_session
    from app.db.models_persona import Therapist
    from app.db.models_risk import RiskEvent

    async with get_session() as db:
        if await db.get(Therapist, request.resolved_by) is None:
            raise HTTPException(status_code=404, detail="找不到這位治療師")
        result = await db.execute(
            text(
                "UPDATE risk_events SET resolved_by = :by, resolution_note = :note, "
                "resolved_at = now() WHERE id = :id AND resolved_by IS NULL"
            ),
            {"by": request.resolved_by, "note": request.resolution_note, "id": event_id},
        )
        if result.rowcount == 0:
            exists = await db.get(RiskEvent, event_id)
            await db.rollback()
            if exists is None:
                raise HTTPException(status_code=404, detail="找不到這筆風險事件")
            raise HTTPException(status_code=409, detail="這筆事件已經處理過，不能覆寫")
        await db.commit()
        db.expire_all()
        return _out(await db.get(RiskEvent, event_id))
