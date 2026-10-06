"""
Admin API — 規則管理（Phase 4，T-Q1–T-Q5、T-Q8 基礎版）

- 每次變更（建立、修改、啟用、封存）都在同一筆 transaction 更新 rules 並寫一筆
  rule_versions 完整快照；version_number 依規則遞增。
- Phase 4 允許直接 draft → active（Phase 5 再加審核流程，in_review 保留給 Phase 5，
  這裡不接受）。啟用前驗證引用對象：persona 必須存在且 active、範例必須存在且 active。
- 寫入端點需要 X-Admin-Key（app/core/admin_auth.py）；真正的帳號與角色權限在 Phase 8。
- 不提供硬刪除，用 status: archived 封存（封存一律立即停用，不論 scope）。
- 回滾（Phase 5.4）尚未提供；rule_versions 已保存完整快照，之後可直接沿用。

created_by / changed_by 跟範例 API 一樣，不帶則落到 placeholder therapist。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.core.admin_auth import require_admin_key
from app.core.condition_matching import validate_condition_json

router = APIRouter()

PLACEHOLDER_THERAPIST_ID = "00000000-0000-0000-0000-000000000001"

RuleScope = Literal["new_conversations_only", "immediate"]
# in_review 保留給 Phase 5 審核流程，Phase 4 的 API 不接受
WritableStatus = Literal["draft", "active", "archived"]

_ACTION_KEYS = ("therapy", "tone", "persona_id", "example_ids")


def validate_action_json(action: dict) -> dict:
    """只檢查格式；引用對象是否存在、是否 active 在寫入時查資料庫（_check_references）。"""
    if not action:
        raise ValueError("action 不可為空（至少要有 therapy、tone、persona_id、example_ids 其中之一）")

    for key, value in action.items():
        if key in ("therapy", "tone"):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} 必須是非空字串")
        elif key == "persona_id":
            if not isinstance(value, str):
                raise ValueError("persona_id 必須是 UUID 字串")
            try:
                uuid.UUID(value)
            except ValueError:
                raise ValueError("persona_id 不是合法的 UUID")
        elif key == "example_ids":
            if not isinstance(value, list) or not value:
                raise ValueError("example_ids 必須是非空的 UUID 字串陣列")
            for item in value:
                if not isinstance(item, str):
                    raise ValueError(f"example_ids 含不合法的 UUID：{item}")
                try:
                    uuid.UUID(item)
                except ValueError:
                    raise ValueError(f"example_ids 含不合法的 UUID：{item}")
        else:
            raise ValueError(f"不支援的 action 鍵：{key}（只支援 {', '.join(_ACTION_KEYS)}）")

    return action


def _require_db():
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL 未設定，規則管理功能需要資料庫。")


def _parse_uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field_name} 不是合法的 UUID")


class RuleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    # 例：{"topics_include": ["Anxiety"], "risk_level": "low", "min_topic_mentions": 3}
    conditions_json: dict
    # 例：{"therapy": "ACT", "tone": "溫暖接納", "example_ids": ["..."]}
    action_json: dict
    priority: int = 0
    scope: RuleScope = "new_conversations_only"
    status: WritableStatus = "draft"
    created_by: str | None = None  # therapist_id（暫時，見範例 API 說明）
    change_note: str | None = None

    @field_validator("conditions_json")
    @classmethod
    def _check_conditions(cls, value: dict) -> dict:
        return validate_condition_json(value, mode="rule")

    @field_validator("action_json")
    @classmethod
    def _check_action(cls, value: dict) -> dict:
        return validate_action_json(value)


class RuleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    conditions_json: dict | None = None
    action_json: dict | None = None
    priority: int | None = None
    scope: RuleScope | None = None
    status: WritableStatus | None = None
    changed_by: str | None = None
    change_note: str | None = None

    @field_validator("conditions_json")
    @classmethod
    def _check_conditions(cls, value: dict | None) -> dict | None:
        return None if value is None else validate_condition_json(value, mode="rule")

    @field_validator("action_json")
    @classmethod
    def _check_action(cls, value: dict | None) -> dict | None:
        return None if value is None else validate_action_json(value)


class RuleOut(BaseModel):
    id: str
    name: str
    conditions_json: dict
    action_json: dict
    priority: int
    scope: str
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    version_number: int


class RuleVersionOut(BaseModel):
    id: str
    version_number: int
    snapshot_json: dict
    changed_by: str
    change_note: str | None
    created_at: datetime


def _to_out(row, version_number: int) -> RuleOut:
    return RuleOut(
        id=str(row.id), name=row.name, conditions_json=row.conditions_json,
        action_json=row.action_json, priority=row.priority, scope=row.scope,
        status=row.status, created_by=str(row.created_by), created_at=row.created_at,
        updated_at=row.updated_at, version_number=version_number,
    )


async def _require_therapist(db, therapist_id: str | None, field_name: str) -> uuid.UUID:
    from app.db.models_persona import Therapist

    therapist_uuid = _parse_uuid(therapist_id or PLACEHOLDER_THERAPIST_ID, field_name)
    if await db.get(Therapist, therapist_uuid) is None:
        raise HTTPException(status_code=400, detail=f"{field_name} 對應的治療師不存在")
    return therapist_uuid


async def _check_references(db, action: dict, activating: bool) -> None:
    """引用的 persona / 範例必須存在；要啟用時還必須是 active。"""
    from app.db.models_example import ResponseExample
    from app.db.models_persona import Persona

    if "persona_id" in action:
        persona = await db.get(Persona, uuid.UUID(action["persona_id"]))
        if persona is None:
            raise HTTPException(status_code=422, detail="action_json.persona_id 對應的 persona 不存在")
        if activating and persona.status != "active":
            raise HTTPException(status_code=422, detail="action_json.persona_id 對應的 persona 不是 active")

    if "example_ids" in action:
        ids = {uuid.UUID(i) for i in action["example_ids"]}
        rows = (
            await db.execute(select(ResponseExample).where(ResponseExample.id.in_(ids)))
        ).scalars().all()
        missing = ids - {r.id for r in rows}
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"action_json.example_ids 有不存在的範例：{sorted(str(i) for i in missing)}",
            )
        if activating:
            inactive = [str(r.id) for r in rows if r.status != "active"]
            if inactive:
                raise HTTPException(
                    status_code=422, detail=f"action_json.example_ids 有非 active 的範例：{inactive}"
                )


async def _write_version(db, rule, changed_by: uuid.UUID, change_note: str | None) -> int:
    from app.db.models_rule import RuleVersion

    current = await db.scalar(
        select(func.max(RuleVersion.version_number)).where(RuleVersion.rule_id == rule.id)
    )
    version_number = (current or 0) + 1
    db.add(
        RuleVersion(
            rule_id=rule.id, version_number=version_number, snapshot_json=rule.snapshot(),
            changed_by=changed_by, change_note=change_note,
        )
    )
    return version_number


async def _current_version_numbers(db, rule_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    from app.db.models_rule import RuleVersion

    if not rule_ids:
        return {}
    result = await db.execute(
        select(RuleVersion.rule_id, func.max(RuleVersion.version_number))
        .where(RuleVersion.rule_id.in_(rule_ids))
        .group_by(RuleVersion.rule_id)
    )
    return dict(result.all())


@router.get("/rules", response_model=list[RuleOut])
async def list_rules(status: Literal["draft", "in_review", "active", "archived"] | None = None):
    _require_db()
    from app.db import get_session
    from app.db.models_rule import Rule

    async with get_session() as db:
        stmt = select(Rule).order_by(Rule.priority.desc(), Rule.created_at.desc())
        if status is not None:
            stmt = stmt.where(Rule.status == status)
        rows = (await db.execute(stmt)).scalars().all()
        versions = await _current_version_numbers(db, [r.id for r in rows])
        return [_to_out(r, versions.get(r.id, 0)) for r in rows]


@router.get("/rules/{rule_id}/versions", response_model=list[RuleVersionOut])
async def list_rule_versions(rule_id: str):
    _require_db()
    from app.db import get_session
    from app.db.models_rule import Rule, RuleVersion

    rule_uuid = _parse_uuid(rule_id, "rule_id")
    async with get_session() as db:
        if await db.get(Rule, rule_uuid) is None:
            raise HTTPException(status_code=404, detail="規則不存在")
        rows = (
            await db.execute(
                select(RuleVersion)
                .where(RuleVersion.rule_id == rule_uuid)
                .order_by(RuleVersion.version_number.desc())
            )
        ).scalars().all()
        return [
            RuleVersionOut(
                id=str(r.id), version_number=r.version_number, snapshot_json=r.snapshot_json,
                changed_by=str(r.changed_by), change_note=r.change_note, created_at=r.created_at,
            )
            for r in rows
        ]


@router.post("/rules", response_model=RuleOut, dependencies=[Depends(require_admin_key)])
async def create_rule(request: RuleCreateRequest):
    _require_db()
    from app.db import get_session
    from app.db.models_rule import Rule

    async with get_session() as db:
        created_by = await _require_therapist(db, request.created_by, "created_by")
        await _check_references(db, request.action_json, activating=request.status == "active")

        rule = Rule(
            name=request.name, conditions_json=request.conditions_json,
            action_json=request.action_json, priority=request.priority,
            scope=request.scope, status=request.status, created_by=created_by,
        )
        db.add(rule)
        await db.flush()
        version_number = await _write_version(db, rule, created_by, request.change_note)
        await db.commit()
        await db.refresh(rule)
        return _to_out(rule, version_number)


@router.patch("/rules/{rule_id}", response_model=RuleOut, dependencies=[Depends(require_admin_key)])
async def update_rule(rule_id: str, request: RuleUpdateRequest):
    _require_db()
    from app.db import get_session
    from app.db.models_rule import Rule

    rule_uuid = _parse_uuid(rule_id, "rule_id")
    updates = request.model_dump(exclude_none=True, exclude={"changed_by", "change_note"})
    if not updates:
        raise HTTPException(status_code=422, detail="沒有任何要修改的欄位")

    async with get_session() as db:
        # FOR UPDATE：同一條規則的並行修改排隊進行，version_number 才不會撞號
        rule = await db.scalar(select(Rule).where(Rule.id == rule_uuid).with_for_update())
        if rule is None:
            raise HTTPException(status_code=404, detail="規則不存在")
        changed_by = await _require_therapist(db, request.changed_by, "changed_by")

        for field_name, value in updates.items():
            setattr(rule, field_name, value)
        # 修改 active 規則的 action，或把規則啟用時，都要確認引用對象可用
        await _check_references(db, rule.action_json, activating=rule.status == "active")
        rule.updated_at = func.now()

        version_number = await _write_version(db, rule, changed_by, request.change_note)
        await db.commit()
        await db.refresh(rule)
        return _to_out(rule, version_number)
