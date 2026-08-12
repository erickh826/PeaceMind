"""
ORM Models — Phase 0
只建立 users / sessions / messages 三張表，對應
InMemoryConversationStore → PostgresConversationStore 的替換。

後續 Phase（Persona / Rule / Profile / Risk / Example）會在對應階段
各自新增檔案（例如 app/db/models_persona.py），避免這個檔案無限膨脹。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    external_ref: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    sessions: Mapped[list["ConversationSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ConversationSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("client_key", name="uq_sessions_client_key"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 前端傳入的 session_id（任意字串，如 crypto.randomUUID() 或測試用固定字串）
    client_key: Mapped[str] = mapped_column(Text, nullable=False)
    # persona_id：Phase 1 起有 FK 指向 personas.id
    persona_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="ck_messages_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # persona_id：Phase 1 起有 FK 指向 personas.id；rule_id 待 Phase 4 補上
    persona_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id"), nullable=True
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    session: Mapped["ConversationSession"] = relationship(back_populates="messages")
