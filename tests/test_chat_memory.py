import asyncio

from fastapi.testclient import TestClient

from app.main import app
from app.storage import conversation_store


async def _noop(*args, **kwargs) -> None:
    return None


def reset_store(session_id: str) -> None:
    """
    測試輔助函式：conversation_store.reset() 在 Phase 0 改為 async
    （為了讓 PostgresConversationStore 可用 async SQLAlchemy），
    這裡包一層讓測試的 setup/teardown 可以同步呼叫。
    """
    asyncio.run(conversation_store.reset(session_id))


def test_chat_uses_server_side_memory_and_ignores_client_history(monkeypatch):
    captured_histories: list[list[dict[str, str]]] = []

    def fake_chat_with_llm(user_message: str, conversation_history: list[dict] | None = None, **kwargs):
        captured_histories.append(conversation_history or [])
        return "我明白你很辛苦，我在這裡陪你。"

    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)
    # Phase 2 起，/chat 和 /reset 在 DATABASE_URL 有設定時會呼叫真的 LLM 做 profile/
    # topic 抽取與 session 摘要。這幾支測試不是 Phase 2 相關測試，跟 Phase 2 的
    # LLM 呼叫無關，這裡 no-op 掉，避免跑這個檔案（尤其是不小心帶著真的
    # DATABASE_URL 執行時）意外打真的 Azure OpenAI、寫入非預期的 profile 資料。
    monkeypatch.setattr("app.routers.chat.process_post_chat_updates", _noop)
    monkeypatch.setattr("app.routers.chat.end_session_and_summarize", _noop)

    session_id = "session-memory-test-1"
    reset_store(session_id)
    client = TestClient(app)

    first = client.post(
        "/api/v1/chat",
        json={
            "session_id": session_id,
            "message": "我今天很累。",
            "history": [],
        },
    )
    assert first.status_code == 200
    assert captured_histories[0] == []

    second = client.post(
        "/api/v1/chat",
        json={
            "session_id": session_id,
            "message": "我有點喘不過氣。",
            "history": [{"role": "assistant", "content": "這是假的 client history"}],
        },
    )
    assert second.status_code == 200
    assert len(captured_histories[1]) == 2
    assert captured_histories[1][0]["role"] == "user"
    assert captured_histories[1][0]["content"] == "我今天很累。"

    reset_store(session_id)


def test_reset_endpoint_clears_session_memory(monkeypatch):
    captured_histories: list[list[dict[str, str]]] = []

    def fake_chat_with_llm(user_message: str, conversation_history: list[dict] | None = None, **kwargs):
        captured_histories.append(conversation_history or [])
        return "我會陪你慢慢整理。"

    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)
    # Phase 2 起，/chat 和 /reset 在 DATABASE_URL 有設定時會呼叫真的 LLM 做 profile/
    # topic 抽取與 session 摘要。這幾支測試不是 Phase 2 相關測試，跟 Phase 2 的
    # LLM 呼叫無關，這裡 no-op 掉，避免跑這個檔案（尤其是不小心帶著真的
    # DATABASE_URL 執行時）意外打真的 Azure OpenAI、寫入非預期的 profile 資料。
    monkeypatch.setattr("app.routers.chat.process_post_chat_updates", _noop)
    monkeypatch.setattr("app.routers.chat.end_session_and_summarize", _noop)

    session_id = "session-memory-test-2"
    reset_store(session_id)
    client = TestClient(app)

    first = client.post(
        "/api/v1/chat",
        json={"session_id": session_id, "message": "第一句", "history": []},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/chat",
        json={"session_id": session_id, "message": "第二句", "history": []},
    )
    assert second.status_code == 200
    assert len(captured_histories[1]) == 2

    reset = client.post("/api/v1/reset", json={"session_id": session_id})
    assert reset.status_code == 200
    assert reset.json() == {"status": "cleared"}

    third = client.post(
        "/api/v1/chat",
        json={"session_id": session_id, "message": "重置後第一句", "history": []},
    )
    assert third.status_code == 200
    assert captured_histories[2] == []

    reset_store(session_id)


def test_sessions_are_isolated(monkeypatch):
    captured_histories: list[list[dict[str, str]]] = []

    def fake_chat_with_llm(user_message: str, conversation_history: list[dict] | None = None, **kwargs):
        captured_histories.append(conversation_history or [])
        return "我在。"

    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)
    # Phase 2 起，/chat 和 /reset 在 DATABASE_URL 有設定時會呼叫真的 LLM 做 profile/
    # topic 抽取與 session 摘要。這幾支測試不是 Phase 2 相關測試，跟 Phase 2 的
    # LLM 呼叫無關，這裡 no-op 掉，避免跑這個檔案（尤其是不小心帶著真的
    # DATABASE_URL 執行時）意外打真的 Azure OpenAI、寫入非預期的 profile 資料。
    monkeypatch.setattr("app.routers.chat.process_post_chat_updates", _noop)
    monkeypatch.setattr("app.routers.chat.end_session_and_summarize", _noop)

    session_a = "session-isolation-a"
    session_b = "session-isolation-b"
    reset_store(session_a)
    reset_store(session_b)
    client = TestClient(app)

    first = client.post(
        "/api/v1/chat",
        json={"session_id": session_a, "message": "A 的第一句", "history": []},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/chat",
        json={"session_id": session_b, "message": "B 的第一句", "history": []},
    )
    assert second.status_code == 200

    assert captured_histories[0] == []
    assert captured_histories[1] == []

    reset_store(session_a)
    reset_store(session_b)
