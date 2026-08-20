"""Phase 2: profiles and cross-session memory

Revision ID: 1ade07baedf2
Revises: cbda7ba4a1c9
Create Date: 2026-08-19

新增 user_profiles / profile_change_log / profile_topics / session_summaries，
對應 docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md §2.1 與
docs/Phase2_implement_plan_Antigravity.md 的資料模型設計（Q1-Q6/Q17）。

session_summaries.session_id 用 ON DELETE SET NULL，不是 CASCADE：
/api/v1/reset 會在寫入摘要後把 sessions 那筆刪掉，CASCADE 會讓摘要在
同一輪請求裡被自己連坐刪除，Q4-Q6 的跨 session 記憶就永遠留不住任何東西。
profile_change_log.session_id 比照同樣理由，也用 SET NULL。

此 migration 只新增這四張表，不動任何既有欄位/索引 —— `alembic revision
--autogenerate` 會連帶偵測到既有 timestamp 欄位型別（TIMESTAMPTZ vs
DateTime）跟索引命名的既有落差，那是 Phase 0/1 遺留、跟本次無關的技術債，
刻意不在這裡一起處理，避免這個 migration 意外改動不相關的既有欄位。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "1ade07baedf2"
down_revision = "cbda7ba4a1c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("year_of_study", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.Text(), nullable=False, server_default="none"),
        sa.Column("profile_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.CheckConstraint(
            "risk_level IN ('none','low','medium','high')", name="ck_user_profiles_risk"
        ),
    )

    op.create_table(
        "profile_topics",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("topic", sa.Text(), primary_key=True),
        sa.Column("mention_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "profile_change_log",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("field_path", sa.Text(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "session_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "source IN ('user_correction','system_inferred','therapist_edit')",
            name="ck_profile_change_source",
        ),
    )
    op.create_index("ix_profile_change_log_user_id", "profile_change_log", ["user_id"])
    op.create_index("ix_profile_change_log_session_id", "profile_change_log", ["session_id"])

    op.create_table(
        "session_summaries",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # SET NULL, not CASCADE — 見檔案頂端說明。
        sa.Column(
            "session_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("key_events", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("emotions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("strategies_used", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("retention_policy", sa.Text(), nullable=False, server_default="permanent"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "retention_policy IN ('permanent','90d','30d')", name="ck_session_summaries_retention"
        ),
    )
    op.create_index("ix_session_summaries_user_id", "session_summaries", ["user_id"])
    op.create_index("ix_session_summaries_session_id", "session_summaries", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_session_summaries_session_id", table_name="session_summaries")
    op.drop_index("ix_session_summaries_user_id", table_name="session_summaries")
    op.drop_table("session_summaries")
    op.drop_index("ix_profile_change_log_session_id", table_name="profile_change_log")
    op.drop_index("ix_profile_change_log_user_id", table_name="profile_change_log")
    op.drop_table("profile_change_log")
    op.drop_table("profile_topics")
    op.drop_table("user_profiles")
