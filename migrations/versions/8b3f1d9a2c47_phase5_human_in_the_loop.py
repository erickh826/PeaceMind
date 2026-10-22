"""Phase 5: human-in-the-loop

Revision ID: 8b3f1d9a2c47
Revises: 6d55b7146dcc
Create Date: 2026-10-22

新增 rule_test_runs / rule_review_requests / manual_overrides / feedback_tags，
對應 docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md 的 DDL，另加 Phase 5 設計決策需要的欄位：

- rule_test_runs：rule_version_id（測的是哪個版本）、snapshot_hash（被測內容的雜湊，
  雙人模式送審時比對）、turns_json / safety_outcome / simulated_profile_json / profile_match。
- rule_review_requests：request_type、base_version_id、proposed_snapshot_json / hash
  （核准時才套用）、submit_note；狀態多了 cancelled / stale；partial unique index 讓
  同一條規則同時只能有一筆 pending。
- feedback_tags：note；標籤詞彙表在程式碼，不加 CHECK。

manual_overrides / feedback_tags 對 messages 的 FK 用 ON DELETE CASCADE：學生 /reset 或
「忘記我」刪除 session 時，衍生資料要一併消失，否則 FK 會擋住刪除。

全部是新表，舊程式碼不受影響，可以先套用到正式環境再部署。跟 Phase 2–4 一樣手寫。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "8b3f1d9a2c47"
down_revision = "6d55b7146dcc"
branch_labels = None
depends_on = None


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", postgresql.UUID(as_uuid=True), primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    op.create_table(
        "rule_test_runs",
        _uuid_pk(),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rules.id", ondelete="CASCADE")),
        sa.Column(
            "rule_version_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rule_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("snapshot_hash", sa.Text(), nullable=False),
        sa.Column("draft_config_json", postgresql.JSONB(), nullable=False),
        sa.Column("turns_json", postgresql.JSONB(), nullable=False),
        sa.Column("sample_input", sa.Text(), nullable=False),
        sa.Column("generated_output", sa.Text(), nullable=False),
        sa.Column("safety_outcome", sa.Text(), nullable=False),
        sa.Column("simulated_profile_json", postgresql.JSONB()),
        sa.Column("profile_match", sa.Boolean()),
        sa.Column(
            "tested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("therapists.id"), nullable=False
        ),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_rule_test_runs_rule_hash", "rule_test_runs", ["rule_id", "snapshot_hash"])
    op.create_index("ix_rule_test_runs_tested_at", "rule_test_runs", ["tested_at"])

    op.create_table(
        "rule_review_requests",
        _uuid_pk(),
        sa.Column(
            "rule_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rules.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("request_type", sa.Text(), nullable=False),
        sa.Column(
            "base_version_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rule_versions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("proposed_snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("proposed_snapshot_hash", sa.Text(), nullable=False),
        sa.Column(
            "submitted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("therapists.id"), nullable=False
        ),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("therapists.id")),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("submit_note", sa.Text()),
        sa.Column("review_note", sa.Text()),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("request_type IN ('activate','modify')", name="ck_review_request_type"),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','cancelled','stale')",
            name="ck_review_request_status",
        ),
    )
    op.create_index(
        "uq_rule_review_pending", "rule_review_requests", ["rule_id"], unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "manual_overrides",
        _uuid_pk(),
        sa.Column(
            "message_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("original_reply", sa.Text(), nullable=False),
        sa.Column("overridden_reply", sa.Text(), nullable=False),
        sa.Column(
            "overridden_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("therapists.id"), nullable=False
        ),
        sa.Column("used_for_training", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_manual_overrides_message", "manual_overrides", ["message_id"])

    op.create_table(
        "feedback_tags",
        _uuid_pk(),
        sa.Column(
            "message_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column(
            "tagged_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("therapists.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("message_id", "tag", "tagged_by", name="uq_feedback_tags_message_tag_by"),
    )
    op.create_index("ix_feedback_tags_tag", "feedback_tags", ["tag"])


def downgrade() -> None:
    op.drop_table("feedback_tags")
    op.drop_table("manual_overrides")
    op.drop_index("uq_rule_review_pending", table_name="rule_review_requests")
    op.drop_table("rule_review_requests")
    op.drop_table("rule_test_runs")
