"""
Rule Engine（Phase 4，T-Q1–T-Q5）
每輪對話決定要套用哪一條規則（最多一條），提供 persona_id / example_ids /
therapy / tone 給 /chat 組裝 prompt。設計決策見 docs/CLINICAL_FRAMEWORK_TASKS.md
Phase 4「開工前的設計決策」。

版本選擇（applicable_version，先選版本、再看狀態）：
  某條規則對某個 session 的適用版本，是符合下列任一條件、version_number 最大的那一版：
    - 快照 status = archived（封存一律立即生效，不論快照的 scope）
    - 快照 scope = immediate
    - created_at <= sessions.started_at（session 開始前就存在的版本）
  選出後「該版本」的 status 是 active 才套用。不能先篩選 active 再選版本，否則會
  跳過停用版本、重新選中更舊的 active 版本。例：v1 active → v2 archived →
  v3 active（new_conversations_only），v2 之前開始的 session 選中 v2 → 停用；
  v3 之後的新 session 才用 v3。

  固定的是規則版本，不是命中結果：每輪仍依當下 profile 重新比對條件。

規則衝突（T-Q4）：命中多條時取快照 priority 最高者，再依規則 created_at（新者優先）、
id 做決定性的 tie-break（同 Phase 3）。

Phase 4 不做 in-process cache（Vercel serverless + NullPool，跨 invocation 的
快取不可靠），每輪直接查詢。

跟 Example Selector 一樣 fail-open：DATABASE_URL 未設定、沒有 session、查詢出錯時
回傳 NO_RULE，不讓 /chat 因為規則問題 500。規則只是回覆策略的調整，不是安全層。
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.core.condition_matching import rule_condition_matches

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppliedRule:
    rule_id: str | None = None
    rule_version_id: str | None = None
    persona_id: str | None = None
    example_ids: list[str] = field(default_factory=list)
    therapy: str | None = None
    tone: str | None = None

    @property
    def strategy(self) -> dict | None:
        """給 build_prompt() 的「本輪策略」；沒有 therapy / tone 時回傳 None。"""
        if not self.therapy and not self.tone:
            return None
        return {"therapy": self.therapy, "tone": self.tone}


NO_RULE = AppliedRule()


@dataclass(frozen=True)
class VersionRow:
    id: str
    version_number: int
    created_at: datetime
    snapshot: dict


def applicable_version(versions: list[VersionRow], session_started_at: datetime | None) -> VersionRow | None:
    """
    依檔案頂端的規則選出適用版本（不檢查 status，由呼叫端判斷）。
    session_started_at 為 None 時視為「現在才開始」，所有版本都適用、選最新一版。
    """
    eligible = [
        v
        for v in versions
        if session_started_at is None
        or v.snapshot.get("status") == "archived"
        or v.snapshot.get("scope") == "immediate"
        or v.created_at <= session_started_at
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda v: v.version_number)


@dataclass(frozen=True)
class Candidate:
    rule_id: str
    rule_created_at: datetime
    version: VersionRow


def pick_rule(candidates: list[Candidate]) -> Candidate | None:
    """命中多條規則時取 priority 最高，再依規則 created_at（新者優先）、id 決定（T-Q4）。"""
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda c: (c.version.snapshot.get("priority", 0), c.rule_created_at, c.rule_id),
    )


def _to_applied(candidate: Candidate) -> AppliedRule:
    action = candidate.version.snapshot.get("action_json") or {}
    return AppliedRule(
        rule_id=candidate.rule_id,
        rule_version_id=candidate.version.id,
        persona_id=action.get("persona_id"),
        example_ids=list(action.get("example_ids") or []),
        therapy=action.get("therapy"),
        tone=action.get("tone"),
    )


async def resolve_rule(session_client_key: str | None, session_started_at: datetime | None) -> AppliedRule:
    if not os.environ.get("DATABASE_URL") or not session_client_key:
        return NO_RULE

    try:
        return await _resolve(session_client_key, session_started_at)
    except Exception:
        logger.exception("rule resolution failed, continuing without rule | session=%s", session_client_key)
        return NO_RULE


async def _resolve(session_client_key: str, session_started_at: datetime | None) -> AppliedRule:
    from sqlalchemy import select

    from app.db import get_session
    from app.db.models import User
    from app.db.models_profile import ProfileTopic, UserProfile
    from app.db.models_rule import Rule, RuleVersion

    async with get_session() as db:
        user_row = await db.scalar(select(User).where(User.external_ref == session_client_key))
        if user_row is None:
            return NO_RULE

        # 目前狀態是 archived 的規則，最新版本必定是 archived 快照（API 在同一筆
        # transaction 更新 rules 與寫入版本），且封存版本永遠適用 → 一定停用，直接略過。
        # draft 不能略過：舊 session 可能還適用更早的 active 版本。
        rules = (
            await db.execute(select(Rule).where(Rule.status != "archived"))
        ).scalars().all()
        if not rules:
            return NO_RULE

        version_rows = (
            await db.execute(
                select(RuleVersion).where(RuleVersion.rule_id.in_([r.id for r in rules]))
            )
        ).scalars().all()
        versions_by_rule: dict[uuid.UUID, list[VersionRow]] = {}
        for v in version_rows:
            versions_by_rule.setdefault(v.rule_id, []).append(
                VersionRow(
                    id=str(v.id), version_number=v.version_number,
                    created_at=v.created_at, snapshot=v.snapshot_json,
                )
            )

        profile_row = await db.get(UserProfile, user_row.id)
        topic_counts = {
            topic: count
            for topic, count in (
                await db.execute(
                    select(ProfileTopic.topic, ProfileTopic.mention_count).where(
                        ProfileTopic.user_id == user_row.id
                    )
                )
            ).all()
        }

        candidates = []
        for rule in rules:
            version = applicable_version(versions_by_rule.get(rule.id, []), session_started_at)
            if version is None or version.snapshot.get("status") != "active":
                continue
            if rule_condition_matches(version.snapshot.get("conditions_json") or {}, profile_row, topic_counts):
                candidates.append(Candidate(rule_id=str(rule.id), rule_created_at=rule.created_at, version=version))

        chosen = pick_rule(candidates)
        return NO_RULE if chosen is None else _to_applied(chosen)
