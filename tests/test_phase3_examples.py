"""
Phase 3 tests — 範例庫（T-Q16–T-Q20）。

分兩部分：
1. 不需要資料庫的單元測試（prompt 組裝、共用條件比對、InMemory append 回傳值、
   /chat 的引用標記邏輯）——Example Selector 用 monkeypatch 換掉，`pytest tests/`
   預設（無 DB）就會跑。
2. 需要真實 Postgres 的整合測試（DATABASE_URL 未設定時跳過）：範例選取與排序上限、
   example_usage_log 寫入、/reset 後使用記錄因 ON DELETE SET NULL 保留、Admin CRUD API。

跟 test_phase2_profiles.py 一樣刻意不呼叫 load_dotenv()（見該檔說明），也不依賴
pytest-asyncio，async 部分用 asyncio.run() 包起來。

response_examples 是全域資料（不屬於特定使用者），DB 測試一律用每支測試獨有的
主題名稱當條件，並在 cleanup 刪掉自己種的範例，避免殘留的 active 範例命中其他測試。
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi.testclient import TestClient

from app.core import condition_matching, persona_resolver
from app.core.example_selector import EMPTY_SELECTION, ExampleSelection, SelectedExample
from app.main import app
from app.prompts.system_prompt import SAFETY_CORE, build_prompt
from app.routers.chat import ATTRIBUTION_TAG
from app.storage.in_memory_store import InMemoryConversationStore

requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="Phase 3 DB tests need a real Postgres (set DATABASE_URL, e.g. local Docker on :5433)",
)


async def _noop(*args, **kwargs) -> None:
    return None


# Phase 4 起 admin 寫入端點要求 X-Admin-Key（app/core/admin_auth.py）
TEST_ADMIN_KEY = "phase3-test-admin-key"


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", TEST_ADMIN_KEY)


def _client() -> TestClient:
    return TestClient(app, headers={"X-Admin-Key": TEST_ADMIN_KEY})


def _selection(*examples: tuple[str, str]) -> ExampleSelection:
    return ExampleSelection(
        user_id=None,
        examples=[
            SelectedExample(
                id=str(uuid.uuid4()), content=f"範例內容 {i}",
                usage_mode=usage_mode, attribution_mode=attribution_mode,
            )
            for i, (usage_mode, attribution_mode) in enumerate(examples)
        ],
    )


# ── 1. 不需要資料庫 ───────────────────────────────────────────────────────────

def test_build_prompt_without_examples_is_unchanged():
    assert build_prompt("hi") == build_prompt("hi", examples=None) == build_prompt("hi", examples=[])


def test_examples_block_sits_between_context_and_safety_core():
    prompt = build_prompt(
        "hi",
        profile_text="[STUDENT PROFILE]",
        examples=[
            {"usage_mode": "style_learning", "content": "風格範例"},
            {"usage_mode": "direct_quote", "content": "引用範例"},
        ],
    )
    block_at = prompt.index("[CLINICAL RESPONSE EXAMPLES]")
    assert prompt.index("[STUDENT PROFILE]") < block_at < prompt.index(SAFETY_CORE)
    # SAFETY_CORE 完整保留、只出現一次
    assert prompt.count(SAFETY_CORE) == 1

    block = prompt[block_at:prompt.index(SAFETY_CORE)]
    assert "Example 1 (Style Learning Reference)" in block
    assert "Do not copy its words verbatim" in block
    assert "Example 2 (Direct Phrasing Reference)" in block
    assert "風格範例" in block and "引用範例" in block
    # 引用改由 chat.py 決定性附加，prompt 裡不要求 LLM 自己帶出來源
    assert "{idx}" not in block and "臨床心理師" not in block


def test_persona_resolver_and_example_selector_share_condition_matching():
    from app.core import example_selector

    assert persona_resolver._condition_matches is condition_matching.condition_matches
    assert example_selector.condition_matches is condition_matching.condition_matches
    assert persona_resolver.EVOLVED_TOPIC_THRESHOLD == condition_matching.EVOLVED_TOPIC_THRESHOLD


@pytest.mark.parametrize(
    "conditions",
    [
        {},
        {"topics_include": "Relationship"},  # 字串不是 list，會被逐字元比對、永遠不命中
        {"topics_includ": ["Relationship"]},  # 打錯字的鍵
        {"topics_include": []},
        {"topics_include": ["ok", ""]},
        {"year_of_study": 1},
    ],
)
def test_validate_condition_json_rejects_never_matching_conditions(conditions):
    with pytest.raises(ValueError):
        condition_matching.validate_condition_json(conditions)


def test_validate_condition_json_accepts_supported_keys():
    conditions = {"year_of_study": "Year 1", "topics_include": ["Relationship"]}
    assert condition_matching.validate_condition_json(conditions) == conditions


def test_selector_fails_open_when_query_errors(monkeypatch):
    from app.core import example_selector

    async def broken_select(session_id):
        raise RuntimeError('relation "response_examples" does not exist')

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused")
    monkeypatch.setattr(example_selector, "_select", broken_select)
    assert asyncio.run(example_selector.select_applicable_examples("any-session")) == EMPTY_SELECTION


def test_in_memory_append_returns_none():
    store = InMemoryConversationStore()
    assert asyncio.run(store.append("in-memory-append-test", "assistant", "hello")) is None


def _patch_chat_pipeline(monkeypatch, selection: ExampleSelection, llm_reply: str, store=None):
    captured: list[dict] = []

    def fake_chat_with_llm(user_message, conversation_history=None, **kwargs):
        captured.append(kwargs)
        return llm_reply

    async def fake_select(session_id, **kwargs):
        return selection

    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)
    monkeypatch.setattr("app.routers.chat.select_applicable_examples", fake_select)
    monkeypatch.setattr("app.routers.chat.log_example_usage", _noop)
    monkeypatch.setattr("app.routers.chat.process_post_chat_updates", _noop)
    if store is not None:
        monkeypatch.setattr("app.routers.chat.conversation_store", store)
    return captured


def test_attributed_direct_quote_appends_tag_once(monkeypatch):
    captured = _patch_chat_pipeline(
        monkeypatch,
        _selection(("direct_quote", "attributed"), ("direct_quote", "attributed")),
        "我明白你的感受。",
    )
    resp = TestClient(app).post("/api/v1/chat", json={"message": "最近好累"})
    assert resp.status_code == 200
    reply = resp.json()["reply"]
    assert reply.endswith(ATTRIBUTION_TAG)
    assert reply.count(ATTRIBUTION_TAG.strip()) == 1
    assert [ex["usage_mode"] for ex in captured[-1]["examples"]] == ["direct_quote", "direct_quote"]


@pytest.mark.parametrize(
    "examples",
    [
        (("style_learning", "attributed"),),
        (("style_learning", "anonymous"),),
        (("direct_quote", "anonymous"),),
        (),
    ],
)
def test_no_tag_without_attributed_direct_quote(monkeypatch, examples):
    _patch_chat_pipeline(monkeypatch, _selection(*examples), "我明白你的感受。")
    resp = TestClient(app).post("/api/v1/chat", json={"message": "最近好累"})
    assert resp.status_code == 200
    assert ATTRIBUTION_TAG.strip() not in resp.json()["reply"]


def test_no_tag_when_output_gateway_intercepts(monkeypatch):
    _patch_chat_pipeline(
        monkeypatch, _selection(("direct_quote", "attributed")), "你可以試試 Xanax。"
    )
    resp = TestClient(app).post("/api/v1/chat", json={"message": "最近好累"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intercepted"] is True
    assert ATTRIBUTION_TAG.strip() not in body["reply"]


def test_attribution_tag_is_not_written_into_memory(monkeypatch):
    store = InMemoryConversationStore()
    _patch_chat_pipeline(
        monkeypatch, _selection(("direct_quote", "attributed")), "我明白你的感受。", store=store
    )
    session_id = "phase3-tag-memory-" + uuid.uuid4().hex[:8]
    resp = TestClient(app).post("/api/v1/chat", json={"session_id": session_id, "message": "最近好累"})
    assert resp.json()["reply"].endswith(ATTRIBUTION_TAG)

    history = asyncio.run(store.get_history(session_id))
    assert history[-1] == {"role": "assistant", "content": "我明白你的感受。"}


# ── 2. 需要真實 Postgres ──────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _new_session_id(label: str) -> str:
    return f"phase3-test-{label}-" + uuid.uuid4().hex[:10]


def _unique_topic(label: str) -> str:
    return f"phase3-topic-{label}-" + uuid.uuid4().hex[:8]


async def _seed_profile(session_id: str, topic: str, year_of_study: str | None = None) -> None:
    from sqlalchemy import select

    from app.core.condition_matching import EVOLVED_TOPIC_THRESHOLD
    from app.db import get_session
    from app.db.models import User
    from app.db.models_profile import ProfileTopic, UserProfile

    async with get_session() as db:
        user = await db.scalar(select(User).where(User.external_ref == session_id))
        profile = await db.get(UserProfile, user.id)
        if profile is None:
            db.add(UserProfile(user_id=user.id, year_of_study=year_of_study))
        else:
            profile.year_of_study = year_of_study
        db.add(ProfileTopic(user_id=user.id, topic=topic, mention_count=EVOLVED_TOPIC_THRESHOLD))
        await db.commit()


async def _cleanup(session_id: str, example_ids: list[str]) -> None:
    from sqlalchemy import select, text

    from app.db import get_session
    from app.db.models import User

    async with get_session() as db:
        for example_id in example_ids:
            # example_usage_log.example_id 是 ON DELETE CASCADE，一起清掉
            await db.execute(text("DELETE FROM response_examples WHERE id = :id"), {"id": example_id})
        user = await db.scalar(select(User).where(User.external_ref == session_id))
        if user is not None:
            await db.execute(text("DELETE FROM example_usage_log WHERE user_id = :uid"), {"uid": user.id})
            await db.execute(text("DELETE FROM profile_topics WHERE user_id = :uid"), {"uid": user.id})
            await db.execute(text("DELETE FROM user_profiles WHERE user_id = :uid"), {"uid": user.id})
            await db.execute(text("DELETE FROM session_summaries WHERE user_id = :uid"), {"uid": user.id})
            await db.execute(
                text("DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE user_id = :uid)"),
                {"uid": user.id},
            )
            await db.execute(text("DELETE FROM sessions WHERE user_id = :uid"), {"uid": user.id})
            await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
        await db.commit()


def _create_example(client: TestClient, conditions: dict, **overrides) -> str:
    body = {"content": overrides.pop("content", "治療師範例回覆"), "applicable_conditions_json": conditions}
    body.update(overrides)
    resp = client.post("/api/v1/admin/examples", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _patch_llm_and_hooks(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    def fake_chat_with_llm(user_message, conversation_history=None, **kwargs):
        captured.append(kwargs)
        return "我明白，我在這裡陪你。"

    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)
    monkeypatch.setattr("app.routers.chat.process_post_chat_updates", _noop)
    monkeypatch.setattr("app.routers.chat.end_session_and_summarize", _noop)
    return captured


@requires_db
def test_postgres_append_returns_message_id():
    from app.storage.postgres_store import PostgresConversationStore

    session_id = _new_session_id("append")
    try:
        message_id = _run(PostgresConversationStore().append(session_id, "assistant", "hello"))
        assert message_id is not None
        uuid.UUID(message_id)
        assert _run(PostgresConversationStore().append(session_id, "system", "ignored")) is None
    finally:
        _run(_cleanup(session_id, []))


@requires_db
def test_matching_example_is_injected_and_usage_logged(monkeypatch):
    from sqlalchemy import select

    from app.db import get_session
    from app.db.models import ConversationSession, Message
    from app.db.models_example import ExampleUsageLog

    captured = _patch_llm_and_hooks(monkeypatch)
    client = _client()
    session_id = _new_session_id("inject")
    topic = _unique_topic("inject")
    example_ids: list[str] = []
    try:
        example_ids.append(_create_example(
            client, {"topics_include": [topic]},
            content="聽起來你真的很在意這段關係。", usage_mode="direct_quote", attribution_mode="attributed",
        ))
        # 另一則條件不符（年級不同），不該被選中
        example_ids.append(_create_example(client, {"year_of_study": "Year 4", "topics_include": [topic]}))

        first = client.post("/api/v1/chat", json={"session_id": session_id, "message": "哈囉"})
        assert first.status_code == 200
        assert captured[-1]["examples"] == []  # 還沒有 profile/主題，不命中
        assert ATTRIBUTION_TAG.strip() not in first.json()["reply"]

        _run(_seed_profile(session_id, topic, year_of_study="Year 1"))

        second = client.post("/api/v1/chat", json={"session_id": session_id, "message": "又同朋友嗌交"})
        assert second.status_code == 200
        assert captured[-1]["examples"] == [
            {"usage_mode": "direct_quote", "content": "聽起來你真的很在意這段關係。"}
        ]
        assert second.json()["reply"].endswith(ATTRIBUTION_TAG)

        async def check():
            async with get_session() as db:
                session_row = await db.scalar(
                    select(ConversationSession).where(ConversationSession.client_key == session_id)
                )
                logs = (
                    await db.execute(
                        select(ExampleUsageLog).where(ExampleUsageLog.session_id == session_row.id)
                    )
                ).scalars().all()
                assert len(logs) == 1
                assert str(logs[0].example_id) == example_ids[0]
                message = await db.get(Message, logs[0].message_id)
                assert message.role == "assistant"
                # memory 裡存的是沒有引用標記的原始回覆
                assert message.content == "我明白，我在這裡陪你。"

        _run(check())
    finally:
        _run(_cleanup(session_id, example_ids))


@requires_db
def test_usage_log_survives_reset_with_nulled_references(monkeypatch):
    from sqlalchemy import select

    from app.db import get_session
    from app.db.models import User
    from app.db.models_example import ExampleUsageLog

    _patch_llm_and_hooks(monkeypatch)
    client = _client()
    session_id = _new_session_id("reset")
    topic = _unique_topic("reset")
    example_ids: list[str] = []
    try:
        example_ids.append(_create_example(client, {"topics_include": [topic]}))
        assert client.post("/api/v1/chat", json={"session_id": session_id, "message": "哈囉"}).status_code == 200
        _run(_seed_profile(session_id, topic))
        assert client.post("/api/v1/chat", json={"session_id": session_id, "message": "再講多次"}).status_code == 200

        reset_resp = client.post("/api/v1/reset", json={"session_id": session_id})
        assert reset_resp.status_code == 200

        async def check():
            async with get_session() as db:
                user = await db.scalar(select(User).where(User.external_ref == session_id))
                logs = (
                    await db.execute(select(ExampleUsageLog).where(ExampleUsageLog.user_id == user.id))
                ).scalars().all()
                assert len(logs) == 1
                assert logs[0].message_id is None
                assert logs[0].session_id is None
                assert str(logs[0].example_id) == example_ids[0]

        _run(check())
    finally:
        _run(_cleanup(session_id, example_ids))


@requires_db
def test_selection_is_capped_and_prefers_more_specific_then_newer(monkeypatch):
    from app.core.example_selector import EXAMPLE_MATCH_LIMIT, select_applicable_examples

    _patch_llm_and_hooks(monkeypatch)
    client = _client()
    session_id = _new_session_id("limit")
    topic = _unique_topic("limit")
    example_ids: list[str] = []
    try:
        # 各自獨立的 API 呼叫 = 各自的 transaction，created_at 才會不同
        older_generic = _create_example(client, {"topics_include": [topic]}, content="older-generic")
        specific = _create_example(
            client, {"year_of_study": "Year 2", "topics_include": [topic]}, content="specific"
        )
        newer_generic = _create_example(client, {"topics_include": [topic]}, content="newer-generic")
        example_ids += [older_generic, specific, newer_generic]

        assert client.post("/api/v1/chat", json={"session_id": session_id, "message": "哈囉"}).status_code == 200
        _run(_seed_profile(session_id, topic, year_of_study="Year 2"))

        selection = _run(select_applicable_examples(session_id))
        assert EXAMPLE_MATCH_LIMIT == 2
        assert [ex.id for ex in selection.examples] == [specific, newer_generic]
        assert selection.user_id is not None
    finally:
        _run(_cleanup(session_id, example_ids))


@requires_db
def test_archived_example_is_not_selected(monkeypatch):
    from app.core.example_selector import select_applicable_examples

    _patch_llm_and_hooks(monkeypatch)
    client = _client()
    session_id = _new_session_id("archive")
    topic = _unique_topic("archive")
    example_ids: list[str] = []
    try:
        example_id = _create_example(client, {"topics_include": [topic]})
        example_ids.append(example_id)
        assert client.post("/api/v1/chat", json={"session_id": session_id, "message": "哈囉"}).status_code == 200
        _run(_seed_profile(session_id, topic))
        assert [ex.id for ex in _run(select_applicable_examples(session_id)).examples] == [example_id]

        patch_resp = client.patch(f"/api/v1/admin/examples/{example_id}", json={"status": "archived"})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["status"] == "archived"
        assert _run(select_applicable_examples(session_id)).examples == []

        listed = client.get("/api/v1/admin/examples", params={"status": "archived"}).json()
        assert example_id in [ex["id"] for ex in listed]
    finally:
        _run(_cleanup(session_id, example_ids))


@requires_db
def test_admin_examples_api_validation():
    client = _client()
    # 空條件永遠不會命中，建立時就擋掉
    assert client.post(
        "/api/v1/admin/examples", json={"content": "x", "applicable_conditions_json": {}}
    ).status_code == 422
    for bad_conditions in ({"topics_include": "t"}, {"topics_includ": ["t"]}):
        assert client.post(
            "/api/v1/admin/examples", json={"content": "x", "applicable_conditions_json": bad_conditions}
        ).status_code == 422
    assert client.patch(
        f"/api/v1/admin/examples/{uuid.uuid4()}", json={"applicable_conditions_json": {"foo": "bar"}}
    ).status_code == 422
    assert client.post(
        "/api/v1/admin/examples",
        json={"content": "x", "applicable_conditions_json": {"topics_include": ["t"]}, "usage_mode": "verbatim"},
    ).status_code == 422
    assert client.post(
        "/api/v1/admin/examples",
        json={"content": "x", "applicable_conditions_json": {"topics_include": ["t"]}, "created_by": "not-a-uuid"},
    ).status_code == 400
    assert client.post(
        "/api/v1/admin/examples",
        json={"content": "x", "applicable_conditions_json": {"topics_include": ["t"]}, "created_by": str(uuid.uuid4())},
    ).status_code == 400
    assert client.patch("/api/v1/admin/examples/not-a-uuid", json={"status": "archived"}).status_code == 400
    assert client.patch(f"/api/v1/admin/examples/{uuid.uuid4()}", json={"status": "archived"}).status_code == 404
