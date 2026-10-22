"""
Phase 5 (HITL) tests — 沙盒、雙人審核、回滾、手動覆寫、回饋標籤。
（舊的 tests/test_phase5.py 是安全層 Layer 1b/1c 測試，與這裡無關。）

DB 測試需要真實 Postgres：把 DATABASE_URL 設成本機 Docker（例如 :5433），不要指到 Neon。
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.rule_review import REVIEWER_ROLES, content_hash, content_of, get_review_mode
from app.main import app

requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="Phase 5 DB tests need a real Postgres (set DATABASE_URL, e.g. local Docker on :5433)",
)

KEY = "phase5-test-admin-key"
BASE = "/api/v1/admin"
COND = {"topics_include": ["Anxiety"], "risk_level": "low", "min_topic_mentions": 3}
ACT = {"therapy": "ACT", "tone": "warm"}
CBT = {"therapy": "CBT", "tone": "warm"}
PLACEHOLDER = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", KEY)
    monkeypatch.setenv("RULE_REVIEW_MODE", "dual")


def _client() -> TestClient:
    return TestClient(app, headers={"X-Admin-Key": KEY})


def _run(coro):
    return asyncio.run(coro)


# ── 1. 不需要資料庫 ───────────────────────────────────────────────────────────

def test_review_mode_defaults_to_dual(monkeypatch):
    monkeypatch.delenv("RULE_REVIEW_MODE", raising=False)
    assert get_review_mode() == "dual"
    monkeypatch.setenv("RULE_REVIEW_MODE", "single")
    assert get_review_mode() == "single"
    monkeypatch.setenv("RULE_REVIEW_MODE", "Singel")  # 打錯字不能意外降級成 single
    assert get_review_mode() == "dual"


def test_content_hash_ignores_key_order_and_detects_changes():
    a = {"name": "r", "conditions_json": {"a": 1, "b": 2}, "action_json": ACT, "priority": 0, "scope": "immediate"}
    b = {**a, "conditions_json": {"b": 2, "a": 1}}
    assert content_hash(a) == content_hash(b)
    assert content_hash(a) != content_hash({**a, "priority": 1})
    assert content_hash(a) != content_hash({**a, "action_json": CBT})
    assert set(content_of({**a, "status": "active"})) == set(a)
    assert "senior_therapist" in REVIEWER_ROLES and "admin" in REVIEWER_ROLES


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/sessions"),
        ("get", "/messages?session_id=x"),
        ("get", "/overrides"),
        ("get", "/feedback-tags"),
        ("get", "/therapists"),
        ("post", f"/messages/{uuid.uuid4()}/override"),
        ("post", f"/messages/{uuid.uuid4()}/tags"),
        ("post", f"/rules/{uuid.uuid4()}/test"),
        ("post", f"/rules/{uuid.uuid4()}/rollback"),
        ("post", f"/review-requests/{uuid.uuid4()}/approve"),
    ],
)
def test_conversation_and_review_endpoints_require_admin_key(method, path):
    resp = getattr(TestClient(app), method)(BASE + path, **({"json": {}} if method == "post" else {}))
    assert resp.status_code == 401


def test_tag_vocabulary_rejects_free_text():
    resp = _client().post(f"{BASE}/messages/{uuid.uuid4()}/tags", json={"tags": ["隨便寫的標籤"]})
    assert resp.status_code == 422


def test_public_lists_stay_public():
    assert TestClient(app).get(f"{BASE}/review-requests").status_code in (200, 503)


# ── 2. 需要資料庫 ─────────────────────────────────────────────────────────────

def _therapist(client, role: str) -> str:
    resp = client.post(
        f"{BASE}/therapists",
        json={"name": f"p5-{role}", "email": f"p5-{uuid.uuid4().hex[:8]}@example.test", "role": role},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _stub_llm(monkeypatch, reply="這是沙盒回覆"):
    calls: list[dict] = []

    def fake(user_message, conversation_history=None, **kwargs):
        calls.append(kwargs)
        return reply

    monkeypatch.setattr("app.core.rule_sandbox.chat_with_llm", fake)
    return calls


async def _cleanup(rule_ids=(), therapist_ids=(), session_ids=()) -> None:
    from sqlalchemy import text

    from app.db import get_session

    async with get_session() as db:
        for sid in session_ids:
            await db.execute(text("DELETE FROM sessions WHERE client_key = :k"), {"k": sid})
            await db.execute(text("DELETE FROM users WHERE external_ref = :k"), {"k": sid})
        for rid in rule_ids:
            for table in ("rule_review_requests", "rule_test_runs"):
                await db.execute(text(f"DELETE FROM {table} WHERE rule_id = :id"), {"id": rid})
            await db.execute(text("DELETE FROM rules WHERE id = :id"), {"id": rid})
        for tid in therapist_ids:
            for table, col in (
                ("manual_overrides", "overridden_by"), ("feedback_tags", "tagged_by"),
            ):
                await db.execute(text(f"DELETE FROM {table} WHERE {col} = :id"), {"id": tid})
            await db.execute(text("DELETE FROM therapists WHERE id = :id"), {"id": tid})
        await db.commit()


@pytest.fixture
def world():
    """兩位治療師 + 一位 senior，結束時把測試資料清掉。"""
    state = {"rules": [], "therapists": [], "sessions": []}
    yield state
    _run(_cleanup(state["rules"], state["therapists"], state["sessions"]))


def _rule(client, rid) -> dict:
    return next(r for r in client.get(f"{BASE}/rules").json() if r["id"] == rid)


def _draft(client, world, action=ACT, **extra) -> str:
    resp = client.post(
        f"{BASE}/rules",
        json={"name": "p5 rule", "conditions_json": COND, "action_json": action, "status": "draft", **extra},
    )
    assert resp.status_code == 200, resp.text
    rid = resp.json()["id"]
    world["rules"].append(rid)
    return rid


def _test_current(client, rid, monkeypatch, config=None):
    body = {"turns": [{"role": "user", "content": "我最近很焦慮"}]}
    if config:
        body["config"] = config
    resp = client.post(f"{BASE}/rules/{rid}/test", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


@requires_db
def test_dual_mode_blocks_direct_activation_and_requires_review(monkeypatch, world):
    _stub_llm(monkeypatch)
    c = _client()
    submitter = _therapist(c, "therapist")
    reviewer = _therapist(c, "senior_therapist")
    plain = _therapist(c, "therapist")
    world["therapists"] += [submitter, reviewer, plain]

    assert c.post(f"{BASE}/rules", json={
        "name": "x", "conditions_json": COND, "action_json": ACT, "status": "active"}).status_code == 409
    rid = _draft(c, world)
    assert c.patch(f"{BASE}/rules/{rid}", json={"status": "active"}).status_code == 409

    # 沒測過當前版本 → 不能送審
    submit = {"submitted_by": submitter}
    assert c.post(f"{BASE}/rules/{rid}/submit-review", json=submit).status_code == 409
    run = _test_current(c, rid, monkeypatch)
    assert run["safety_outcome"] and run["rule_version_id"]

    resp = c.post(f"{BASE}/rules/{rid}/submit-review", json=submit)
    assert resp.status_code == 200, resp.text
    req = resp.json()
    assert _rule(c, rid)["status"] == "in_review"
    # 第二份申請被擋
    assert c.post(f"{BASE}/rules/{rid}/submit-review", json=submit).status_code == 409

    approve = f"{BASE}/review-requests/{req['id']}/approve"
    assert c.post(approve, json={"reviewer_id": submitter}).status_code in (403, 409, 422)  # 不能自審
    assert c.post(approve, json={"reviewer_id": plain}).status_code in (403, 409, 422)  # 角色不足
    ok = c.post(approve, json={"reviewer_id": reviewer})
    assert ok.status_code == 200, ok.text
    assert _rule(c, rid)["status"] == "active"


@requires_db
def test_reject_returns_to_draft_and_can_resubmit(monkeypatch, world):
    _stub_llm(monkeypatch)
    c = _client()
    submitter, reviewer = _therapist(c, "therapist"), _therapist(c, "admin")
    world["therapists"] += [submitter, reviewer]
    rid = _draft(c, world)
    _test_current(c, rid, monkeypatch)
    req = c.post(f"{BASE}/rules/{rid}/submit-review", json={"submitted_by": submitter}).json()

    assert c.post(f"{BASE}/review-requests/{req['id']}/reject", json={"reviewer_id": reviewer}).status_code == 422
    rej = c.post(f"{BASE}/review-requests/{req['id']}/reject", json={"reviewer_id": reviewer, "note": "語氣太硬"})
    assert rej.status_code == 200, rej.text
    assert _rule(c, rid)["status"] == "draft"
    again = c.post(f"{BASE}/rules/{rid}/submit-review", json={"submitted_by": submitter})
    assert again.status_code == 200, again.text
    old = c.get(f"{BASE}/review-requests", params={"rule_id": rid, "status": "rejected"}).json()
    assert old and old[0]["review_note"] == "語氣太硬"


@requires_db
def test_active_rule_edit_goes_through_review_and_rollback(monkeypatch, world):
    _stub_llm(monkeypatch)
    c = _client()
    submitter, reviewer = _therapist(c, "therapist"), _therapist(c, "senior_therapist")
    world["therapists"] += [submitter, reviewer]
    rid = _draft(c, world)

    def publish(config=None):
        _test_current(c, rid, monkeypatch, config)
        body = {"submitted_by": submitter}
        if config:
            body["proposed"] = config
        req = c.post(f"{BASE}/rules/{rid}/submit-review", json=body)
        assert req.status_code == 200, req.text
        ok = c.post(f"{BASE}/review-requests/{req.json()['id']}/approve", json={"reviewer_id": reviewer})
        assert ok.status_code == 200, ok.text

    publish()
    assert c.patch(f"{BASE}/rules/{rid}", json={"action_json": CBT}).status_code == 409  # 不能繞過審核
    publish({"action_json": CBT})
    rule = _rule(c, rid)
    assert rule["status"] == "active" and rule["action_json"] == CBT

    rb = c.post(f"{BASE}/rules/{rid}/rollback", json={"changed_by": reviewer, "reason": "CBT 效果不好"})
    assert rb.status_code == 200, rb.text
    assert rb.json()["action_json"] == ACT
    versions = c.get(f"{BASE}/rules/{rid}/versions").json()
    assert [v["version_number"] for v in versions][-1] >= 3 or len(versions) >= 3  # 歷史沒被覆寫

    # 封存立即生效，且不能回滾已封存規則
    assert c.patch(f"{BASE}/rules/{rid}", json={"status": "archived"}).status_code == 200
    assert c.post(f"{BASE}/rules/{rid}/rollback", json={"changed_by": reviewer, "reason": "x"}).status_code == 409


@requires_db
def test_single_mode_keeps_phase4_behaviour(monkeypatch, world):
    monkeypatch.setenv("RULE_REVIEW_MODE", "single")
    c = _client()
    resp = c.post(f"{BASE}/rules", json={"name": "s", "conditions_json": COND, "action_json": ACT, "status": "active"})
    assert resp.status_code == 200, resp.text
    world["rules"].append(resp.json()["id"])


@requires_db
def test_sandbox_runs_real_pipeline_without_writing_messages(monkeypatch, world):
    calls = _stub_llm(monkeypatch)
    c = _client()
    rid = _draft(c, world)
    resp = c.post(f"{BASE}/rules/{rid}/test", json={
        "turns": [{"role": "user", "content": "我最近很焦慮"}],
        "simulated_profile": {"risk_level": "low", "topic_counts": {"Anxiety": 3}},
    })
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["profile_match"] is True and calls
    miss = c.post(f"{BASE}/rules/{rid}/test", json={
        "turns": [{"role": "user", "content": "我最近很焦慮"}],
        "simulated_profile": {"risk_level": "high", "topic_counts": {"Anxiety": 3}},
    }).json()
    assert miss["profile_match"] is False
    runs = c.get(f"{BASE}/rules/{rid}/test-runs").json()
    assert len(runs) == 2
    # 最後一輪必須是 user
    bad = c.post(f"{BASE}/rules/{rid}/test", json={"turns": [{"role": "assistant", "content": "hi"}]})
    assert bad.status_code == 422


@requires_db
def test_sandbox_rate_limit(monkeypatch, world):
    _stub_llm(monkeypatch)
    monkeypatch.setenv("SANDBOX_MAX_RUNS_PER_HOUR", "1")
    c = _client()
    rid = _draft(c, world)
    body = {"turns": [{"role": "user", "content": "hi"}]}
    first = c.post(f"{BASE}/rules/{rid}/test", json=body)
    second = c.post(f"{BASE}/rules/{rid}/test", json=body)
    assert first.status_code == 200 and second.status_code == 429


async def _seed_chat(world) -> tuple[str, str]:
    from app.db import get_session
    from app.db.models import ConversationSession, Message, User

    sid = f"phase5-test-{uuid.uuid4().hex[:10]}"
    world["sessions"].append(sid)
    async with get_session() as db:
        user = User(external_ref=sid)
        db.add(user)
        await db.flush()
        session = ConversationSession(user_id=user.id, client_key=sid)
        db.add(session)
        await db.flush()
        db.add(Message(session_id=session.id, role="user", content="你好"))
        reply = Message(session_id=session.id, role="assistant", content="原本的回覆")
        db.add(reply)
        await db.flush()
        await db.commit()
        return sid, str(reply.id)


@requires_db
def test_override_and_tags_are_post_hoc_only(world):
    c = _client()
    therapist = _therapist(c, "therapist")
    world["therapists"].append(therapist)
    sid, reply_id = _run(_seed_chat(world))

    msgs = c.get(f"{BASE}/messages", params={"session_id": sid}).json()
    user_msg = next(m for m in msgs if m["role"] == "user")
    assert c.post(f"{BASE}/messages/{user_msg['id']}/override",
                  json={"overridden_reply": "x"}).status_code == 422  # 只能標 assistant

    ov = c.post(f"{BASE}/messages/{reply_id}/override", json={
        "overridden_reply": "更好的回覆", "overridden_by": therapist, "used_for_training": False})
    assert ov.status_code == 200, ov.text
    assert ov.json()["original_reply"] == "原本的回覆"
    upd = c.patch(f"{BASE}/overrides/{ov.json()['id']}", json={"used_for_training": True})
    assert upd.json()["used_for_training"] is True
    assert [o["id"] for o in c.get(f"{BASE}/overrides", params={"used_for_training": True}).json()] == [ov.json()["id"]]

    tags = c.post(f"{BASE}/messages/{reply_id}/tags", json={"tags": ["太正式", "缺乏同理心"], "tagged_by": therapist})
    assert tags.status_code == 200 and len(tags.json()) == 2
    dup = c.post(f"{BASE}/messages/{reply_id}/tags", json={"tags": ["太正式"], "tagged_by": therapist})
    assert dup.json() == []

    after = c.get(f"{BASE}/messages", params={"session_id": sid}).json()
    reply = next(m for m in after if m["id"] == reply_id)
    assert reply["content"] == "原本的回覆"  # 訊息本身不變
    assert reply["tags"] == ["缺乏同理心", "太正式"] or set(reply["tags"]) == {"太正式", "缺乏同理心"}
    assert len(reply["overrides"]) == 1

