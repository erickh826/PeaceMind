"""
ORM Models — Phase 4（Rule Engine，T-Q1–T-Q5、T-Q8 基礎版）

rules：目前狀態（最新一版）。conditions_json 由 condition_matching 的 rules 模式
驗證與比對；action_json 提供 persona_id / example_ids / therapy / tone。

rule_versions：每次變更（建立、修改、啟用、封存）在同一筆 transaction 寫一筆完整
快照（name / conditions_json / action_json / priority / scope / status）。
Rule Engine 依快照（不是 rules 目前的值）決定某個 session 適用哪個版本，規則見
app/core/rule_engine.py 的 applicable_version()。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# created_by / changed_by 對 therapists.id 有 FK，確保 Therapist 已註冊進 Base.metadata
from app.db import models, models_persona  # noqa: F401

RULE_SCOPES = ("new_conversations_only", "immediate")
RULE_STATUSES = ("draft", "in_review", "active", "archived")


class Rule(Base):
    __tablename__ = "rules"
    __table_args__ = (
        CheckConstraint("scope IN ('new_conversations_only','immediate')", name="ck_rules_scope"),
        CheckConstraint(
            "status IN ('draft','in_review','active','archived')", name="ck_rules_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    conditions_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="new_conversations_only")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "conditions_json": self.conditions_json,
            "action_json": self.action_json,
            "priority": self.priority,
            "scope": self.scope,
            "status": self.status,
        }


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (
        UniqueConstraint("rule_id", "version_number", name="uq_rule_versions_rule_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=False
    )
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
