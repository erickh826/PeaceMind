"""Phase 6: crisis tiers (risk_events)

Revision ID: c4a7e91b5d20
Revises: 8b3f1d9a2c47
Create Date: 2026-10-23

新增 risk_events（Q10–Q14）。跟架構文件 DDL 的差異：

- session_id / message_id 為 nullable + ON DELETE SET NULL：/reset 刪除對話時風險紀錄保留
  （觸發片段、tier、時間、user_id），不會被 FK 擋住也不會被 CASCADE 刪掉。
- 多一欄 resolved_at。
- trigger risk_events_guard：擋 DELETE；UPDATE 只允許
  (a) session_id / message_id 被設成 NULL（FK 的 SET NULL 動作）、
  (b) 通知 / 轉介時間與處理紀錄「從空值補寫一次」。tier、片段、user_id、created_at 不可改，
  已處理的紀錄不能覆寫。

全部是新表，舊程式碼不受影響，可以先套用到正式環境再部署。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c4a7e91b5d20"
down_revision = "8b3f1d9a2c47"
branch_labels = None
depends_on = None

GUARD_FN = """
CREATE OR REPLACE FUNCTION risk_events_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'risk_events rows cannot be deleted';
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.trigger_snippet IS DISTINCT FROM OLD.trigger_snippet
       OR NEW.risk_tier IS DISTINCT FROM OLD.risk_tier
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'risk_events immutable columns cannot be modified';
    END IF;

    IF NEW.session_id IS DISTINCT FROM OLD.session_id AND NEW.session_id IS NOT NULL THEN
        RAISE EXCEPTION 'risk_events.session_id can only be set to NULL';
    END IF;
    IF NEW.message_id IS DISTINCT FROM OLD.message_id AND NEW.message_id IS NOT NULL THEN
        RAISE EXCEPTION 'risk_events.message_id can only be set to NULL';
    END IF;

    IF OLD.supervisor_notified_at IS NOT NULL
       AND NEW.supervisor_notified_at IS DISTINCT FROM OLD.supervisor_notified_at THEN
        RAISE EXCEPTION 'risk_events.supervisor_notified_at is write-once';
    END IF;
    IF OLD.escalated_to_human_at IS NOT NULL
       AND NEW.escalated_to_human_at IS DISTINCT FROM OLD.escalated_to_human_at THEN
        RAISE EXCEPTION 'risk_events.escalated_to_human_at is write-once';
    END IF;
    IF OLD.resolved_by IS NOT NULL
       AND (NEW.resolved_by IS DISTINCT FROM OLD.resolved_by
            OR NEW.resolution_note IS DISTINCT FROM OLD.resolution_note
            OR NEW.resolved_at IS DISTINCT FROM OLD.resolved_at) THEN
        RAISE EXCEPTION 'risk_events already resolved';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "risk_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "session_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "message_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
        ),
        sa.Column("trigger_snippet", sa.Text(), nullable=False),
        sa.Column("risk_tier", sa.SmallInteger(), nullable=False),
        sa.Column("supervisor_notified_at", sa.DateTime(timezone=True)),
        sa.Column("escalated_to_human_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("therapists.id")),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("risk_tier IN (1,2,3)", name="ck_risk_events_tier"),
    )
    op.create_index("ix_risk_events_user_created", "risk_events", ["user_id", "created_at"])
    op.create_index("ix_risk_events_session", "risk_events", ["session_id"])

    op.execute(GUARD_FN)
    op.execute(
        "CREATE TRIGGER risk_events_guard_trg BEFORE UPDATE OR DELETE ON risk_events "
        "FOR EACH ROW EXECUTE FUNCTION risk_events_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS risk_events_guard_trg ON risk_events")
    op.drop_table("risk_events")
    op.execute("DROP FUNCTION IF EXISTS risk_events_guard()")
