"""
Phase 6 tests — 危機分層（risk_events、通知、tier 3 鎖定、後台 API）。

DB 測試需要真實 Postgres：把 DATABASE_URL 設成本機 Docker（例如 :5433），不要指到 Neon。
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import crisis_notifier
from app.core.crisis_handler import get_crisis_response, get_referral_response
from app.main import app

requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="Phase 6 DB tests need a real Postgres (set DATABASE_URL, e.g. local Docker on :5433)",
)

KEY = "phase6-test-admin-key"
BASE = "/api/v1/admin"
MOCK_CONTACT = "電話 0000 0000（測試用 mock）"
CRISIS_MSG = "我想死"
SECRET = "我想死，我藏了一個獨特的秘密字串XYZZY"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", KEY)
    for k in ("COUNSELING_CENTER_CONTACT", "CRISIS_NOTIFY_EMAIL_TO", "SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)


# ── 1. 不需要資料庫 ───────────────────────────────────────────────────────────

def test_no_contact_line_without_env_even_at_tier_3():
    for tier in (1, 2, 3):
        assert "輔導中心" not in get_crisis_response(CRISIS_MSG, tier=tier)
    assert "輔導中心" not in get_referral_response("hello")
    assert "Counselling" not in get_crisis_response("I want to die", tier=3)


def test_contact_line_from_env(monkeypatch):
    monkeypatch.setenv("COUNSELING_CENTER_CONTACT", MOCK_CONTACT)
    assert MOCK_CONTACT not in get_crisis_response(CRISIS_MSG, tier=1)
    assert MOCK_CONTACT in get_crisis_response(CRISIS_MSG, tier=2)
    assert MOCK_CONTACT in get_referral_response("hi")


def test_notified_sentence_only_when_really_sent_and_never_claims_a_human_took_over():
    sent = get_crisis_response(CRISIS_MSG, tier=2, notified=True)
    not_sent = get_crisis_response(CRISIS_MSG, tier=2, notified=False)
    assert "辦公時間跟進" in sent and "24 小時" in sent
    assert "通知" not in not_sent
    for text in (sent, not_sent, get_referral_response("hi"), get_crisis_response("I want to die", tier=3, notified=True)):
        assert "轉接" not in text and "connected you" not in text


def test_notification_email_has_no_student_text(monkeypatch):
    monkeypatch.setenv("CRISIS_NOTIFY_EMAIL_TO", "a@example.test, b@example.test")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    msg = crisis_notifier.build_message("evt-123", 2, datetime(2026, 10, 23, tzinfo=timezone.utc))
    blob = msg["Subject"] + msg.get_content()
    assert "evt-123" in blob and "tier 2" in blob
    assert "死" not in blob and "XYZZY" not in blob
    assert msg["To"] == "a@example.test, b@example.test"


def test_notifier_unconfigured_returns_false_and_failure_never_raises(monkeypatch):
    now = datetime.now(timezone.utc)
    assert _run(crisis_notifier.send_crisis_notification("e", 2, now)) is False

    monkeypatch.setenv("CRISIS_NOTIFY_EMAIL_TO", "a@example.test")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")

    def boom(msg):
        raise OSError("smtp down")

    monkeypatch.setattr(crisis_notifier, "_send_blocking", boom)
    assert _run(crisis_notifier.send_crisis_notification("e", 2, now)) is False

    monkeypatch.setattr(crisis_notifier, "_send_blocking", lambda msg: None)
    assert _run(crisis_notifier.send_crisis_notification("e", 2, now)) is True


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/risk-events"),
        ("get", f"/risk-events/users/{uuid.uuid4()}/timeline"),
        ("post", f"/risk-events/{uuid.uuid4()}/resolve"),
    ],
)
def test_risk_admin_endpoints_require_key(method, path):
    resp = getattr(TestClient(app), method)(BASE + path, **({"json": {}} if method == "post" else {}))
    assert resp.status_code == 401


def test_no_delete_or_edit_endpoints():
    paths = {(m, r.path) for r in app.routes if hasattr(r, "methods") for m in r.methods}
    risk = [(m, p) for m, p in paths if "risk-events" in p]
    assert not [1 for m, _ in risk if m in ("DELETE", "PUT", "PATCH")]


# ── 2. 需要資料庫 ─────────────────────────────────────────────────────────────

class _Env:
    def __init__(self):
        self.sessions: list[str] = []
        self.therapists: list[str] = []
        self.llm_calls: list[dict] = []
        self.notifications: list[tuple] = []

    def new_session(self) -> str:
        sid = f"p6-{uuid.uuid4().hex}"
        self.sessions.append(sid)
        return sid


async def _cleanup(env: _Env) -> None:
    from sqlalchemy import text

    from app.db import get_session

    async with get_session() as db:
        await db.execute(text("ALTER TABLE risk_events DISABLE TRIGGER risk_events_guard_trg"))
        try:
            for sid in env.sessions:
                await db.execute(
                    text("DELETE FROM risk_events WHERE user_id IN (SELECT id FROM users WHERE external_ref = :k)"),
                    {"k": sid},
                )
                await db.execute(text("DELETE FROM sessions WHERE client_key = :k"), {"k": sid})
                await db.execute(text("DELETE FROM users WHERE external_ref = :k"), {"k": sid})
            for tid in env.therapists:
                await db.execute(text("DELETE FROM risk_events WHERE resolved_by = :t"), {"t": tid})
                await db.execute(text("DELETE FROM therapists WHERE id = :t"), {"t": tid})
            await db.commit()
        finally:
            await db.execute(text("ALTER TABLE risk_events ENABLE TRIGGER risk_events_guard_trg"))
            await db.commit()


@pytest.fixture
def world(monkeypatch):
    env = _Env()

    def fake_llm(user_message, conversation_history=None, **kwargs):
        env.llm_calls.append(kwargs)
        return "一般回覆"

    async def noop(*a, **k):
        return None

    async def fake_notify(event_id, tier, created_at):
        env.notifications.append((event_id, tier, created_at))
        return True

    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_llm)
    monkeypatch.setattr("app.routers.chat.process_post_chat_updates", noop)
    monkeypatch.setattr("app.routers.chat.end_session_and_summarize", noop)
    monkeypatch.setattr("app.core.risk_escalation.send_crisis_notification", fake_notify)
    yield env
    _run(_cleanup(env))


def _client() -> TestClient:
    return TestClient(app, headers={"X-Admin-Key": KEY})


def _chat(client, sid, msg):
    resp = client.post("/api/v1/chat", json={"message": msg, "session_id": sid})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _events(sid: str) -> list[dict]:
    from sqlalchemy import text

    from app.db import get_session

    async with get_session() as db:
        rows = await db.execute(
            text(
                "SELECT e.risk_tier, e.session_id, e.message_id, e.trigger_snippet, e.supervisor_notified_at "
                "FROM risk_events e JOIN users u ON u.id = e.user_id WHERE u.external_ref = :k "
                "ORDER BY e.created_at"
            ),
            {"k": sid},
        )
        return [dict(r._mapping) for r in rows]


@requires_db
def test_three_triggers_give_three_different_behaviours(world, monkeypatch):
    monkeypatch.setenv("COUNSELING_CENTER_CONTACT", MOCK_CONTACT)
    client = _client()
    sid = world.new_session()

    r1 = _chat(client, sid, SECRET)
    r2 = _chat(client, sid, CRISIS_MSG)
    r3 = _chat(client, sid, CRISIS_MSG)
    assert r1["crisis"] and r2["crisis"] and r3["crisis"]

    assert MOCK_CONTACT not in r1["reply"]
    assert MOCK_CONTACT in r2["reply"] and "辦公時間跟進" in r2["reply"]
    assert "生命熱線" in r1["reply"] and "生命熱線" in r2["reply"]

    events = _run(_events(sid))
    assert [e["risk_tier"] for e in events] == [1, 2, 3]
    assert events[0]["supervisor_notified_at"] is None
    assert events[1]["supervisor_notified_at"] is not None
    assert [n[1] for n in world.notifications] == [2, 3]
    assert all(e["message_id"] is not None for e in events)
    # 通知只帶 event id / tier / 時間，不帶原文
    assert all(SECRET not in str(n) for n in world.notifications)

    # tier 3 之後：AI 不再回覆，固定轉介訊息，不呼叫 LLM
    r4 = _chat(client, sid, "今天天氣不錯")
    assert "不會再以 AI 身份" in r4["reply"] and MOCK_CONTACT in r4["reply"]
    assert world.llm_calls == []
    # 之後再觸發仍記為 tier 3
    _chat(client, sid, CRISIS_MSG)
    assert [e["risk_tier"] for e in _run(_events(sid))] == [1, 2, 3, 3]


@requires_db
def test_followup_hint_only_after_a_risk_event(world):
    client = _client()
    sid = world.new_session()
    _chat(client, sid, "今天有點累")
    assert world.llm_calls[-1]["risk_followup"] is False
    _chat(client, sid, CRISIS_MSG)
    _chat(client, sid, "謝謝你")
    assert world.llm_calls[-1]["risk_followup"] is True


def test_followup_block_sits_before_safety_core():
    from app.prompts.system_prompt import RISK_FOLLOWUP_BLOCK, SAFETY_CORE, build_prompt

    prompt = build_prompt("x", risk_followup=True)
    assert prompt.index(RISK_FOLLOWUP_BLOCK) < prompt.index(SAFETY_CORE)
    assert SAFETY_CORE in prompt
    assert RISK_FOLLOWUP_BLOCK not in build_prompt("x")


@requires_db
def test_risk_events_survive_reset(world):
    client = _client()
    sid = world.new_session()
    _chat(client, sid, CRISIS_MSG)
    assert client.post("/api/v1/reset", json={"session_id": sid}).status_code == 200

    events = _run(_events(sid))
    assert len(events) == 1
    assert events[0]["session_id"] is None and events[0]["message_id"] is None
    assert events[0]["trigger_snippet"] == CRISIS_MSG

    # 新 session（重新開始）從 tier 1 起算
    assert _chat(client, sid, CRISIS_MSG)["crisis"] is True
    assert [e["risk_tier"] for e in _run(_events(sid))] == [1, 1]


@requires_db
def test_db_failure_still_returns_hotline(world, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    with monkeypatch.context() as m:
        m.setattr("app.db.get_session", boom)
        m.setattr("app.storage.postgres_store.get_session", boom)
        resp = TestClient(app).post(
            "/api/v1/chat", json={"message": CRISIS_MSG, "session_id": world.new_session()}
        )
    assert resp.status_code == 200
    assert resp.json()["crisis"] is True and "生命熱線" in resp.json()["reply"]


@requires_db
def test_notification_failure_still_returns_hotline(world, monkeypatch):
    async def fail(*a, **k):
        return False

    monkeypatch.setattr("app.core.risk_escalation.send_crisis_notification", fail)
    client = _client()
    sid = world.new_session()
    _chat(client, sid, CRISIS_MSG)
    r2 = _chat(client, sid, CRISIS_MSG)
    assert "生命熱線" in r2["reply"] and "通知" not in r2["reply"]
    assert _run(_events(sid))[1]["supervisor_notified_at"] is None


@requires_db
def test_db_trigger_blocks_delete_and_edits_but_allows_resolve(world):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.db import get_session

    client = _client()
    sid = world.new_session()
    _chat(client, sid, CRISIS_MSG)

    async def attempt(sql: str) -> bool:
        async with get_session() as db:
            try:
                await db.execute(text(sql), {"k": sid})
                await db.commit()
                return True
            except DBAPIError:
                await db.rollback()
                return False

    sub = "(SELECT id FROM users WHERE external_ref = :k)"
    assert not _run(attempt(f"DELETE FROM risk_events WHERE user_id = {sub}"))
    assert not _run(attempt(f"UPDATE risk_events SET risk_tier = 3 WHERE user_id = {sub}"))
    assert not _run(attempt(f"UPDATE risk_events SET trigger_snippet = 'x' WHERE user_id = {sub}"))
    assert not _run(attempt(f"UPDATE risk_events SET session_id = gen_random_uuid() WHERE user_id = {sub}"))
    assert _run(attempt(f"UPDATE risk_events SET session_id = NULL WHERE user_id = {sub}"))


@requires_db
def test_admin_list_timeline_and_resolve_once(world):
    client = _client()
    t = client.post(
        f"{BASE}/therapists",
        json={"name": "p6", "email": f"p6-{uuid.uuid4().hex[:8]}@example.test", "role": "therapist"},
    )
    assert t.status_code == 200, t.text
    tid = t.json()["id"]
    world.therapists.append(tid)

    sid = world.new_session()
    _chat(client, sid, CRISIS_MSG)
    _chat(client, sid, CRISIS_MSG)

    async def _uid() -> str:
        from sqlalchemy import text

        from app.db import get_session

        async with get_session() as db:
            return str(await db.scalar(text("SELECT id FROM users WHERE external_ref = :k"), {"k": sid}))

    user_id = _run(_uid())
    timeline = client.get(f"{BASE}/risk-events/users/{user_id}/timeline").json()
    assert [e["risk_tier"] for e in timeline] == [1, 2]

    eid = timeline[0]["id"]
    ok = client.post(f"{BASE}/risk-events/{eid}/resolve", json={"resolved_by": tid, "resolution_note": "已聯絡"})
    assert ok.status_code == 200 and ok.json()["resolved_by"] == tid and ok.json()["resolved_at"]
    again = client.post(f"{BASE}/risk-events/{eid}/resolve", json={"resolved_by": tid, "resolution_note": "覆寫"})
    assert again.status_code == 409
    assert client.post(
        f"{BASE}/risk-events/{uuid.uuid4()}/resolve", json={"resolved_by": tid, "resolution_note": "x"}
    ).status_code == 404

    still_open = client.get(f"{BASE}/risk-events", params={"unresolved": "true"}).json()
    assert eid not in [e["id"] for e in still_open]
