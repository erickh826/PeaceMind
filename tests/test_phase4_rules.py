"""
Phase 4 tests — Rule Engine（T-Q1–T-Q5、T-Q8 基礎版）。

分兩部分：
1. 不需要資料庫的單元測試：rules 模式條件驗證與比對、版本選擇（含封存與混合 scope）、
   規則衝突、action 驗證、[SESSION STRATEGY] prompt 區塊、admin key、/chat 串接
   （Rule Engine / Persona Resolver / Example Selector 用 monkeypatch 換掉）。
2. 需要真實 Postgres 的整合測試（DATABASE_URL 未設定時跳過）：完成判準情境、
   每輪規則紀錄、版本 API、scope 與封存的端到端行為、persona 優先序、範例合併。

跟 Phase 2/3 測試一樣不呼叫 load_dotenv()、不依賴 pytest-asyncio。

rules 是全域資料：每支 DB 測試都在 finally 刪掉自己建立的規則，避免殘留的 active
規則命中其他測試的使用者。規則條件只能用標準主題，所以測試使用者另外種主題次數。
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi.testclient import TestClient

from app.core import rule_engine
from app.core.condition_matching import rule_condition_matches, validate_condition_json
from app.core.example_selector import EMPTY_SELECTION
from app.core.rule_engine import (
    NO_RULE,
    AppliedRule,
    Candidate,
    VersionRow,
    applicable_version,
    pick_rule,
)
from app.main import app
from app.prompts.system_prompt import SAFETY_CORE, build_prompt
from app.routers.admin_rules import validate_action_json

requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="Phase 4 DB tests need a real Postgres (set DATABASE_URL, e.g. local Docker on :5433)",
)

TEST_ADMIN_KEY = "phase4-test-admin-key"
ACT_RULE_CONDITIONS = {"topics_include": ["Anxiety"], "risk_level": "low", "min_topic_mentions": 3}


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", TEST_ADMIN_KEY)


def _client() -> TestClient:
    return TestClient(app, headers={"X-Admin-Key": TEST_ADMIN_KEY})


async def _noop(*args, **kwargs) -> None:
    return None


# ── 1. 不需要資料庫 ───────────────────────────────────────────────────────────

def test_rule_mode_accepts_completion_criterion_conditions():
    assert validate_condition_json(ACT_RULE_CONDITIONS, mode="rule") == ACT_RULE_CONDITIONS


@pytest.mark.parametrize(
    "conditions",
    [
        {},
        {"topics_include": ["社交焦慮"]},  # 不在標準主題清單，永遠不會命中
        {"topics_include": "Anxiety"},
        {"min_topic_mentions": 3},  # 必須搭配 topics_include
        {"topics_include": ["Anxiety"], "min_topic_mentions": 0},
        {"topics_include": ["Anxiety"], "min_topic_mentions": True},
        {"topics_include": ["Anxiety"], "min_topic_mentions": "3"},
        {"risk_level": "critical"},
        {"history_therapy_used": "CBT"},  # 延後到有療法紀錄之後
    ],
)
def test_rule_mode_rejects_invalid_conditions(conditions):
    with pytest.raises(ValueError):
        validate_condition_json(conditions, mode="rule")


def test_default_mode_is_unchanged_for_personas_and_examples():
    # persona / example 仍只接受 year_of_study、topics_include，主題不限標準清單
    assert validate_condition_json({"topics_include": ["phase3-custom-topic"]})
    with pytest.raises(ValueError):
        validate_condition_json({"risk_level": "low"})


def _profile(year_of_study=None, risk_level="none"):
    return SimpleNamespace(year_of_study=year_of_study, risk_level=risk_level)


def test_rule_matching_topic_threshold_and_risk():
    profile = _profile(risk_level="low")
    assert not rule_condition_matches(ACT_RULE_CONDITIONS, profile, {"Anxiety": 2})
    assert rule_condition_matches(ACT_RULE_CONDITIONS, profile, {"Anxiety": 3})
    assert not rule_condition_matches(ACT_RULE_CONDITIONS, _profile(risk_level="medium"), {"Anxiety": 3})
    assert not rule_condition_matches(ACT_RULE_CONDITIONS, None, {"Anxiety": 3})


def test_rule_matching_threshold_defaults_and_does_not_sum_topics():
    conditions = {"topics_include": ["Anxiety", "Relationship"]}
    assert not rule_condition_matches(conditions, _profile(), {"Anxiety": 2, "Relationship": 2})
    assert rule_condition_matches(conditions, _profile(), {"Relationship": 3})
    assert rule_condition_matches(
        {"topics_include": ["Anxiety"], "min_topic_mentions": 1}, _profile(), {"Anxiety": 1}
    )
    assert not rule_condition_matches({"unknown": 1}, _profile(), {"Anxiety": 9})
    assert not rule_condition_matches({}, _profile(), {"Anxiety": 9})


T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _v(number: int, existed: bool, status: str = "active", scope: str = "new_conversations_only") -> VersionRow:
    """existed：session 開始時這個版本是否已經 commit（實際由資料庫快照判定）。"""
    return VersionRow(
        id=f"v{number}", version_number=number, existed_at_session_start=existed,
        snapshot={"status": status, "scope": scope, "priority": 0, "conditions_json": {}, "action_json": {}},
    )


def test_archive_then_reactivate_keeps_old_sessions_disabled():
    # v1 之後、v2 之前開始的 session
    old_session = [_v(1, True), _v(2, False, status="archived"), _v(3, False)]
    chosen = applicable_version(old_session)
    assert chosen.id == "v2" and chosen.snapshot["status"] == "archived"  # 停用，不會退回 v1
    # v3 之後開始的新 session 用 v3
    assert applicable_version([_v(1, True), _v(2, True, status="archived"), _v(3, True)]).id == "v3"
    # v2 與 v3 之間開始的 session：v3 是 new_conversations_only，仍停用
    assert applicable_version([_v(1, True), _v(2, True, status="archived"), _v(3, False)]).id == "v2"


def test_mixed_scope_history_picks_newest_eligible_version():
    versions = [_v(1, True), _v(2, False, status="draft", scope="immediate"), _v(3, False)]
    # v2 立即生效（停用），v3 是 session 開始後的 new_conversations_only，不適用
    assert applicable_version(versions).id == "v2"
    assert applicable_version([_v(1, True), _v(2, False, scope="immediate")]).id == "v2"
    # session 開始前規則不存在、之後才以 new_conversations_only 建立：不套用
    assert applicable_version([_v(1, False)]) is None


def test_latest_is_decided_by_version_number_not_list_order():
    assert applicable_version([_v(2, True), _v(1, True), _v(3, True, status="archived")]).id == "v3"


def test_pick_rule_prefers_priority_then_newer_rule_then_id():
    def candidate(rule_id, priority, minutes):
        version = VersionRow(
            id=f"{rule_id}-v1", version_number=1, existed_at_session_start=True,
            snapshot={"status": "active", "priority": priority},
        )
        return Candidate(rule_id=rule_id, rule_created_at=T0 + timedelta(minutes=minutes), version=version)

    assert pick_rule([]) is None
    assert pick_rule([candidate("a", 1, 9), candidate("b", 5, 0)]).rule_id == "b"
    assert pick_rule([candidate("a", 5, 0), candidate("b", 5, 9)]).rule_id == "b"
    assert pick_rule([candidate("a", 5, 0), candidate("b", 5, 0)]).rule_id == "b"


@pytest.mark.parametrize(
    "action",
    [
        {},
        {"therapy": ""},
        {"persona_id": "not-a-uuid"},
        {"example_ids": []},
        {"example_ids": ["not-a-uuid"]},
        {"example_ids": [123]},
        {"style": "ACT"},
    ],
)
def test_action_validation_rejects_invalid(action):
    with pytest.raises(ValueError):
        validate_action_json(action)


def test_strategy_block_sits_before_examples_and_safety_core():
    prompt = build_prompt(
        "hi",
        profile_text="[STUDENT PROFILE]",
        strategy={"therapy": "ACT", "tone": "溫暖接納"},
        examples=[{"usage_mode": "style_learning", "content": "範例"}],
    )
    at = prompt.index("[SESSION STRATEGY]")
    assert prompt.index("[STUDENT PROFILE]") < at < prompt.index("[CLINICAL RESPONSE EXAMPLES]")
    assert prompt.index("[CLINICAL RESPONSE EXAMPLES]") < prompt.index(SAFETY_CORE)
    assert "Therapeutic approach: ACT" in prompt and "Tone: 溫暖接納" in prompt
    assert prompt.count(SAFETY_CORE) == 1
    assert build_prompt("hi") == build_prompt("hi", strategy=None) == build_prompt("hi", strategy={})


def test_applied_rule_strategy_only_when_therapy_or_tone():
    assert NO_RULE.strategy is None
    assert AppliedRule(rule_id="r", example_ids=["x"]).strategy is None
    assert AppliedRule(tone="溫暖").strategy == {"therapy": None, "tone": "溫暖"}


def test_admin_writes_fail_closed_without_key(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    body = {"name": "r", "conditions_json": ACT_RULE_CONDITIONS, "action_json": {"therapy": "ACT"}}

    monkeypatch.delenv("ADMIN_API_KEY")
    assert _client().post("/api/v1/admin/rules", json=body).status_code == 503
    assert _client().post("/api/v1/admin/examples", json={}).status_code == 503

    monkeypatch.setenv("ADMIN_API_KEY", TEST_ADMIN_KEY)
    assert TestClient(app).post("/api/v1/admin/rules", json=body).status_code == 401
    assert TestClient(app, headers={"X-Admin-Key": "wrong"}).post(
        "/api/v1/admin/personas/assign", json={}
    ).status_code == 401
    # key 正確才會往下走（沒有 DB 時回 503「需要資料庫」）
    resp = _client().post("/api/v1/admin/rules", json=body)
    assert resp.status_code == 503 and "DATABASE_URL" in resp.json()["detail"]


def test_rule_engine_fails_open(monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("relation \"rules\" does not exist")

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused")
    monkeypatch.setattr(rule_engine, "_resolve", broken)
    assert asyncio.run(rule_engine.resolve_rule("any-session")) == NO_RULE


def test_chat_passes_rule_to_persona_examples_and_prompt(monkeypatch):
    from app.core.persona_resolver import FALLBACK_PERSONA

    rule = AppliedRule(
        rule_id=str(uuid.uuid4()), rule_version_id=str(uuid.uuid4()), persona_id="p-1",
        example_ids=["e-1"], therapy="ACT", tone="溫暖接納",
    )
    seen: dict = {}

    async def fake_resolve_rule(session_id):
        return rule

    async def fake_resolve_persona(user_client_key=None, rule_persona_id=None):
        seen["rule_persona_id"] = rule_persona_id
        return FALLBACK_PERSONA

    async def fake_select(session_id, pinned_example_ids=None):
        seen["pinned"] = pinned_example_ids
        return EMPTY_SELECTION

    def fake_llm(user_message, conversation_history=None, **kwargs):
        seen["strategy"] = kwargs.get("strategy")
        return "我明白你的感受。"

    monkeypatch.setattr("app.routers.chat.resolve_rule", fake_resolve_rule)
    monkeypatch.setattr("app.routers.chat.resolve_persona", fake_resolve_persona)
    monkeypatch.setattr("app.routers.chat.select_applicable_examples", fake_select)
    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_llm)
    monkeypatch.setattr("app.routers.chat.log_example_usage", _noop)
    monkeypatch.setattr("app.routers.chat.process_post_chat_updates", _noop)

    resp = TestClient(app).post("/api/v1/chat", json={"message": "最近好緊張"})
    assert resp.status_code == 200
    assert seen == {"rule_persona_id": "p-1", "pinned": ["e-1"], "strategy": {"therapy": "ACT", "tone": "溫暖接納"}}


# ── 2. 需要資料庫 ─────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _new_session_id(label: str) -> str:
    return f"phase4-test-{label}-" + uuid.uuid4().hex[:10]


async def _seed_profile(session_id: str, topics: dict[str, int], risk_level: str = "none") -> None:
    from sqlalchemy import select

    from app.db import get_session
    from app.db.models import User
    from app.db.models_profile import ProfileTopic, UserProfile

    async with get_session() as db:
        user = await db.scalar(select(User).where(User.external_ref == session_id))
        profile = await db.get(UserProfile, user.id)
        if profile is None:
            db.add(UserProfile(user_id=user.id, risk_level=risk_level))
        else:
            profile.risk_level = risk_level
        for topic, count in topics.items():
            row = await db.get(ProfileTopic, (user.id, topic))
            if row is None:
                db.add(ProfileTopic(user_id=user.id, topic=topic, mention_count=count))
            else:
                row.mention_count = count
        await db.commit()


async def _cleanup(session_ids=(), rule_ids=(), example_ids=(), persona_ids=()) -> None:
    from sqlalchemy import text

    from app.db import get_session

    async with get_session() as db:
        for rule_id in rule_ids:
            # rule_versions 是 CASCADE；messages.rule_id / rule_version_id 是 SET NULL
            await db.execute(text("DELETE FROM rules WHERE id = :id"), {"id": rule_id})
        for example_id in example_ids:
            await db.execute(text("DELETE FROM response_examples WHERE id = :id"), {"id": example_id})
        for session_id in session_ids:
            uid = (
                await db.execute(text("SELECT id FROM users WHERE external_ref = :k"), {"k": session_id})
            ).scalar()
            if uid is None:
                continue
            for table in (
                "example_usage_log", "profile_topics", "user_profiles", "session_summaries",
                "persona_assignments",
            ):
                await db.execute(text(f"DELETE FROM {table} WHERE user_id = :uid"), {"uid": uid})
            await db.execute(
                text("DELETE FROM persona_switch_log WHERE session_id IN (SELECT id FROM sessions WHERE user_id = :uid)"),
                {"uid": uid},
            )
            await db.execute(
                text("DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE user_id = :uid)"),
                {"uid": uid},
            )
            await db.execute(text("DELETE FROM sessions WHERE user_id = :uid"), {"uid": uid})
            await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": uid})
        for persona_id in persona_ids:
            await db.execute(
                text("DELETE FROM persona_switch_log WHERE to_persona_id = :p OR from_persona_id = :p"), {"p": persona_id}
            )
            await db.execute(text("DELETE FROM persona_assignments WHERE persona_id = :p"), {"p": persona_id})
            await db.execute(text("UPDATE sessions SET persona_id = NULL WHERE persona_id = :p"), {"p": persona_id})
            await db.execute(text("UPDATE messages SET persona_id = NULL WHERE persona_id = :p"), {"p": persona_id})
            await db.execute(text("DELETE FROM personas WHERE id = :p"), {"p": persona_id})
        await db.commit()


def _create_rule(client: TestClient, action: dict, conditions: dict | None = None, **overrides) -> dict:
    body = {
        "name": overrides.pop("name", "phase4 test rule"),
        "conditions_json": conditions or ACT_RULE_CONDITIONS,
        "action_json": action,
        "status": overrides.pop("status", "active"),
    }
    body.update(overrides)
    resp = client.post("/api/v1/admin/rules", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _patch_rule(client: TestClient, rule_id: str, **changes) -> dict:
    resp = client.patch(f"/api/v1/admin/rules/{rule_id}", json=changes)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create_example(client: TestClient, conditions: dict, content: str) -> str:
    resp = client.post(
        "/api/v1/admin/examples", json={"content": content, "applicable_conditions_json": conditions}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _patch_llm_and_hooks(monkeypatch, reply: str = "我明白，我在這裡陪你。") -> list[dict]:
    captured: list[dict] = []

    def fake_chat_with_llm(user_message, conversation_history=None, **kwargs):
        captured.append(kwargs)
        return reply

    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)
    monkeypatch.setattr("app.routers.chat.process_post_chat_updates", _noop)
    monkeypatch.setattr("app.routers.chat.end_session_and_summarize", _noop)
    return captured


def _chat(client: TestClient, session_id: str, message: str = "最近好緊張") -> None:
    resp = client.post("/api/v1/chat", json={"session_id": session_id, "message": message})
    assert resp.status_code == 200, resp.text


async def _assistant_messages(session_id: str):
    from sqlalchemy import select

    from app.db import get_session
    from app.db.models import ConversationSession, Message

    async with get_session() as db:
        session_row = await db.scalar(
            select(ConversationSession).where(ConversationSession.client_key == session_id)
        )
        return (
            await db.execute(
                select(Message)
                .where(Message.session_id == session_row.id, Message.role == "assistant")
                .order_by(Message.created_at)
            )
        ).scalars().all()


@requires_db
def test_completion_criterion_rule_fires_after_third_mention_and_is_recorded(monkeypatch):
    captured = _patch_llm_and_hooks(monkeypatch)
    client = _client()
    session_id = _new_session_id("act")
    rule_ids: list[str] = []
    try:
        rule = _create_rule(client, {"therapy": "ACT", "tone": "溫暖接納"})
        rule_ids.append(rule["id"])
        assert rule["version_number"] == 1

        _chat(client, session_id)  # 還沒有 profile
        assert captured[-1]["strategy"] is None

        _run(_seed_profile(session_id, {"Anxiety": 2}, risk_level="low"))
        _chat(client, session_id)
        assert captured[-1]["strategy"] is None  # 未達 3 次

        _run(_seed_profile(session_id, {"Anxiety": 3}, risk_level="low"))
        _chat(client, session_id)
        assert captured[-1]["strategy"] == {"therapy": "ACT", "tone": "溫暖接納"}

        versions = client.get(f"/api/v1/admin/rules/{rule['id']}/versions").json()
        messages = _run(_assistant_messages(session_id))
        assert [m.rule_id for m in messages[:2]] == [None, None]
        assert str(messages[2].rule_id) == rule["id"]
        assert str(messages[2].rule_version_id) == versions[0]["id"]
        assert messages[2].output_replaced is False
    finally:
        _run(_cleanup(session_ids=[session_id], rule_ids=rule_ids))


@requires_db
def test_rule_is_recorded_but_flagged_when_output_gateway_replaces_reply(monkeypatch):
    _patch_llm_and_hooks(monkeypatch, reply="你可以試試 Xanax。")
    client = _client()
    session_id = _new_session_id("l3")
    rule_ids: list[str] = []
    try:
        rule_ids.append(_create_rule(client, {"tone": "溫暖"}, scope="immediate")["id"])
        _chat(client, session_id, "哈囉")
        _run(_seed_profile(session_id, {"Anxiety": 3}, risk_level="low"))
        resp = client.post("/api/v1/chat", json={"session_id": session_id, "message": "最近好緊張"})
        assert resp.json()["intercepted"] is True

        last = _run(_assistant_messages(session_id))[-1]
        assert str(last.rule_id) == rule_ids[0]
        assert last.output_replaced is True
    finally:
        _run(_cleanup(session_ids=[session_id], rule_ids=rule_ids))


@requires_db
def test_rule_api_versions_and_validation():
    client = _client()
    rule_ids: list[str] = []
    example_ids: list[str] = []
    try:
        rule = _create_rule(client, {"therapy": "ACT"}, status="draft")
        rule_ids.append(rule["id"])
        assert (rule["status"], rule["version_number"]) == ("draft", 1)

        updated = _patch_rule(client, rule["id"], status="active", change_note="啟用")
        assert (updated["status"], updated["version_number"]) == ("active", 2)

        versions = client.get(f"/api/v1/admin/rules/{rule['id']}/versions").json()
        assert [v["version_number"] for v in versions] == [2, 1]
        assert versions[0]["snapshot_json"]["status"] == "active"
        assert versions[1]["snapshot_json"]["status"] == "draft"
        assert versions[0]["change_note"] == "啟用"

        base = f"/api/v1/admin/rules/{rule['id']}"
        assert client.patch(base, json={}).status_code == 422
        assert client.patch(base, json={"status": "in_review"}).status_code == 422  # 保留給 Phase 5
        assert client.patch(base, json={"conditions_json": {"topics_include": ["社交焦慮"]}}).status_code == 422
        assert client.patch(base, json={"action_json": {"persona_id": str(uuid.uuid4())}}).status_code == 422
        assert client.patch("/api/v1/admin/rules/not-a-uuid", json={"priority": 1}).status_code == 400
        assert client.patch(f"/api/v1/admin/rules/{uuid.uuid4()}", json={"priority": 1}).status_code == 404

        # 引用已封存的範例：草稿可以存，啟用時擋下
        example_id = _create_example(client, {"topics_include": ["phase4-unused"]}, "封存範例")
        example_ids.append(example_id)
        assert client.patch(f"/api/v1/admin/examples/{example_id}", json={"status": "archived"}).status_code == 200
        draft = _create_rule(client, {"example_ids": [example_id]}, status="draft")
        rule_ids.append(draft["id"])
        assert client.patch(f"/api/v1/admin/rules/{draft['id']}", json={"status": "active"}).status_code == 422

        # 失敗的修改不會留下版本
        assert len(client.get(f"/api/v1/admin/rules/{rule['id']}/versions").json()) == 2
    finally:
        _run(_cleanup(rule_ids=rule_ids, example_ids=example_ids))


@requires_db
def test_scope_and_archive_end_to_end(monkeypatch):
    captured = _patch_llm_and_hooks(monkeypatch)
    client = _client()
    sessions = {name: _new_session_id(f"scope-{name}") for name in ("a", "b", "c")}
    rule_ids: list[str] = []

    def tone_for(session_id: str):
        _chat(client, session_id)
        strategy = captured[-1]["strategy"]
        return None if strategy is None else strategy["tone"]

    try:
        rule = _create_rule(client, {"tone": "v1"})  # new_conversations_only
        rule_ids.append(rule["id"])

        _chat(client, sessions["a"], "哈囉")
        _run(_seed_profile(sessions["a"], {"Anxiety": 3}, risk_level="low"))
        assert tone_for(sessions["a"]) == "v1"

        # new_conversations_only 的修改：進行中的 session 沿用舊版，新 session 用新版
        _patch_rule(client, rule["id"], action_json={"tone": "v2"})
        assert tone_for(sessions["a"]) == "v1"
        _chat(client, sessions["b"], "哈囉")
        _run(_seed_profile(sessions["b"], {"Anxiety": 3}, risk_level="low"))
        assert tone_for(sessions["b"]) == "v2"

        # immediate 的修改：進行中的 session 下一輪就用新版
        _patch_rule(client, rule["id"], action_json={"tone": "v3"}, scope="immediate")
        assert tone_for(sessions["a"]) == "v3"

        # 封存一律立即停用（即使改回 new_conversations_only）
        _patch_rule(client, rule["id"], status="archived", scope="new_conversations_only")
        assert tone_for(sessions["a"]) is None
        assert tone_for(sessions["b"]) is None

        # 重新啟用（new_conversations_only）：舊 session 維持停用，新 session 才用新版
        _patch_rule(client, rule["id"], status="active", action_json={"tone": "v6"})
        assert tone_for(sessions["a"]) is None
        _chat(client, sessions["c"], "哈囉")
        _run(_seed_profile(sessions["c"], {"Anxiety": 3}, risk_level="low"))
        assert tone_for(sessions["c"]) == "v6"
    finally:
        _run(_cleanup(session_ids=list(sessions.values()), rule_ids=rule_ids))


@requires_db
def test_persona_priority_manual_assignment_beats_rule_but_strategy_applies(monkeypatch):
    captured = _patch_llm_and_hooks(monkeypatch)
    client = _client()
    session_id = _new_session_id("persona")
    rule_ids: list[str] = []
    persona_ids: list[str] = []
    try:
        persona = client.post(
            "/api/v1/admin/personas",
            json={"name": "Phase4 規則人格", "tone": "冷靜", "system_prompt_fragment": "你是規則指定的人格。"},
        ).json()
        persona_ids.append(persona["id"])
        assert client.patch(f"/api/v1/admin/personas/{persona['id']}/activate").status_code == 200

        rule_ids.append(
            _create_rule(client, {"persona_id": persona["id"], "therapy": "ACT"}, scope="immediate")["id"]
        )
        _chat(client, session_id, "哈囉")
        _run(_seed_profile(session_id, {"Anxiety": 3}, risk_level="low"))
        _chat(client, session_id)
        assert captured[-1]["persona_name"] == "Phase4 規則人格"

        default_persona = next(p for p in client.get("/api/v1/admin/personas").json() if p["is_default"])
        resp = client.post(
            "/api/v1/admin/personas/assign",
            json={
                "session_id": session_id, "persona_id": default_persona["id"],
                "assigned_by": "00000000-0000-0000-0000-000000000001",
            },
        )
        assert resp.status_code == 200, resp.text
        _chat(client, session_id)
        assert captured[-1]["persona_name"] == default_persona["name"]  # 手動指派鎖住 persona
        assert captured[-1]["strategy"] == {"therapy": "ACT", "tone": None}  # 策略仍生效
    finally:
        _run(_cleanup(session_ids=[session_id], rule_ids=rule_ids, persona_ids=persona_ids))


@requires_db
def test_rule_examples_come_first_then_auto_matches_fill(monkeypatch):
    captured = _patch_llm_and_hooks(monkeypatch)
    client = _client()
    session_id = _new_session_id("examples")
    auto_topic = "phase4-auto-" + uuid.uuid4().hex[:8]
    rule_ids: list[str] = []
    example_ids: list[str] = []
    try:
        auto = _create_example(client, {"topics_include": [auto_topic]}, "auto")
        pinned_1 = _create_example(client, {"topics_include": ["phase4-never"]}, "pinned-1")
        pinned_2 = _create_example(client, {"topics_include": ["phase4-never"]}, "pinned-2")
        example_ids += [auto, pinned_1, pinned_2]

        rule_ids.append(
            _create_rule(client, {"example_ids": [pinned_2, pinned_1, pinned_2]}, scope="immediate")["id"]
        )
        _chat(client, session_id, "哈囉")
        _run(_seed_profile(session_id, {"Anxiety": 3, auto_topic: 3}, risk_level="low"))

        _chat(client, session_id)
        # 依 example_ids 順序、去重、不檢查範例自身條件；上限 2 則，自動匹配被擠掉
        assert [e["content"] for e in captured[-1]["examples"]] == ["pinned-2", "pinned-1"]

        # 封存的指定範例不再使用，自動匹配補上
        assert client.patch(f"/api/v1/admin/examples/{pinned_2}", json={"status": "archived"}).status_code == 200
        _chat(client, session_id)
        assert [e["content"] for e in captured[-1]["examples"]] == ["pinned-1", "auto"]
    finally:
        _run(_cleanup(session_ids=[session_id], rule_ids=rule_ids, example_ids=example_ids))


@requires_db
def test_update_committed_after_session_start_is_not_applied_to_that_session(monkeypatch):
    """
    並行更新：v2 的 transaction 在 session 開始前就開始（例如等待 row lock），在
    session 開始後才 commit。v2 是 new_conversations_only，這個 session 必須維持 v1。
    用時間戳判定時，v2 的 created_at（transaction 開始時間）早於 session，會被誤用。
    """
    import psycopg

    captured = _patch_llm_and_hooks(monkeypatch)
    client = _client()
    session_id = _new_session_id("race")
    later_session_id = _new_session_id("race-later")
    rule_ids: list[str] = []
    try:
        rule = _create_rule(client, {"tone": "v1"})  # new_conversations_only
        rule_ids.append(rule["id"])

        # 另一個連線開始寫 v2，但先不 commit
        sync_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(sync_url) as writer:
            with writer.cursor() as cur:
                # 跟 PATCH /rules 一樣拿 FOR NO KEY UPDATE（不擋 messages.rule_id 的外鍵檢查）
                cur.execute("SELECT id FROM rules WHERE id = %s FOR NO KEY UPDATE", (rule["id"],))
                snapshot = {
                    "name": rule["name"], "conditions_json": ACT_RULE_CONDITIONS,
                    "action_json": {"tone": "v2"}, "priority": 0,
                    "scope": "new_conversations_only", "status": "active",
                }
                cur.execute(
                    "UPDATE rules SET action_json = %s::jsonb, updated_at = now() WHERE id = %s",
                    (psycopg.types.json.Json({"tone": "v2"}), rule["id"]),
                )
                cur.execute(
                    "INSERT INTO rule_versions (rule_id, version_number, snapshot_json, changed_by) "
                    "VALUES (%s, 2, %s::jsonb, '00000000-0000-0000-0000-000000000001')",
                    (rule["id"], psycopg.types.json.Json(snapshot)),
                )

                # v2 尚未 commit 時開始新 session，首輪只看得到 v1
                _chat(client, session_id, "哈囉")
                _run(_seed_profile(session_id, {"Anxiety": 3}, risk_level="low"))
                _chat(client, session_id)
                assert captured[-1]["strategy"]["tone"] == "v1"

            writer.commit()

        # v2 commit 後，這個 session 仍維持 v1；之後開始的 session 才用 v2
        _chat(client, session_id)
        assert captured[-1]["strategy"]["tone"] == "v1"

        _chat(client, later_session_id, "哈囉")
        _run(_seed_profile(later_session_id, {"Anxiety": 3}, risk_level="low"))
        _chat(client, later_session_id)
        assert captured[-1]["strategy"]["tone"] == "v2"
    finally:
        _run(_cleanup(session_ids=[session_id, later_session_id], rule_ids=rule_ids))
