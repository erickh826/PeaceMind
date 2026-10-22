"""
Admin API — 治療師管理（Phase 5）

目前沒有治療師登入（Phase 8 才做），therapists 表只有一筆 placeholder（admin），雙人審核
需要至少兩位不同的治療師（且 reviewer 要是 senior_therapist / admin），所以先提供最小的
建立與列表端點。兩個端點都要 X-Admin-Key（email 屬於個資）。

這不是帳號系統：沒有密碼、沒有登入、也不驗證「呼叫者就是這位治療師」——審核等端點仍是
由呼叫端自報 therapist id，見 app/core/rule_review.py 的說明。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.admin_auth import require_admin_key

router = APIRouter()


class TherapistCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    role: Literal["therapist", "senior_therapist", "admin"] = "therapist"


class TherapistOut(BaseModel):
    id: str
    name: str
    email: str
    role: str
    created_at: datetime


def _out(row) -> TherapistOut:
    return TherapistOut(
        id=str(row.id), name=row.name, email=row.email, role=row.role, created_at=row.created_at
    )


def _require_db():
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL 未設定，治療師管理需要資料庫。")


@router.get("/therapists", response_model=list[TherapistOut], dependencies=[Depends(require_admin_key)])
async def list_therapists():
    _require_db()
    from app.db import get_session
    from app.db.models_persona import Therapist

    async with get_session() as db:
        rows = (await db.execute(select(Therapist).order_by(Therapist.created_at))).scalars().all()
        return [_out(r) for r in rows]


@router.post("/therapists", response_model=TherapistOut, dependencies=[Depends(require_admin_key)])
async def create_therapist(request: TherapistCreateRequest):
    _require_db()
    from app.db import get_session
    from app.db.models_persona import Therapist

    async with get_session() as db:
        row = Therapist(name=request.name, email=request.email.lower(), role=request.role)
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail="這個 email 已經存在")
        await db.refresh(row)
        return _out(row)
