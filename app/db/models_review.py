"""
ORM Models — Phase 5（Human-in-the-loop，T-Q6–T-Q10）

rule_test_runs：沙盒測試紀錄。snapshot_hash 是「被測內容」（name / conditions / action /
priority / scope）的雜湊，雙人模式送審時用它確認「這份提議的內容有被測過」。

rule_review_requests：審核申請，帶一份完整的提議快照。核准時才套用到 rules 並寫入新版本，
審核期間舊版本照常運作。同一條規則同時只能有一筆 pending（partial unique index）。

manual_overrides / feedback_tags：事後標註，不改學生已看到的內容，也不改 LLM 看到的歷史。
FK 一律 ON DELETE CASCADE：學生執行「忘記我」或 /reset 刪除 session 時，從該對話衍生的
覆寫與標籤要一併消失，否則 FK 會擋住刪除，或讓學生對話的衍生資料留在訓練語料裡。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# FK 對象（therapists / messages / rules / rule_versions）要先註冊進 Base.metadata
from app.db import models, models_persona, models_rule  # noqa: F401

REVIEW_REQUEST_TYPES = ("activate", "modify")
REVIEW_REQUEST_STATUSES = ("pending", "approved", "rejected", "cancelled", "stale")


class RuleTestRun(Base):
    __tablename__ = "rule_test_runs"
    __table_args__ = (Index("ix_rule_test_runs_rule_hash", "rule_id", "snapshot_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=True
    )
    rule_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_versions.id", ondelete="SET NULL"), nullable=True
    )
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    draft_config_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    turns_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    sample_input: Mapped[str] = mapped_column(Text, nullable=False)
    generated_output: Mapped[str] = mapped_column(Text, nullable=False)
    # passed | input_blocked | crisis | semantic_blocked | multiturn_blocked | output_replaced
    safety_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    simulated_profile_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    profile_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=False
    )
    tested_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RuleReviewRequest(Base):
    __tablename__ = "rule_review_requests"
    __table_args__ = (
        CheckConstraint("request_type IN ('activate','modify')", name="ck_review_request_type"),
        CheckConstraint(
            "status IN ('pending','approved','rejected','cancelled','stale')",
            name="ck_review_request_status",
        ),
        Index(
            "uq_rule_review_pending", "rule_id", unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
    )
    request_type: Mapped[str] = mapped_column(Text, nullable=False)
    base_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_versions.id", ondelete="CASCADE"), nullable=False
    )
    proposed_snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    proposed_snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=False
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    submit_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ManualOverride(Base):
    __tablename__ = "manual_overrides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    original_reply: Mapped[str] = mapped_column(Text, nullable=False)
    overridden_reply: Mapped[str] = mapped_column(Text, nullable=False)
    overridden_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=False
    )
    used_for_training: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class FeedbackTag(Base):
    __tablename__ = "feedback_tags"
    __table_args__ = (
        UniqueConstraint("message_id", "tag", "tagged_by", name="uq_feedback_tags_message_tag_by"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    # 詞彙表在程式碼（app/routers/admin_oversight.py 的 FEEDBACK_TAGS），不在資料庫約束，
    # 改詞彙要走部署，避免統計失真
    tag: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    tagged_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
