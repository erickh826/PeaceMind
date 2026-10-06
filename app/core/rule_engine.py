"""
Rule Engine（Phase 4，T-Q1–T-Q5）
每輪對話決定要套用哪一條規則（最多一條），提供 persona_id / example_ids /
therapy / tone 給 /chat 組裝 prompt。設計決策見 docs/CLINICAL_FRAMEWORK_TASKS.md
Phase 4「開工前的設計決策」。

版本選擇（applicable_version，先選版本、再看狀態）：
  某條規則對某個 session 的適用版本，是符合下列任一條件、version_number 最大的那一版：
    - 快照 status = archived（封存一律立即生效，不論快照的 scope）
    - 快照 scope = immediate
    - session 開始時這個版本已經存在（見下方「版本是否已存在」）
  選出後「該版本」的 status 是 active 才套用。不能先篩選 active 再選版本，否則會
  跳過停用版本、重新選中更舊的 active 版本。例：v1 active → v2 archived →
  v3 active（new_conversations_only），v2 之前開始的 session 選中 v2 → 停用；
  v3 之後的新 session 才用 v3。

  固定的是規則版本，不是命中結果：每輪仍依當下 profile 重新比對條件。

版本是否已存在：不比較時間戳。now() 是 transaction 開始時間，等待 row lock 或尚未
commit 的更新會拿到比 session 更早的時間戳——session 開始時明明看不到這個版本，下一輪
卻會被判定成「開始前就存在」。改成比較資料庫快照：rule_versions.created_xact 是寫入
版本的 transaction id，sessions.rule_snapshot 是建立 session 那個 INSERT 當下的快照，
pg_visible_in_snapshot() 為 true 才代表該版本在 session 開始前已經 commit。
（見 migration 6d55b7146dcc。/chat 在 Rule Engine 之前先 ensure_session()，快照才會
早於首輪查詢，首輪與之後各輪的判定一致。）

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
    existed_at_session_start: bool
    snapshot: dict


def applicable_version(versions: list[VersionRow]) -> VersionRow | None:
    """依檔案頂端的規則選出適用版本（不檢查 status，由呼叫端判斷）。"""
    eligible = [
        v
        for v in versions
        if v.snapshot.get("status") == "archived"
        or v.snapshot.get("scope") == "immediate"
        or v.existed_at_session_start
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


async def resolve_rule(session_client_key: str | None) -> AppliedRule:
    """呼叫前 session 必須已存在（/chat 先呼叫 ensure_session()），否則回傳 NO_RULE。"""
    if not os.environ.get("DATABASE_URL") or not session_client_key:
        return NO_RULE

    try:
        return await _resolve(session_client_key)
    except Exception:
        logger.exception("rule resolution failed, continuing without rule | session=%s", session_client_key)
        return NO_RULE


# 目前狀態是 archived 的規則，最新版本必定是 archived 快照（API 在同一筆 transaction
# 更新 rules 與寫入版本），且封存版本永遠適用 → 一定停用，直接略過。draft 不能略過：
# 舊 session 可能還適用更早的 active 版本。session 是 migration 之前建立的（快照為 NULL）
# 時，任何版本都視為不存在。
_VERSIONS_SQL = """
SELECT r.id AS rule_id, r.created_at AS rule_created_at,
       rv.id AS version_id, rv.version_number, rv.snapshot_json,
       COALESCE(pg_visible_in_snapshot(rv.created_xact, s.rule_snapshot), false) AS existed
FROM rules r
JOIN rule_versions rv ON rv.rule_id = r.id
CROSS JOIN sessions s
WHERE s.client_key = :client_key AND r.status <> 'archived'
"""


async def _resolve(session_client_key: str) -> AppliedRule:
    from sqlalchemy import select, text

    from app.db import get_session
    from app.db.models import User
    from app.db.models_profile import ProfileTopic, UserProfile

    async with get_session() as db:
        user_row = await db.scalar(select(User).where(User.external_ref == session_client_key))
        if user_row is None:
            return NO_RULE

        rows = (await db.execute(text(_VERSIONS_SQL), {"client_key": session_client_key})).all()
        if not rows:
            return NO_RULE

        versions_by_rule: dict[str, list[VersionRow]] = {}
        rule_created_at: dict[str, datetime] = {}
        for row in rows:
            rule_id = str(row.rule_id)
            rule_created_at[rule_id] = row.rule_created_at
            versions_by_rule.setdefault(rule_id, []).append(
                VersionRow(
                    id=str(row.version_id), version_number=row.version_number,
                    existed_at_session_start=row.existed, snapshot=row.snapshot_json,
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
        for rule_id, versions in versions_by_rule.items():
            version = applicable_version(versions)
            if version is None or version.snapshot.get("status") != "active":
                continue
            if rule_condition_matches(version.snapshot.get("conditions_json") or {}, profile_row, topic_counts):
                candidates.append(
                    Candidate(rule_id=rule_id, rule_created_at=rule_created_at[rule_id], version=version)
                )

        chosen = pick_rule(candidates)
        return NO_RULE if chosen is None else _to_applied(chosen)
