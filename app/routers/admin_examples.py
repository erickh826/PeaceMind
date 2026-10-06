"""
Admin API — 範例庫管理（Phase 3，T-Q16–T-Q20）

⚠️ 跟 admin_personas.py 一樣，目前沒有真正的治療師登入/權限機制（留給 Phase 8
跟 Admin Console 前端一起做）。created_by 暫時由呼叫端在 request body 帶入，
不帶則落到 migration cbda7ba4a1c9 種入的 placeholder therapist——不會驗證權限，
僅供後端/測試驗證邏輯用，正式上線前必須加上真實 auth。

Phase 4 起寫入端點（建立、修改）要求 X-Admin-Key（app/core/admin_auth.py）：規則會
引用範例，未授權者不該能改被引用的內容。這是 Phase 8 之前的最低限度保護。

需要 DATABASE_URL 已設定才能使用（沒接 DB 時 Example Selector 一律回傳空結果，
這些端點回 503）。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.core.admin_auth import require_admin_key

from app.core.condition_matching import validate_condition_json

router = APIRouter()

# migration cbda7ba4a1c9 種入的 placeholder therapist（response_examples.created_by 是 NOT NULL）
PLACEHOLDER_THERAPIST_ID = "00000000-0000-0000-0000-000000000001"

UsageMode = Literal["style_learning", "direct_quote"]
AttributionMode = Literal["anonymous", "attributed"]
ExampleStatus = Literal["active", "archived"]


def _require_db():
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL 未設定，範例庫管理功能需要資料庫。",
        )


def _parse_uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field_name} 不是合法的 UUID")


class ExampleCreateRequest(BaseModel):
    content: str = Field(..., min_length=1)
    # 例：{"year_of_study": "Year 1", "topics_include": ["Relationship"]}
    # 空條件、未知鍵、型別錯誤都會讓範例永遠不命中，建立時就擋掉（validate_condition_json）
    applicable_conditions_json: dict
    usage_mode: UsageMode = "style_learning"
    attribution_mode: AttributionMode = "anonymous"
    created_by: str | None = None  # therapist_id（暫時，見檔案頂端說明）

    @field_validator("applicable_conditions_json")
    @classmethod
    def _check_conditions(cls, value: dict) -> dict:
        return validate_condition_json(value)


class ExampleUpdateRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    applicable_conditions_json: dict | None = None
    usage_mode: UsageMode | None = None
    attribution_mode: AttributionMode | None = None
    status: ExampleStatus | None = None  # 封存用 "archived"，不提供硬刪除（保留 T-Q18 使用記錄）

    @field_validator("applicable_conditions_json")
    @classmethod
    def _check_conditions(cls, value: dict | None) -> dict | None:
        return None if value is None else validate_condition_json(value)


class ExampleOut(BaseModel):
    id: str
    content: str
    applicable_conditions_json: dict
    usage_mode: str
    attribution_mode: str
    status: str
    created_by: str
    created_at: datetime


def _to_out(row) -> ExampleOut:
    return ExampleOut(
        id=str(row.id), content=row.content,
        applicable_conditions_json=row.applicable_conditions_json,
        usage_mode=row.usage_mode, attribution_mode=row.attribution_mode,
        status=row.status, created_by=str(row.created_by), created_at=row.created_at,
    )


@router.get("/examples", response_model=list[ExampleOut])
async def list_examples(status: ExampleStatus | None = None):
    _require_db()
    from app.db import get_session
    from app.db.models_example import ResponseExample

    async with get_session() as db:
        stmt = select(ResponseExample).order_by(ResponseExample.created_at.desc())
        if status is not None:
            stmt = stmt.where(ResponseExample.status == status)
        result = await db.execute(stmt)
        return [_to_out(r) for r in result.scalars().all()]


@router.post("/examples", response_model=ExampleOut, dependencies=[Depends(require_admin_key)])
async def create_example(request: ExampleCreateRequest):
    """新增範例，建立後即為 active（範例只影響語氣/措辭參考，不需要 persona 那樣的 draft 流程）。"""
    _require_db()
    from app.db import get_session
    from app.db.models_example import ResponseExample
    from app.db.models_persona import Therapist

    created_by = _parse_uuid(request.created_by or PLACEHOLDER_THERAPIST_ID, "created_by")

    async with get_session() as db:
        if await db.get(Therapist, created_by) is None:
            raise HTTPException(status_code=400, detail="created_by 對應的治療師不存在")

        example = ResponseExample(
            content=request.content,
            applicable_conditions_json=request.applicable_conditions_json,
            usage_mode=request.usage_mode,
            attribution_mode=request.attribution_mode,
            status="active",
            created_by=created_by,
        )
        db.add(example)
        await db.commit()
        await db.refresh(example)
        return _to_out(example)


@router.patch("/examples/{example_id}", response_model=ExampleOut, dependencies=[Depends(require_admin_key)])
async def update_example(example_id: str, request: ExampleUpdateRequest):
    _require_db()
    from app.db import get_session
    from app.db.models_example import ResponseExample

    example_uuid = _parse_uuid(example_id, "example_id")
    updates = request.model_dump(exclude_none=True)

    async with get_session() as db:
        example = await db.get(ResponseExample, example_uuid)
        if example is None:
            raise HTTPException(status_code=404, detail="範例不存在")
        for field_name, value in updates.items():
            setattr(example, field_name, value)
        await db.commit()
        await db.refresh(example)
        return _to_out(example)
