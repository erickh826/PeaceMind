"""
Admin API — 對話檢視、手動覆寫、回饋標籤（Phase 5.5 / 5.6，T-Q9 / T-Q10）

隱私：這裡每個端點都會回傳學生對話內容，一律要 X-Admin-Key（規則、範例清單維持公開
讀取，因為不含學生資料）。錯誤回應與 log 不帶學生訊息原文。

覆寫與標籤都只是「事後標註」：
- 不改 messages.content，學生已經看到的回覆不變，之後 LLM 看到的對話歷史也不變。
- 只能針對真實對話中的 assistant 訊息（沙盒不寫 messages，所以不會被標到）。
- 覆寫的用途是累積訓練語料（used_for_training）；真的要「改掉」學生端內容是另一個
  複雜得多的功能，不在 Phase 5。
- 衍生資料隨 messages 的 ON DELETE CASCADE：學生 /reset 或「忘記我」時一併消失。

治療師 id 同樣是自報（見 app/core/rule_review.py 的說明），未帶時落到 placeholder。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.admin_auth import require_admin_key
from app.routers.admin_rules import _parse_uuid, _require_therapist

router = APIRouter(dependencies=[Depends(require_admin_key)])

# 固定詞彙表：改詞彙要走部署，避免統計失真。要增減標籤時同步更新任務文件。
FEEDBACK_TAGS = ("太正式", "缺乏同理心", "療法選擇不當", "資訊錯誤", "其他")
FeedbackTagName = Literal["太正式", "缺乏同理心", "療法選擇不當", "資訊錯誤", "其他"]


def _require_db():
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL 未設定，此功能需要資料庫。")


async def _get_assistant_message(db, message_id: str):
    from app.db.models import Message

    message = await db.get(Message, _parse_uuid(message_id, "message_id"))
    if message is None:
        raise HTTPException(status_code=404, detail="訊息不存在")
    if message.role != "assistant":
        raise HTTPException(status_code=422, detail="只能對 assistant 訊息做覆寫或標籤")
    return message


# ── 對話檢視 ────────────────────────────────────────────────────────────────

class SessionSummaryOut(BaseModel):
    session_id: str
    started_at: datetime
    ended_at: datetime | None
    message_count: int


class OverrideBrief(BaseModel):
    id: str
    overridden_reply: str
    used_for_training: bool


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    rule_id: str | None
    rule_version_id: str | None
    output_replaced: bool
    overrides: list[OverrideBrief]
    tags: list[str]


@router.get("/sessions", response_model=list[SessionSummaryOut])
async def list_sessions(limit: int = 50):
    _require_db()
    from app.db import get_session
    from app.db.models import ConversationSession, Message

    async with get_session() as db:
        rows = (
            await db.execute(
                select(ConversationSession, func.count(Message.id))
                .outerjoin(Message, Message.session_id == ConversationSession.id)
                .group_by(ConversationSession.id)
                .order_by(ConversationSession.started_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).all()
        return [
            SessionSummaryOut(
                session_id=s.client_key, started_at=s.started_at, ended_at=s.ended_at, message_count=count
            )
            for s, count in rows
        ]


@router.get("/messages", response_model=list[MessageOut])
async def list_messages(session_id: str):
    _require_db()
    from app.db import get_session
    from app.db.models import ConversationSession, Message
    from app.db.models_review import FeedbackTag, ManualOverride

    async with get_session() as db:
        session_row = await db.scalar(
            select(ConversationSession).where(ConversationSession.client_key == session_id)
        )
        if session_row is None:
            raise HTTPException(status_code=404, detail="session 不存在")
        messages = (
            await db.execute(
                select(Message).where(Message.session_id == session_row.id).order_by(Message.created_at, Message.id)
            )
        ).scalars().all()
        ids = [m.id for m in messages]

        overrides: dict[uuid.UUID, list] = {}
        tags: dict[uuid.UUID, list[str]] = {}
        if ids:
            for o in (
                await db.execute(select(ManualOverride).where(ManualOverride.message_id.in_(ids)).order_by(ManualOverride.created_at))
            ).scalars():
                overrides.setdefault(o.message_id, []).append(o)
            for t in (await db.execute(select(FeedbackTag).where(FeedbackTag.message_id.in_(ids)))).scalars():
                tags.setdefault(t.message_id, []).append(t.tag)

        return [
            MessageOut(
                id=str(m.id), role=m.role, content=m.content, created_at=m.created_at,
                rule_id=str(m.rule_id) if m.rule_id else None,
                rule_version_id=str(m.rule_version_id) if m.rule_version_id else None,
                output_replaced=m.output_replaced,
                overrides=[
                    OverrideBrief(id=str(o.id), overridden_reply=o.overridden_reply, used_for_training=o.used_for_training)
                    for o in overrides.get(m.id, [])
                ],
                tags=sorted(set(tags.get(m.id, []))),
            )
            for m in messages
        ]


# ── 5.5 手動覆寫 ────────────────────────────────────────────────────────────

class OverrideCreateRequest(BaseModel):
    overridden_reply: str = Field(..., min_length=1, max_length=4000)
    overridden_by: str | None = None
    used_for_training: bool = False


class OverrideUpdateRequest(BaseModel):
    used_for_training: bool


class OverrideOut(BaseModel):
    id: str
    message_id: str
    original_reply: str
    overridden_reply: str
    overridden_by: str
    used_for_training: bool
    created_at: datetime


def _override_out(o) -> OverrideOut:
    return OverrideOut(
        id=str(o.id), message_id=str(o.message_id), original_reply=o.original_reply,
        overridden_reply=o.overridden_reply, overridden_by=str(o.overridden_by),
        used_for_training=o.used_for_training, created_at=o.created_at,
    )


@router.post("/messages/{message_id}/override", response_model=OverrideOut)
async def create_override(message_id: str, request: OverrideCreateRequest):
    _require_db()
    from app.db import get_session
    from app.db.models_review import ManualOverride

    async with get_session() as db:
        message = await _get_assistant_message(db, message_id)
        therapist = await _require_therapist(db, request.overridden_by, "overridden_by")
        row = ManualOverride(
            message_id=message.id, original_reply=message.content,
            overridden_reply=request.overridden_reply, overridden_by=therapist,
            used_for_training=request.used_for_training,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return _override_out(row)


@router.patch("/overrides/{override_id}", response_model=OverrideOut)
async def update_override(override_id: str, request: OverrideUpdateRequest):
    _require_db()
    from app.db import get_session
    from app.db.models_review import ManualOverride

    async with get_session() as db:
        row = await db.get(ManualOverride, _parse_uuid(override_id, "override_id"))
        if row is None:
            raise HTTPException(status_code=404, detail="覆寫不存在")
        row.used_for_training = request.used_for_training
        await db.commit()
        await db.refresh(row)
        return _override_out(row)


@router.get("/overrides", response_model=list[OverrideOut])
async def list_overrides(used_for_training: bool | None = None, limit: int = 100):
    _require_db()
    from app.db import get_session
    from app.db.models_review import ManualOverride

    async with get_session() as db:
        stmt = select(ManualOverride).order_by(ManualOverride.created_at.desc()).limit(max(1, min(limit, 500)))
        if used_for_training is not None:
            stmt = stmt.where(ManualOverride.used_for_training == used_for_training)
        return [_override_out(o) for o in (await db.execute(stmt)).scalars().all()]


# ── 5.6 回饋標籤 ────────────────────────────────────────────────────────────

class TagCreateRequest(BaseModel):
    tags: list[FeedbackTagName] = Field(..., min_length=1, max_length=len(FEEDBACK_TAGS))
    note: str | None = Field(default=None, max_length=2000)
    tagged_by: str | None = None


class TagOut(BaseModel):
    id: str
    message_id: str
    tag: str
    note: str | None
    tagged_by: str
    created_at: datetime


def _tag_out(t) -> TagOut:
    return TagOut(
        id=str(t.id), message_id=str(t.message_id), tag=t.tag, note=t.note,
        tagged_by=str(t.tagged_by), created_at=t.created_at,
    )


@router.get("/feedback-tag-vocabulary", response_model=list[str])
async def tag_vocabulary():
    return list(FEEDBACK_TAGS)


@router.post("/messages/{message_id}/tags", response_model=list[TagOut])
async def add_tags(message_id: str, request: TagCreateRequest):
    """一則訊息可有多個標籤；同一位治療師重複標同一個標籤會被忽略。"""
    _require_db()
    from app.db import get_session
    from app.db.models_review import FeedbackTag

    async with get_session() as db:
        message = await _get_assistant_message(db, message_id)
        therapist = await _require_therapist(db, request.tagged_by, "tagged_by")
        existing = {
            t.tag
            for t in (
                await db.execute(
                    select(FeedbackTag).where(
                        FeedbackTag.message_id == message.id, FeedbackTag.tagged_by == therapist
                    )
                )
            ).scalars()
        }
        rows = [
            FeedbackTag(message_id=message.id, tag=tag, note=request.note, tagged_by=therapist)
            for tag in dict.fromkeys(request.tags)
            if tag not in existing
        ]
        db.add_all(rows)
        await db.commit()
        for row in rows:
            await db.refresh(row)
        return [_tag_out(r) for r in rows]


@router.get("/feedback-tags", response_model=list[TagOut])
async def list_tags(tag: FeedbackTagName | None = None, message_id: str | None = None, limit: int = 200):
    _require_db()
    from app.db import get_session
    from app.db.models_review import FeedbackTag

    async with get_session() as db:
        stmt = select(FeedbackTag).order_by(FeedbackTag.created_at.desc()).limit(max(1, min(limit, 1000)))
        if tag is not None:
            stmt = stmt.where(FeedbackTag.tag == tag)
        if message_id is not None:
            stmt = stmt.where(FeedbackTag.message_id == _parse_uuid(message_id, "message_id"))
        return [_tag_out(t) for t in (await db.execute(stmt)).scalars().all()]
