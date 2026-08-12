"""Phase 1: Persona system

Revision ID: 102d7d73bbd7
Revises: b6dcae4f4555
Create Date: 2026-08-12

新增 therapists / personas / persona_match_conditions / persona_assignments /
persona_switch_log，並把 Phase 0 留空的 sessions.persona_id / messages.persona_id
補上正式的 FK constraint。

同時把現有寫死在 system_prompt.py 的「Boon」人格，種一筆資料進 personas
表（status=active, is_default=true），做為系統預設 persona —— 這樣現有行為
在切換到 Persona Resolver 後不會改變。
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "102d7d73bbd7"
down_revision = "b6dcae4f4555"
branch_labels = None
depends_on = None

# 固定 UUID，讓程式碼可以直接用常數引用這筆 default persona，
# 不需要每次都查一次「哪一筆 is_default=true」。
DEFAULT_PERSONA_ID = "00000000-0000-0000-0000-000000000001"

# 對應原本 system_prompt.py 的 TOP_LAYER 中，屬於「人格描述」的部分
# （語言限制 / 絕對禁止事項 / 危機熱線是 SAFETY_CORE，永遠固定，不放進這裡）
DEFAULT_PERSONA_FRAGMENT = """你是「Boon」，一位具備高度同理心的心理健康支持助理。
你的唯一職責是：主動傾聽、接住情緒、給予溫和且不帶批判的情緒支持。

【你的核心原則】
1. 先感受，後回應：先確認並接受對方的情緒，再給予支持
2. 使用繁體中文，語氣溫暖、真誠、不說教
3. 粵語口語可接受（如「唔使擔心」），讓對方感到親切
4. 若對方願意，可溫和引導他們分享更多感受
5. 若情況嚴重，主動提供香港緊急求助資源"""


def upgrade() -> None:
    op.create_table(
        "therapists",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("role", sa.Text(), nullable=False, server_default="therapist"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('therapist','senior_therapist','admin')", name="ck_therapists_role"),
    )

    op.create_table(
        "personas",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tone", sa.Text(), nullable=False),
        sa.Column("response_length", sa.Text(), nullable=False, server_default="medium"),
        sa.Column("therapy_style", sa.Text(), nullable=True),
        sa.Column("system_prompt_fragment", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("therapists.id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('draft','active','archived')", name="ck_personas_status"),
        sa.CheckConstraint(
            "response_length IN ('short','medium','long')", name="ck_personas_response_length"
        ),
    )
    # 同一時間只能有一個 is_default=true 的 persona
    op.create_index(
        "ux_personas_single_default", "personas", ["is_default"],
        unique=True, postgresql_where=sa.text("is_default = true"),
    )

    op.create_table(
        "persona_match_conditions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "persona_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("personas.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("condition_json", postgresql.JSONB(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_persona_match_conditions_persona_id", "persona_match_conditions", ["persona_id"]
    )

    op.create_table(
        "persona_assignments",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "persona_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("personas.id"), nullable=False,
        ),
        sa.Column(
            "assigned_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("therapists.id"), nullable=False,
        ),
        sa.Column("assigned_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
    )

    op.create_table(
        "persona_switch_log",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("from_persona_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("personas.id"), nullable=True),
        sa.Column("to_persona_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("personas.id"), nullable=False),
        sa.Column("trigger_reason", sa.Text(), nullable=True),
        sa.Column("switched_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_persona_switch_log_session_id", "persona_switch_log", ["session_id"])

    # ── 補上 Phase 0 留空的 FK constraint ────────────────────────────────────
    op.create_foreign_key(
        "fk_sessions_persona_id", "sessions", "personas", ["persona_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_messages_persona_id", "messages", "personas", ["persona_id"], ["id"]
    )

    # ── 種入預設 persona（對應現有寫死的「Boon」人格）───────────────────────
    personas_table = sa.table(
        "personas",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("tone", sa.Text()),
        sa.column("response_length", sa.Text()),
        sa.column("system_prompt_fragment", sa.Text()),
        sa.column("status", sa.Text()),
        sa.column("is_default", sa.Boolean()),
    )
    op.bulk_insert(
        personas_table,
        [
            {
                "id": uuid.UUID(DEFAULT_PERSONA_ID),
                "name": "Boon（預設）",
                "description": "系統預設人格，對應原本寫死在 system_prompt.py 的內容。",
                "tone": "溫暖真誠",
                "response_length": "medium",
                "system_prompt_fragment": DEFAULT_PERSONA_FRAGMENT,
                "status": "active",
                "is_default": True,
            }
        ],
    )


def downgrade() -> None:
    op.drop_constraint("fk_messages_persona_id", "messages", type_="foreignkey")
    op.drop_constraint("fk_sessions_persona_id", "sessions", type_="foreignkey")
    op.drop_index("ix_persona_switch_log_session_id", table_name="persona_switch_log")
    op.drop_table("persona_switch_log")
    op.drop_table("persona_assignments")
    op.drop_index("ix_persona_match_conditions_persona_id", table_name="persona_match_conditions")
    op.drop_table("persona_match_conditions")
    op.drop_index("ux_personas_single_default", table_name="personas")
    op.drop_table("personas")
    op.drop_table("therapists")
