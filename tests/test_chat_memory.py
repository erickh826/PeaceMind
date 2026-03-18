from fastapi.testclient import TestClient

from app.main import app
from app.storage import conversation_store


def test_chat_uses_server_side_memory_and_ignores_client_history(monkeypatch):
    captured_histories: list[list[dict[str, str]]] = []

    def fake_chat_with_llm(user_message: str, conversation_history: list[dict] | None = None):
        captured_histories.append(conversation_history or [])
        return "我明白你很辛苦，我在這裡陪你。"

    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)

    session_id = "session-memory-test-1"
    conversation_store.reset(session_id)
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

    conversation_store.reset(session_id)


def test_reset_endpoint_clears_session_memory(monkeypatch):
    captured_histories: list[list[dict[str, str]]] = []

    def fake_chat_with_llm(user_message: str, conversation_history: list[dict] | None = None):
        captured_histories.append(conversation_history or [])
        return "我會陪你慢慢整理。"

    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)

    session_id = "session-memory-test-2"
    conversation_store.reset(session_id)
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

    conversation_store.reset(session_id)


def test_sessions_are_isolated(monkeypatch):
    captured_histories: list[list[dict[str, str]]] = []

    def fake_chat_with_llm(user_message: str, conversation_history: list[dict] | None = None):
        captured_histories.append(conversation_history or [])
        return "我在。"

    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)

    session_a = "session-isolation-a"
    session_b = "session-isolation-b"
    conversation_store.reset(session_a)
    conversation_store.reset(session_b)
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

    conversation_store.reset(session_a)
    conversation_store.reset(session_b)
