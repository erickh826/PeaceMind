"""seed placeholder therapist

Revision ID: cbda7ba4a1c9
Revises: 102d7d73bbd7
Create Date: 2026-08-14 16:38:25.353473

Phase 1 的 persona_assignments.assigned_by 對 therapists.id 有 FK 約束，
但目前沒有真實治療師登入機制（留給 Phase 8 跟 Admin Console 一起做），
therapists 表在此之前完全是空的 —— 導致 POST /admin/personas/assign
永遠會因為 FK violation 打不通，即使呼叫端自己填了 assigned_by 也一樣。

這裡種一筆固定 UUID 的 placeholder therapist，暫時頂著這個 FK，讓手動
指派流程在 Phase 8 真正的治療師帳號系統上線前也能被呼叫、驗證邏輯。
Phase 8 加入真實治療師登入後，這筆記錄應該保留當作系統帳號或直接汰換
成真實資料，屆時再決定去留。
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'cbda7ba4a1c9'
down_revision: Union[str, Sequence[str], None] = '102d7d73bbd7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 固定 UUID，方便程式碼/腳本直接引用這筆 placeholder therapist
# （呼應 102d7d73bbd7 裡 DEFAULT_PERSONA_ID 的做法）
PLACEHOLDER_THERAPIST_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    """Upgrade schema."""
    therapists_table = sa.table(
        "therapists",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.Text()),
        sa.column("email", sa.Text()),
        sa.column("role", sa.Text()),
    )
    op.bulk_insert(
        therapists_table,
        [
            {
                "id": uuid.UUID(PLACEHOLDER_THERAPIST_ID),
                "name": "Placeholder Therapist（暫代，Phase 8 前使用）",
                "email": "placeholder-therapist@peacemind.local",
                "role": "admin",
            }
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        sa.text("DELETE FROM therapists WHERE id = :id").bindparams(
            id=uuid.UUID(PLACEHOLDER_THERAPIST_ID)
        )
    )
