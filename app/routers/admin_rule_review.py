"""
Admin API — 規則沙盒、審核工作流、回滾（Phase 5.1 / 5.3 / 5.4，T-Q6–T-Q8）

設計決策與限制見 docs/CLINICAL_FRAMEWORK_TASKS.md Phase 5「開工前的設計決策」。重點：

- 所有端點（含讀取測試紀錄）都要 X-Admin-Key：沙盒紀錄含治療師輸入與模型輸出；
  審核申請列表跟規則列表一樣不含學生資料，維持公開讀取。
- submitted_by / reviewer_id / changed_by 由呼叫端自報（目前只有共用 admin key）。系統驗證
  治療師存在、reviewer 的角色與 reviewer ≠ submitter（僅 dual 模式），只能擋誤操作、擋不了
  冒名，真正的保證等 Phase 8 的帳號系統。
- 審核申請帶完整的「提議快照」，核准時才在同一筆 transaction 套用並寫入新版本；審核期間
  舊版本照常運作。申請記下 base_version_id，核准時規則已不是當初的版本就判定 stale。
- 所有會改規則的操作先 SELECT … FOR NO KEY UPDATE 鎖住規則（同 PATCH，見 admin_rules.py），
  同一規則的審核、修改、回滾彼此排隊。
- 回滾不經審核、立即生效：新版本的 scope 強制為 immediate，否則已在使用壞版本的 session
  不會切換（版本選擇規則見 app/core/rule_engine.py）。
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
from app.core.rule_review import REVIEWER_ROLES, content_hash, content_of, get_review_mode
from app.core.rule_sandbox import run_sandbox, simulate_profile_match
from app.routers.admin_rules import (
    RuleOut, RuleScope, _check_references, _parse_uuid, _require_db, _to_out,
    _write_version, supersede_pending_requests, validate_action_json,
)

router = APIRouter()

DEFAULT_SANDBOX_MAX_RUNS_PER_HOUR = 30


# ── 共用 ───────────────────────────────────────────────────────────────────

class ConfigOverlay(BaseModel):
    """疊在規則目前內容上的提議；沒帶的欄位沿用規則目前的值。"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    conditions_json: dict | None = None
    action_json: dict | None = None
    priority: int | None = None
    scope: RuleScope | None = None

    @field_validator("conditions_json")
    @classmethod
    def _check_conditions(cls, value: dict | None) -> dict | None:
        return None if value is None else validate_condition_json(value, mode="rule")

    @field_validator("action_json")
    @classmethod
    def _check_action(cls, value: dict | None) -> dict | None:
        return None if value is None else validate_action_json(value)


def _effective_content(rule, overlay: ConfigOverlay | None) -> dict:
    content = content_of(rule.snapshot())
    if overlay is not None:
        content.update(overlay.model_dump(exclude_none=True))
    return content


async def _lock_rule(db, rule_id: uuid.UUID):
    from app.db.models_rule import Rule

    rule = await db.scalar(select(Rule).where(Rule.id == rule_id).with_for_update(key_share=True))
    if rule is None:
        raise HTTPException(status_code=404, detail="規則不存在")
    return rule


async def _latest_version(db, rule_id: uuid.UUID):
    from app.db.models_rule import RuleVersion

    return await db.scalar(
        select(RuleVersion)
        .where(RuleVersion.rule_id == rule_id)
        .order_by(RuleVersion.version_number.desc())
        .limit(1)
    )


async def _get_therapist(db, therapist_id: str, field_name: str):
    from app.db.models_persona import Therapist

    row = await db.get(Therapist, _parse_uuid(therapist_id, field_name))
    if row is None:
        raise HTTPException(status_code=400, detail=f"{field_name} 對應的治療師不存在")
    return row


def _require_reviewer(reviewer, submitted_by: uuid.UUID) -> None:
    """dual 模式：reviewer 必須是 senior_therapist / admin，且不是送審者。single 模式不限制。"""
    if get_review_mode() != "dual":
        return
    if reviewer.role not in REVIEWER_ROLES:
        raise HTTPException(status_code=403, detail=f"審核者角色必須是 {' / '.join(REVIEWER_ROLES)}")
    if reviewer.id == submitted_by:
        raise HTTPException(status_code=403, detail="審核者不能是送審者本人")


# ── 5.1 沙盒 ────────────────────────────────────────────────────────────────

class TurnIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=1600)


class SimulatedProfile(BaseModel):
    year_of_study: str | None = None
    risk_level: Literal["none", "low", "medium", "high"] = "none"
    topic_counts: dict[str, int] = Field(default_factory=dict)

    @field_validator("topic_counts")
    @classmethod
    def _non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("topic_counts 不可為負數")
        return value


class RuleTestRequest(BaseModel):
    config: ConfigOverlay | None = None
    # 單句就是一則 user；多輪最後一則必須是 user，前面的當作對話歷史
    turns: list[TurnIn] = Field(..., min_length=1, max_length=10)
    simulated_profile: SimulatedProfile | None = None
    tested_by: str | None = None

    @field_validator("turns")
    @classmethod
    def _last_is_user(cls, value: list[TurnIn]) -> list[TurnIn]:
        if value[-1].role != "user":
            raise ValueError("turns 的最後一則必須是 user")
        return value


class RuleTestOut(BaseModel):
    id: str
    rule_version_id: str | None
    snapshot_hash: str
    generated_output: str
    safety_outcome: str
    persona_name: str | None = None
    example_ids: list[str] = []
    profile_match: bool | None = None
    profile_match_detail: dict[str, bool] | None = None
    tested_at: datetime


class RuleTestRunOut(BaseModel):
    id: str
    rule_version_id: str | None
    snapshot_hash: str
    draft_config_json: dict
    turns_json: list
    generated_output: str
    safety_outcome: str
    simulated_profile_json: dict | None
    profile_match: bool | None
    tested_by: str
    tested_at: datetime


def _max_runs_per_hour() -> int:
    try:
        return int(os.environ.get("SANDBOX_MAX_RUNS_PER_HOUR", DEFAULT_SANDBOX_MAX_RUNS_PER_HOUR))
    except ValueError:
        return DEFAULT_SANDBOX_MAX_RUNS_PER_HOUR


@router.post(
    "/rules/{rule_id}/test", response_model=RuleTestOut, dependencies=[Depends(require_admin_key)]
)
async def test_rule(rule_id: str, request: RuleTestRequest):
    """沙盒：強制套用規則（或提議內容）的 action，不寫 messages / sessions / profile。"""
    _require_db()
    from datetime import timedelta, timezone

    from app.db import get_session
    from app.db.models_review import RuleTestRun
    from app.db.models_rule import Rule

    rule_uuid = _parse_uuid(rule_id, "rule_id")
    async with get_session() as db:
        rule = await db.get(Rule, rule_uuid)
        if rule is None:
            raise HTTPException(status_code=404, detail="規則不存在")
        tester_uuid = await _tester_id(db, request.tested_by)

        # 每次測試都會呼叫真的 LLM：用全站共用的簡單上限擋掉亂打（共用 admin key，無法分人）
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        recent = await db.scalar(
            select(func.count()).select_from(RuleTestRun).where(RuleTestRun.tested_at >= since)
        )
        if recent >= _max_runs_per_hour():
            raise HTTPException(status_code=429, detail="沙盒測試次數已達每小時上限，請稍後再試")

        content = _effective_content(rule, request.config)
        # 測試 draft 時引用的 persona / 範例可能還沒 active，只檢查存在
        await _check_references(db, content["action_json"], activating=False)
        version = await _latest_version(db, rule.id)

        turns = [t.model_dump() for t in request.turns]
        try:
            result = await run_sandbox(db, content, turns)
        except Exception:
            # 不記錄對話內容；logger.exception 只含 traceback
            import logging

            logging.getLogger(__name__).exception("sandbox LLM call failed | rule=%s", rule.id)
            raise HTTPException(status_code=503, detail="AI 服務暫時無法使用，請稍後再試。")

        profile = request.simulated_profile
        matched, detail = (None, None)
        if profile is not None:
            matched, detail = simulate_profile_match(content["conditions_json"], profile.model_dump())

        run = RuleTestRun(
            rule_id=rule.id, rule_version_id=version.id if version else None,
            snapshot_hash=content_hash(content), draft_config_json=content,
            turns_json=turns, sample_input=turns[-1]["content"],
            generated_output=result.output, safety_outcome=result.safety_outcome,
            simulated_profile_json=profile.model_dump() if profile else None,
            profile_match=matched, tested_by=tester_uuid,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return RuleTestOut(
            id=str(run.id), rule_version_id=str(run.rule_version_id) if run.rule_version_id else None,
            snapshot_hash=run.snapshot_hash, generated_output=run.generated_output,
            safety_outcome=run.safety_outcome, persona_name=result.persona_name,
            example_ids=result.example_ids, profile_match=matched, profile_match_detail=detail,
            tested_at=run.tested_at,
        )


async def _tester_id(db, tested_by: str | None) -> uuid.UUID:
    from app.routers.admin_rules import _require_therapist

    return await _require_therapist(db, tested_by, "tested_by")


@router.get(
    "/rules/{rule_id}/test-runs", response_model=list[RuleTestRunOut],
    dependencies=[Depends(require_admin_key)],
)
async def list_test_runs(rule_id: str, limit: int = 50):
    _require_db()
    from app.db import get_session
    from app.db.models_review import RuleTestRun
    from app.db.models_rule import Rule

    rule_uuid = _parse_uuid(rule_id, "rule_id")
    async with get_session() as db:
        if await db.get(Rule, rule_uuid) is None:
            raise HTTPException(status_code=404, detail="規則不存在")
        rows = (
            await db.execute(
                select(RuleTestRun)
                .where(RuleTestRun.rule_id == rule_uuid)
                .order_by(RuleTestRun.tested_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars().all()
        return [
            RuleTestRunOut(
                id=str(r.id), rule_version_id=str(r.rule_version_id) if r.rule_version_id else None,
                snapshot_hash=r.snapshot_hash, draft_config_json=r.draft_config_json,
                turns_json=r.turns_json, generated_output=r.generated_output,
                safety_outcome=r.safety_outcome, simulated_profile_json=r.simulated_profile_json,
                profile_match=r.profile_match, tested_by=str(r.tested_by), tested_at=r.tested_at,
            )
            for r in rows
        ]


# ── 5.3 審核工作流 ──────────────────────────────────────────────────────────

class ReviewRequestOut(BaseModel):
    id: str
    rule_id: str
    request_type: str
    status: str
    submitted_by: str
    reviewer_id: str | None
    base_version_id: str
    proposed_snapshot_json: dict
    proposed_snapshot_hash: str
    submit_note: str | None
    review_note: str | None
    submitted_at: datetime
    reviewed_at: datetime | None


def _request_out(r) -> ReviewRequestOut:
    return ReviewRequestOut(
        id=str(r.id), rule_id=str(r.rule_id), request_type=r.request_type, status=r.status,
        submitted_by=str(r.submitted_by), reviewer_id=str(r.reviewer_id) if r.reviewer_id else None,
        base_version_id=str(r.base_version_id), proposed_snapshot_json=r.proposed_snapshot_json,
        proposed_snapshot_hash=r.proposed_snapshot_hash, submit_note=r.submit_note,
        review_note=r.review_note, submitted_at=r.submitted_at, reviewed_at=r.reviewed_at,
    )


class SubmitReviewRequest(BaseModel):
    submitted_by: str
    # 沒帶就是送審規則目前的內容（draft → 上線）；active 規則要帶要修改的內容
    proposed: ConfigOverlay | None = None
    note: str | None = None


class DecisionRequest(BaseModel):
    reviewer_id: str
    note: str | None = None


class RejectRequest(BaseModel):
    reviewer_id: str
    note: str = Field(..., min_length=1)


class CancelRequest(BaseModel):
    cancelled_by: str


@router.post(
    "/rules/{rule_id}/submit-review", response_model=ReviewRequestOut,
    dependencies=[Depends(require_admin_key)],
)
async def submit_review(rule_id: str, request: SubmitReviewRequest):
    _require_db()
    from app.db import get_session
    from app.db.models_review import RuleReviewRequest, RuleTestRun

    rule_uuid = _parse_uuid(rule_id, "rule_id")
    async with get_session() as db:
        rule = await _lock_rule(db, rule_uuid)
        submitter = await _get_therapist(db, request.submitted_by, "submitted_by")

        if rule.status == "in_review":
            raise HTTPException(status_code=409, detail="規則已在審核中")
        if rule.status == "archived":
            raise HTTPException(status_code=409, detail="已封存的規則不能送審")
        pending = await db.scalar(
            select(RuleReviewRequest.id).where(
                RuleReviewRequest.rule_id == rule.id, RuleReviewRequest.status == "pending"
            )
        )
        if pending is not None:
            raise HTTPException(status_code=409, detail="這條規則已經有待審的申請")

        content = _effective_content(rule, request.proposed)
        if rule.status == "active" and content == content_of(rule.snapshot()):
            raise HTTPException(status_code=422, detail="提議的內容跟目前 active 的內容相同，沒有要審核的修改")
        await _check_references(db, content["action_json"], activating=True)

        proposed_hash = content_hash(content)
        if get_review_mode() == "dual":
            tested = await db.scalar(
                select(RuleTestRun.id)
                .where(RuleTestRun.rule_id == rule.id, RuleTestRun.snapshot_hash == proposed_hash)
                .limit(1)
            )
            if tested is None:
                raise HTTPException(
                    status_code=409,
                    detail="雙人審核模式下，送審前必須先用沙盒（POST /rules/{id}/test）測過這份提議的內容",
                )

        request_type = "modify" if rule.status == "active" else "activate"
        if rule.status == "draft":
            rule.status = "in_review"
            rule.updated_at = func.now()
            await _write_version(db, rule, submitter.id, "送審")
            await db.flush()
        base = await _latest_version(db, rule.id)

        row = RuleReviewRequest(
            rule_id=rule.id, request_type=request_type, base_version_id=base.id,
            proposed_snapshot_json={**content, "status": "active"},
            proposed_snapshot_hash=proposed_hash, submitted_by=submitter.id,
            submit_note=request.note,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return _request_out(row)


@router.get("/review-requests", response_model=list[ReviewRequestOut])
async def list_review_requests(
    status: Literal["pending", "approved", "rejected", "cancelled", "stale"] | None = None,
    rule_id: str | None = None,
):
    _require_db()
    from app.db import get_session
    from app.db.models_review import RuleReviewRequest

    async with get_session() as db:
        stmt = select(RuleReviewRequest).order_by(RuleReviewRequest.submitted_at.desc())
        if status is not None:
            stmt = stmt.where(RuleReviewRequest.status == status)
        if rule_id is not None:
            stmt = stmt.where(RuleReviewRequest.rule_id == _parse_uuid(rule_id, "rule_id"))
        return [_request_out(r) for r in (await db.execute(stmt)).scalars().all()]


async def _load_pending_request(db, request_id: str):
    """先找出申請所屬的規則並鎖住規則，再重新讀取申請，避免跟其他操作交錯。"""
    from app.db.models_review import RuleReviewRequest

    req_uuid = _parse_uuid(request_id, "request_id")
    req = await db.get(RuleReviewRequest, req_uuid)
    if req is None:
        raise HTTPException(status_code=404, detail="審核申請不存在")
    rule = await _lock_rule(db, req.rule_id)
    await db.refresh(req)
    if req.status != "pending":
        raise HTTPException(status_code=409, detail=f"這筆申請已是 {req.status}，不能再處理")
    return req, rule


async def _restore_draft_if_in_review(db, rule, changed_by: uuid.UUID, note: str) -> None:
    if rule.status == "in_review":
        rule.status = "draft"
        rule.updated_at = func.now()
        await _write_version(db, rule, changed_by, note)


@router.post(
    "/review-requests/{request_id}/approve", response_model=RuleOut,
    dependencies=[Depends(require_admin_key)],
)
async def approve_review(request_id: str, request: DecisionRequest):
    _require_db()
    from app.db import get_session

    async with get_session() as db:
        req, rule = await _load_pending_request(db, request_id)
        reviewer = await _get_therapist(db, request.reviewer_id, "reviewer_id")
        _require_reviewer(reviewer, req.submitted_by)

        latest = await _latest_version(db, rule.id)
        if latest.id != req.base_version_id:
            req.status = "stale"
            req.review_note = "規則在送審後已變更，申請失效，請重新送審"
            req.reviewed_at = func.now()
            await db.commit()
            raise HTTPException(status_code=409, detail="規則在送審後已變更，申請已標為 stale，請重新送審")

        content = content_of(req.proposed_snapshot_json)
        await _check_references(db, content["action_json"], activating=True)

        for field_name, value in content.items():
            setattr(rule, field_name, value)
        rule.status = "active"
        rule.updated_at = func.now()
        version_number = await _write_version(
            db, rule, reviewer.id, f"審核通過：{request.note}" if request.note else "審核通過"
        )
        req.status = "approved"
        req.reviewer_id = reviewer.id
        req.review_note = request.note
        req.reviewed_at = func.now()
        await db.commit()
        await db.refresh(rule)
        return _to_out(rule, version_number)


@router.post(
    "/review-requests/{request_id}/reject", response_model=ReviewRequestOut,
    dependencies=[Depends(require_admin_key)],
)
async def reject_review(request_id: str, request: RejectRequest):
    """退回：draft 回到 draft（可修改後重送）、active 規則維持 active；理由必填並保留。"""
    _require_db()
    from app.db import get_session

    async with get_session() as db:
        req, rule = await _load_pending_request(db, request_id)
        reviewer = await _get_therapist(db, request.reviewer_id, "reviewer_id")
        _require_reviewer(reviewer, req.submitted_by)

        await _restore_draft_if_in_review(db, rule, reviewer.id, f"審核退回：{request.note}")
        req.status = "rejected"
        req.reviewer_id = reviewer.id
        req.review_note = request.note
        req.reviewed_at = func.now()
        await db.commit()
        await db.refresh(req)
        return _request_out(req)


@router.post(
    "/review-requests/{request_id}/cancel", response_model=ReviewRequestOut,
    dependencies=[Depends(require_admin_key)],
)
async def cancel_review(request_id: str, request: CancelRequest):
    """送審者撤回申請。"""
    _require_db()
    from app.db import get_session

    async with get_session() as db:
        req, rule = await _load_pending_request(db, request_id)
        actor = await _get_therapist(db, request.cancelled_by, "cancelled_by")
        if actor.id != req.submitted_by:
            raise HTTPException(status_code=403, detail="只有送審者本人能撤回申請")

        await _restore_draft_if_in_review(db, rule, actor.id, "撤回審核申請")
        req.status = "cancelled"
        req.review_note = "送審者撤回"
        req.reviewed_at = func.now()
        await db.commit()
        await db.refresh(req)
        return _request_out(req)


# ── 5.4 一鍵回滾 ────────────────────────────────────────────────────────────

class RollbackRequest(BaseModel):
    changed_by: str
    reason: str = Field(..., min_length=1)
    # 不帶就回到「最近一個內容不同、且當時是 active 的版本」
    target_version_number: int | None = Field(default=None, ge=1)


@router.post(
    "/rules/{rule_id}/rollback", response_model=RuleOut, dependencies=[Depends(require_admin_key)]
)
async def rollback_rule(rule_id: str, request: RollbackRequest):
    """
    緊急操作：不經審核、立即生效。把舊版本快照複製回 rules 並寫一個新版本，不覆寫歷史。
    只能回到當時是 active 的版本；引用的 persona / 範例已不是 active 時擋下並回報原因。
    """
    _require_db()
    from app.db import get_session
    from app.db.models_rule import RuleVersion

    rule_uuid = _parse_uuid(rule_id, "rule_id")
    async with get_session() as db:
        rule = await _lock_rule(db, rule_uuid)
        actor = await _get_therapist(db, request.changed_by, "changed_by")
        if rule.status == "archived":
            raise HTTPException(
                status_code=409, detail="已封存的規則不能回滾（回滾等於重新啟用，會繞過審核）"
            )

        versions = (
            await db.execute(
                select(RuleVersion)
                .where(RuleVersion.rule_id == rule.id)
                .order_by(RuleVersion.version_number.desc())
            )
        ).scalars().all()
        current_content = content_of(rule.snapshot())

        if request.target_version_number is not None:
            target = next((v for v in versions if v.version_number == request.target_version_number), None)
            if target is None:
                raise HTTPException(status_code=404, detail="指定的版本不存在")
            if target.snapshot_json.get("status") != "active":
                raise HTTPException(status_code=422, detail="只能回滾到當時是 active 的版本")
        else:
            target = next(
                (
                    v for v in versions
                    if v.snapshot_json.get("status") == "active"
                    and content_of(v.snapshot_json) != current_content
                ),
                None,
            )
            if target is None:
                raise HTTPException(status_code=404, detail="找不到可回滾的前一個 active 版本")

        target_content = content_of(target.snapshot_json)
        if rule.status == "active" and target_content == current_content:
            raise HTTPException(status_code=409, detail="該版本的內容跟目前相同，不需要回滾")

        try:
            await _check_references(db, target_content["action_json"], activating=True)
        except HTTPException as exc:
            raise HTTPException(status_code=409, detail=f"無法回滾到 v{target.version_number}：{exc.detail}")

        # 立即生效：已在用壞版本的 session 只會切到 scope=immediate 的新版本
        target_content["scope"] = "immediate"
        for field_name, value in target_content.items():
            setattr(rule, field_name, value)
        rule.status = "active"
        rule.updated_at = func.now()
        version_number = await _write_version(
            db, rule, actor.id, f"回滾到 v{target.version_number}：{request.reason}"
        )
        await supersede_pending_requests(db, rule.id, "stale", "規則已回滾，申請失效")
        await db.commit()
        await db.refresh(rule)
        return _to_out(rule, version_number)
