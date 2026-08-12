"""
Admin API — Persona 管理（Phase 1）

⚠️ 目前沒有真正的治療師登入/權限機制（留給 Phase 8 跟 Admin Console 前端
一起做）。這裡的端點暫時要求呼叫端自己在 request body 帶 therapist_id，
不會驗證這個 id 是否真的有權限——僅供後端/測試驗證邏輯用，正式上線前
必須加上真實 auth，否則任何人都能呼叫這些端點。

需要 DATABASE_URL 已設定才能使用（沒接 DB 時，Persona 系統整體退回
resolve_persona() 內建的 fallback，這些端點會回 503）。
"""
from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

router = APIRouter()


def _require_db():
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL 未設定，Persona 管理功能需要資料庫。",
        )


class PersonaCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    tone: str = Field(..., min_length=1, max_length=100)
    response_length: str = Field(default="medium", pattern="^(short|medium|long)$")
    therapy_style: str | None = None
    system_prompt_fragment: str = Field(..., min_length=1)
    created_by: str | None = None  # therapist_id（暫時，見檔案頂端說明）


class PersonaOut(BaseModel):
    id: str
    name: str
    description: str | None
    tone: str
    response_length: str
    therapy_style: str | None
    status: str
    is_default: bool


class PersonaAssignRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=128)  # 對應 users.external_ref
    persona_id: str
    assigned_by: str  # therapist_id（暫時，見檔案頂端說明）
    note: str | None = None


@router.get("/personas", response_model=list[PersonaOut])
async def list_personas():
    _require_db()
    from app.db import get_session
    from app.db.models_persona import Persona

    async with get_session() as db:
        result = await db.execute(select(Persona))
        rows = result.scalars().all()
        return [
            PersonaOut(
                id=str(r.id), name=r.name, description=r.description, tone=r.tone,
                response_length=r.response_length, therapy_style=r.therapy_style,
                status=r.status, is_default=r.is_default,
            )
            for r in rows
        ]


@router.post("/personas", response_model=PersonaOut)
async def create_persona(request: PersonaCreateRequest):
    """新增一個 draft persona。需另外呼叫 /personas/{id}/activate 才會生效。"""
    _require_db()
    from app.db import get_session
    from app.db.models_persona import Persona

    async with get_session() as db:
        persona = Persona(
            name=request.name,
            description=request.description,
            tone=request.tone,
            response_length=request.response_length,
            therapy_style=request.therapy_style,
            system_prompt_fragment=request.system_prompt_fragment,
            status="draft",
            is_default=False,
            created_by=uuid.UUID(request.created_by) if request.created_by else None,
        )
        db.add(persona)
        await db.commit()
        await db.refresh(persona)
        return PersonaOut(
            id=str(persona.id), name=persona.name, description=persona.description,
            tone=persona.tone, response_length=persona.response_length,
            therapy_style=persona.therapy_style, status=persona.status,
            is_default=persona.is_default,
        )


@router.patch("/personas/{persona_id}/activate", response_model=PersonaOut)
async def activate_persona(persona_id: str):
    _require_db()
    from app.db import get_session
    from app.db.models_persona import Persona

    async with get_session() as db:
        persona = await db.get(Persona, uuid.UUID(persona_id))
        if persona is None:
            raise HTTPException(status_code=404, detail="Persona 不存在")
        persona.status = "active"
        await db.commit()
        await db.refresh(persona)
        return PersonaOut(
            id=str(persona.id), name=persona.name, description=persona.description,
            tone=persona.tone, response_length=persona.response_length,
            therapy_style=persona.therapy_style, status=persona.status,
            is_default=persona.is_default,
        )


@router.post("/personas/assign")
async def assign_persona(request: PersonaAssignRequest):
    """
    治療師手動指派 persona 給特定學生（T-Q15），覆寫自動匹配。
    session_id 對應該學生任一次對話用過的 session_id
    （目前透過 users.external_ref 綁定，見 Phase 0 PostgresConversationStore）。
    """
    _require_db()
    from app.db import get_session
    from app.db.models import User
    from app.db.models_persona import Persona, PersonaAssignment

    async with get_session() as db:
        user_result = await db.execute(select(User).where(User.external_ref == request.session_id))
        user_row = user_result.scalar_one_or_none()
        if user_row is None:
            raise HTTPException(
                status_code=404,
                detail="找不到對應的使用者，該 session_id 可能還沒有任何對話紀錄。",
            )

        persona = await db.get(Persona, uuid.UUID(request.persona_id))
        if persona is None:
            raise HTTPException(status_code=404, detail="Persona 不存在")

        existing = await db.get(PersonaAssignment, user_row.id)
        if existing is not None:
            existing.persona_id = persona.id
            existing.assigned_by = uuid.UUID(request.assigned_by)
            existing.note = request.note
        else:
            db.add(
                PersonaAssignment(
                    user_id=user_row.id,
                    persona_id=persona.id,
                    assigned_by=uuid.UUID(request.assigned_by),
                    note=request.note,
                )
            )
        await db.commit()
        return {"status": "assigned", "user_id": str(user_row.id), "persona_id": str(persona.id)}
