"""
ORM Model — Phase 6（危機分層，Q10–Q14）

risk_events：獨立的風險紀錄表，供治療師審閱。刻意跟架構文件 DDL 不同的地方：

- session_id / message_id 是 ON DELETE SET NULL（原本 NOT NULL REFERENCES）：學生按「重新開始」
  （/reset）會刪除 session 與訊息；FK 若擋住，學生刪不掉對話，若 CASCADE，風險紀錄會一起消失。
  改成 SET NULL：對話照樣被刪，最低限度的安全紀錄（觸發片段、tier、時間、user_id）保留。
- resolved_at：記錄處理時間。
- DB trigger（見 migration）擋 DELETE，且只允許把 session_id / message_id 設成 NULL、
  補寫一次通知時間與處理紀錄；tier、片段、時間不可修改。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

from app.db import models, models_persona  # noqa: F401  FK 對象先註冊


class RiskEvent(Base):
    __tablename__ = "risk_events"
    __table_args__ = (
        CheckConstraint("risk_tier IN (1,2,3)", name="ck_risk_events_tier"),
        Index("ix_risk_events_user_created", "user_id", "created_at"),
        Index("ix_risk_events_session", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    trigger_snippet: Mapped[str] = mapped_column(Text, nullable=False)
    risk_tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    supervisor_notified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    escalated_to_human_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
