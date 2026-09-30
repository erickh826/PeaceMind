"""Phase 3: example library

Revision ID: 5c3e9a1f7d20
Revises: 1ade07baedf2
Create Date: 2026-09-30

新增 response_examples / example_usage_log，對應
docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md 的 DDL 與
docs/Phase3_Phase4_implement_plan_Antigravity.md §3（T-Q16–T-Q20）。

example_usage_log.message_id / session_id 用 nullable + ON DELETE SET NULL，
不是原設計的 NOT NULL（+ 隱含 NO ACTION）：/api/v1/reset 會刪除整個 session，
messages 連帶 CASCADE 刪除——NOT NULL 會讓 /reset 因外鍵限制直接失敗，
CASCADE 則會讓 T-Q18 的使用記錄跟著憑空消失。改記 user_id（CASCADE）讓記錄
在 message 被刪除後仍可回溯。

跟 Phase 2 一樣手寫、不用 --autogenerate（autogenerate 會連帶偵測到 Phase 0/1
遺留的 timestamp 型別與索引命名落差，那是跟本次無關的既有技術債）。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "5c3e9a1f7d20"
down_revision = "1ade07baedf2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "response_examples",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "applicable_conditions_json", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("usage_mode", sa.Text(), nullable=False, server_default="style_learning"),
        sa.Column("attribution_mode", sa.Text(), nullable=False, server_default="anonymous"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("therapists.id"), nullable=False,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "usage_mode IN ('style_learning','direct_quote')",
            name="ck_response_examples_usage_mode",
        ),
        sa.CheckConstraint(
            "attribution_mode IN ('anonymous','attributed')",
            name="ck_response_examples_attribution_mode",
        ),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_response_examples_status"),
    )

    op.create_table(
        "example_usage_log",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "example_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("response_examples.id", ondelete="CASCADE"), nullable=False,
        ),
        # SET NULL, not CASCADE / NOT NULL — 見檔案頂端說明。
        sa.Column(
            "message_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "session_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("effectiveness_feedback", sa.Text(), nullable=True),
    )
    op.create_index("ix_example_usage_log_example_id", "example_usage_log", ["example_id"])
    op.create_index("ix_example_usage_log_message_id", "example_usage_log", ["message_id"])
    op.create_index("ix_example_usage_log_session_id", "example_usage_log", ["session_id"])
    op.create_index("ix_example_usage_log_user_id", "example_usage_log", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_example_usage_log_user_id", table_name="example_usage_log")
    op.drop_index("ix_example_usage_log_session_id", table_name="example_usage_log")
    op.drop_index("ix_example_usage_log_message_id", table_name="example_usage_log")
    op.drop_index("ix_example_usage_log_example_id", table_name="example_usage_log")
    op.drop_table("example_usage_log")
    op.drop_table("response_examples")
