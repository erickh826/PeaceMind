"""
ORM Models — Phase 1（Persona 系統）

治療師帳號目前只有最基本的欄位，還沒有真正的登入/權限機制
（Auth 留給 Phase 8 Admin Console 前端一起做）。這裡先建表，
讓 persona 的 created_by / assigned_by 有地方可以指向，
API 目前先用 request body 直接傳 therapist_id，之後接上真實 auth
後改成從 session/token 取得，呼叫端不需要再自己傳。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, SmallInteger, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Therapist(Base):
    __tablename__ = "therapists"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="therapist")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "role IN ('therapist','senior_therapist','admin')", name="ck_therapists_role"
        ),
    )


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tone: Mapped[str] = mapped_column(Text, nullable=False)
    response_length: Mapped[str] = mapped_column(Text, nullable=False, server_default="medium")
    therapy_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 會被組裝進三明治 Prompt 頂層的人格描述片段。
    # 不包含語言限制/絕對禁止事項/危機熱線 —— 那些是 SAFETY_CORE，永遠固定，不受 persona 影響。
    system_prompt_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    is_default: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('draft','active','archived')", name="ck_personas_status"),
        CheckConstraint(
            "response_length IN ('short','medium','long')", name="ck_personas_response_length"
        ),
    )

    match_conditions: Mapped[list["PersonaMatchCondition"]] = relationship(
        back_populates="persona", cascade="all, delete-orphan"
    )


class PersonaMatchCondition(Base):
    __tablename__ = "persona_match_conditions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    persona_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), nullable=False
    )
    # 例：{"year_of_study": "Year 1", "topics_include": ["社交焦慮"]}
    # Phase 2 補上 user_profiles / profile_topics 後才會真正被 Persona Resolver 使用
    condition_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    persona: Mapped["Persona"] = relationship(back_populates="match_conditions")


class PersonaAssignment(Base):
    """治療師手動指派 persona 給特定學生，覆寫自動匹配（T-Q15）。"""

    __tablename__ = "persona_assignments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    persona_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id"), nullable=False
    )
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(server_default=func.now())
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PersonaSwitchLog(Base):
    """同一對話中切換 persona 的紀錄（T-Q13/T-Q14）。"""

    __tablename__ = "persona_switch_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    from_persona_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id"), nullable=True
    )
    to_persona_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id"), nullable=False
    )
    trigger_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    switched_at: Mapped[datetime] = mapped_column(server_default=func.now())
