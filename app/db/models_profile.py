"""
ORM Models — Phase 2（Profile / 主題演化 / 跨 Session 摘要）

對應 docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md §2.1 與
docs/Phase2_implement_plan_Antigravity.md 的資料模型設計。

SessionSummary.session_id 刻意用 ON DELETE SET NULL，不是 CASCADE：
/api/v1/reset（前端「新對話」按鈕）會在寫入摘要之後把 sessions 那筆刪掉，
如果這裡是 CASCADE，摘要會在寫入的同一輪請求裡被自己連坐刪除，
Q4–Q6 的跨 session 記憶就永遠留不住任何東西（見 Phase2_implement_plan_Antigravity.md §2）。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    year_of_study: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False, server_default="none")
    profile_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "risk_level IN ('none','low','medium','high')", name="ck_user_profiles_risk"
        ),
    )


class ProfileChangeLog(Base):
    __tablename__ = "profile_change_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    field_path: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "source IN ('user_correction','system_inferred','therapist_edit')",
            name="ck_profile_change_source",
        ),
    )


class ProfileTopic(Base):
    __tablename__ = "profile_topics"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    topic: Mapped[str] = mapped_column(Text, primary_key=True)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SessionSummary(Base):
    __tablename__ = "session_summaries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # SET NULL, not CASCADE — 見檔案頂端說明。
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    key_events: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    emotions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    strategies_used: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    retention_policy: Mapped[str] = mapped_column(Text, nullable=False, server_default="permanent")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "retention_policy IN ('permanent','90d','30d')", name="ck_session_summaries_retention"
        ),
    )
