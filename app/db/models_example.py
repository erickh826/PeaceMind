"""
ORM Models — Phase 3（範例庫，T-Q16–T-Q20）

response_examples：治療師提供的範例回覆 + 適用條件（applicable_conditions_json
跟 persona_match_conditions.condition_json 同格式，由 app/core/condition_matching.py
比對）。usage_mode 決定注入方式（T-Q17），attribution_mode 決定回覆是否附加
引用標記（T-Q20，由 app/routers/chat.py 決定性附加，不交給 LLM）。

example_usage_log：每次範例被注入 prompt 的使用記錄（T-Q18 有效性追蹤）。
message_id / session_id 刻意可為 NULL + ON DELETE SET NULL，不是 NOT NULL + CASCADE：
/api/v1/reset 會刪除整個 session，messages 連帶 CASCADE 刪除——NOT NULL 會讓 /reset
因外鍵限制失敗，CASCADE 則會讓使用記錄跟著憑空消失（跟 Phase 2 session_summaries
同一類坑）。額外記下 user_id，message 被刪除後仍可回溯是哪個使用者。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# created_by 對 therapists.id 有 FK，確保 Therapist 已註冊進 Base.metadata，
# flush 時 SQLAlchemy 才解析得到這個外鍵（不依賴其他模組的 import 順序）。
from app.db import models, models_persona  # noqa: F401


class ResponseExample(Base):
    __tablename__ = "response_examples"
    __table_args__ = (
        CheckConstraint(
            "usage_mode IN ('style_learning','direct_quote')",
            name="ck_response_examples_usage_mode",
        ),
        CheckConstraint(
            "attribution_mode IN ('anonymous','attributed')",
            name="ck_response_examples_attribution_mode",
        ),
        CheckConstraint("status IN ('active','archived')", name="ck_response_examples_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 例：{"year_of_study": "Year 1", "topics_include": ["Relationship"]}
    applicable_conditions_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    usage_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="style_learning")
    attribution_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="anonymous")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ExampleUsageLog(Base):
    """範例被注入 prompt 的使用記錄（T-Q18）。"""

    __tablename__ = "example_usage_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    example_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("response_examples.id", ondelete="CASCADE"), nullable=False
    )
    # 刻意 nullable + SET NULL，見檔案頂端說明
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    used_at: Mapped[datetime] = mapped_column(server_default=func.now())
    effectiveness_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
