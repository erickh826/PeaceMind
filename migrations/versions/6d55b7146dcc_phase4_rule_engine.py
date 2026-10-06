"""Phase 4: rule engine

Revision ID: 6d55b7146dcc
Revises: 5c3e9a1f7d20
Create Date: 2026-10-06

新增 rules / rule_versions，對應 docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md 的 DDL
與 docs/CLINICAL_FRAMEWORK_TASKS.md Phase 4 的設計決策（T-Q1–T-Q5、T-Q8 基礎版）。

messages 的變更：
- rule_id：Phase 0 就留了欄位但沒有 FK，這裡補上（ON DELETE SET NULL）。
- rule_version_id：記錄該則回覆實際使用的規則版本（ON DELETE SET NULL）。
- output_replaced：L3 Output Gateway 替換回覆時為 true，統計規則成效時排除。
  NOT NULL + server_default false，Phase 3 以前的程式碼 INSERT 時不帶這個欄位也能寫入。

三個新欄位都不需要舊程式配合，所以這個 migration 可以先套用到正式環境、再部署
Phase 4 程式碼（merge 前的硬性前置條件，見任務文件）。

FK 一律 SET NULL 而不是 CASCADE：目前沒有硬刪除規則的 API，但萬一手動刪除，
不該連帶刪掉學生的對話紀錄。rule_versions 對 rules 是 CASCADE（版本是規則的一部分）。

跟 Phase 2/3 一樣手寫、不用 --autogenerate。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "6d55b7146dcc"
down_revision = "5c3e9a1f7d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("conditions_json", postgresql.JSONB(), nullable=False),
        sa.Column("action_json", postgresql.JSONB(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scope", sa.Text(), nullable=False, server_default="new_conversations_only"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("therapists.id"), nullable=False,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "scope IN ('new_conversations_only','immediate')", name="ck_rules_scope"
        ),
        sa.CheckConstraint(
            "status IN ('draft','in_review','active','archived')", name="ck_rules_status"
        ),
    )
    op.create_index("ix_rules_status", "rules", ["status"])

    op.create_table(
        "rule_versions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rule_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rules.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        # 完整快照：name / conditions_json / action_json / priority / scope / status
        sa.Column("snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "changed_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("therapists.id"), nullable=False,
        ),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("rule_id", "version_number", name="uq_rule_versions_rule_version"),
    )

    op.create_foreign_key(
        "fk_messages_rule_id", "messages", "rules",
        ["rule_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column(
        "messages",
        sa.Column(
            "rule_version_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rule_versions.id", ondelete="SET NULL", name="fk_messages_rule_version_id"),
            nullable=True,
        ),
    )
    op.add_column(
        "messages",
        sa.Column("output_replaced", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_messages_rule_id", "messages", ["rule_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_rule_id", table_name="messages")
    op.drop_column("messages", "output_replaced")
    op.drop_column("messages", "rule_version_id")
    op.drop_constraint("fk_messages_rule_id", "messages", type_="foreignkey")
    op.drop_table("rule_versions")
    op.drop_index("ix_rules_status", table_name="rules")
    op.drop_table("rules")
