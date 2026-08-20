"""
Phase 2 integration tests — Profile / 主題演化 / 跨 Session 摘要。

需要一個真實 Postgres（DATABASE_URL），因為要驗證的行為（session_summaries
的 ON DELETE SET NULL、profile_topics 的 mention_count 累加、idempotency
guard）都是資料庫層行為，InMemoryConversationStore 模擬不出來——沒有設定
DATABASE_URL 時整批跳過，不影響 `pytest tests/ -v` 預設（無 DB）的快速回歸。

LLM 呼叫一律 mock（monkeypatch `get_azure_client`），理由：
1. 確定性——斷言 profile/topic 的值不該隨真實模型輸出漂移。
2. 可以精確計數呼叫次數，驗證「合併成一次 LLM 呼叫」這個 Phase 2 的關鍵修正
   （見 docs/Phase2_implement_plan_Antigravity.md §2）真的生效。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from types import SimpleNamespace

import pytest

# 刻意不呼叫 load_dotenv()：這支測試要不要連真的資料庫，完全由呼叫端是否已經
# `export DATABASE_URL=...` 決定（跟 tests/ 底下其它測試一致，保持 `pytest tests/`
# 預設不連 DB 的行為）。如果這裡載入 .env，會把 .env 裡的 Supabase DATABASE_URL
# 悄悄帶進本來沒設 DB 的預設測試執行，導致 CI/開發者沒注意到就打到正式環境。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db import get_session
from app.db.models import ConversationSession, User
from app.db.models_profile import ProfileTopic, SessionSummary, UserProfile
from app.main import app

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="Phase 2 tests need a real Postgres (set DATABASE_URL, e.g. local Docker on :5433)",
)


@pytest.fixture(autouse=True)
def _dummy_azure_deployment_env(monkeypatch):
    """
    profile_service.py / session_summarizer.py 讀 os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]
    來組 create() 的 model= 參數——即使 client 本身被 monkeypatch 成 FakeAzureClient，
    這個環境變數查找仍然會在呼叫 create() 之前先執行。這裡給一個假值，讓測試不需要
    真的 Azure 憑證也能跑（FakeAzureClient 完全不會用到這個值）。
    """
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME", "test-deployment")


class FakeAzureClient:
    """假的 AzureOpenAI client：每次 create() 依序回傳 response_jsons 裡的下一筆，並計數呼叫次數。"""

    def __init__(self, *response_jsons: dict):
        self._responses = list(response_jsons)
        self.call_count = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.call_count += 1
        data = self._responses.pop(0) if self._responses else {}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))]
        )


def _run(coro):
    return asyncio.run(coro)


def _new_session_id(label: str) -> str:
    return f"phase2-test-{label}-" + uuid.uuid4().hex[:10]


async def _cleanup(session_id: str) -> None:
    async with get_session() as db:
        user = await db.scalar(select(User).where(User.external_ref == session_id))
        if user is None:
            return
        await db.execute(text("DELETE FROM session_summaries WHERE user_id = :uid"), {"uid": user.id})
        await db.execute(text("DELETE FROM profile_change_log WHERE user_id = :uid"), {"uid": user.id})
        await db.execute(text("DELETE FROM profile_topics WHERE user_id = :uid"), {"uid": user.id})
        await db.execute(text("DELETE FROM user_profiles WHERE user_id = :uid"), {"uid": user.id})
        await db.execute(text("DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE user_id = :uid)"), {"uid": user.id})
        await db.execute(text("DELETE FROM sessions WHERE user_id = :uid"), {"uid": user.id})
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
        await db.commit()


def fake_chat_with_llm(user_message, conversation_history=None, **kwargs):
    return "我明白，我在這裡陪你。"


def test_post_chat_updates_extract_profile_and_topics_in_a_single_llm_call(monkeypatch):
    session_id = _new_session_id("profile")
    fake_client = FakeAzureClient({
        "display_name": "Alex",
        "year_of_study": "Year 2",
        "profile_updates": {},
        "corrections": [],
        "topics": ["Academic Stress"],
    })
    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)
    monkeypatch.setattr("app.core.profile_service.get_azure_client", lambda: fake_client)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/chat",
        json={"session_id": session_id, "message": "我叫 Alex，讀 Year 2，功課壓力好大。"},
    )
    assert resp.status_code == 200

    # 合併成一次 LLM 呼叫（不是原設計的兩次）
    assert fake_client.call_count == 1

    async def check():
        async with get_session() as db:
            user = await db.scalar(select(User).where(User.external_ref == session_id))
            assert user is not None
            profile = await db.get(UserProfile, user.id)
            assert profile is not None
            assert profile.display_name == "Alex"
            assert profile.year_of_study == "Year 2"
            topic = await db.get(ProfileTopic, (user.id, "Academic Stress"))
            assert topic is not None
            assert topic.mention_count == 1

    _run(check())
    _run(_cleanup(session_id))


def test_context_assembly_injects_profile_and_past_summary(monkeypatch):
    session_id = _new_session_id("context")
    captured: list[dict] = []

    def capturing_chat_with_llm(user_message, conversation_history=None, **kwargs):
        captured.append(kwargs)
        return "回應"

    monkeypatch.setattr("app.routers.chat.chat_with_llm", capturing_chat_with_llm)
    monkeypatch.setattr(
        "app.core.profile_service.get_azure_client",
        lambda: FakeAzureClient({"display_name": None, "year_of_study": None, "profile_updates": {}, "corrections": [], "topics": []}),
    )

    client = TestClient(app)
    # 第一句先建立 user/session，並種入 profile + 3 次同主題 + 一筆舊摘要
    first = client.post("/api/v1/chat", json={"session_id": session_id, "message": "哈囉"})
    assert first.status_code == 200

    async def seed():
        async with get_session() as db:
            user = await db.scalar(select(User).where(User.external_ref == session_id))
            profile = await db.get(UserProfile, user.id)
            profile.display_name = "小美"
            profile.year_of_study = "Year 1"
            for _ in range(3):
                topic = await db.get(ProfileTopic, (user.id, "Relationship"))
                if topic:
                    topic.mention_count += 1
                else:
                    db.add(ProfileTopic(user_id=user.id, topic="Relationship", mention_count=1))
            db.add(SessionSummary(
                user_id=user.id, session_id=None,
                summary_text="上次談到跟朋友的爭執，情緒逐漸平復。",
            ))
            await db.commit()

    _run(seed())

    second = client.post("/api/v1/chat", json={"session_id": session_id, "message": "想接續上次講嗰個朋友嘅事"})
    assert second.status_code == 200

    profile_text = captured[-1]["profile_text"]
    past_summaries_text = captured[-1]["past_summaries_text"]
    assert profile_text is not None and "小美" in profile_text and "Relationship" in profile_text
    assert past_summaries_text is not None and "上次談到跟朋友的爭執" in past_summaries_text

    _run(_cleanup(session_id))


def test_reset_summarizes_session_and_summary_survives_cascade_delete(monkeypatch):
    session_id = _new_session_id("reset")
    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)
    monkeypatch.setattr(
        "app.core.profile_service.get_azure_client",
        lambda: FakeAzureClient({"display_name": None, "year_of_study": None, "profile_updates": {}, "corrections": [], "topics": []}),
    )
    fake_summarizer = FakeAzureClient({
        "summary_text": "討論了考試焦慮，練習了呼吸法。",
        "key_events": [], "emotions": [], "strategies_used": [],
    })
    monkeypatch.setattr("app.core.session_summarizer.get_azure_client", lambda: fake_summarizer)

    client = TestClient(app)
    resp = client.post("/api/v1/chat", json={"session_id": session_id, "message": "考試好焦慮"})
    assert resp.status_code == 200

    reset_resp = client.post("/api/v1/reset", json={"session_id": session_id})
    assert reset_resp.status_code == 200

    async def check():
        async with get_session() as db:
            user = await db.scalar(select(User).where(User.external_ref == session_id))
            assert user is not None
            # sessions 那筆已經被 /reset 刪掉
            session_row = await db.scalar(
                select(ConversationSession).where(ConversationSession.client_key == session_id)
            )
            assert session_row is None

            summaries = (
                await db.execute(select(SessionSummary).where(SessionSummary.user_id == user.id))
            ).scalars().all()
            assert len(summaries) == 1
            assert summaries[0].session_id is None  # SET NULL，不是被 cascade 刪掉
            assert "考試焦慮" in summaries[0].summary_text

    _run(check())
    _run(_cleanup(session_id))


def test_chat_end_then_reset_does_not_duplicate_summary(monkeypatch):
    session_id = _new_session_id("idempotent")
    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)
    monkeypatch.setattr(
        "app.core.profile_service.get_azure_client",
        lambda: FakeAzureClient({"display_name": None, "year_of_study": None, "profile_updates": {}, "corrections": [], "topics": []}),
    )
    fake_summarizer = FakeAzureClient({
        "summary_text": "summary", "key_events": [], "emotions": [], "strategies_used": [],
    })
    monkeypatch.setattr("app.core.session_summarizer.get_azure_client", lambda: fake_summarizer)

    client = TestClient(app)
    resp = client.post("/api/v1/chat", json={"session_id": session_id, "message": "hello"})
    assert resp.status_code == 200

    end_resp = client.post("/api/v1/chat/end", json={"session_id": session_id})
    assert end_resp.status_code == 200
    assert end_resp.json()["status"] == "ended"

    reset_resp = client.post("/api/v1/reset", json={"session_id": session_id})
    assert reset_resp.status_code == 200

    # 只呼叫了一次摘要 LLM（第二次 end_session_and_summarize 因 ended_at 已設而 no-op）
    assert fake_summarizer.call_count == 1

    async def check():
        async with get_session() as db:
            user = await db.scalar(select(User).where(User.external_ref == session_id))
            summaries = (
                await db.execute(select(SessionSummary).where(SessionSummary.user_id == user.id))
            ).scalars().all()
            assert len(summaries) == 1

    _run(check())
    _run(_cleanup(session_id))


def test_profile_forget_soft_deletes_summaries_and_resets_profile(monkeypatch):
    session_id = _new_session_id("forget")
    monkeypatch.setattr("app.routers.chat.chat_with_llm", fake_chat_with_llm)
    monkeypatch.setattr(
        "app.core.profile_service.get_azure_client",
        lambda: FakeAzureClient({"display_name": None, "year_of_study": None, "profile_updates": {}, "corrections": [], "topics": []}),
    )

    client = TestClient(app)
    resp = client.post("/api/v1/chat", json={"session_id": session_id, "message": "hi"})
    assert resp.status_code == 200

    async def seed():
        async with get_session() as db:
            user = await db.scalar(select(User).where(User.external_ref == session_id))
            profile = await db.get(UserProfile, user.id)
            profile.display_name = "阿明"
            profile.year_of_study = "Year 3"
            db.add(ProfileTopic(user_id=user.id, topic="Anxiety", mention_count=5))
            db.add(SessionSummary(user_id=user.id, session_id=None, summary_text="要被遺忘的摘要"))
            await db.commit()

    _run(seed())

    forget_resp = client.post("/api/v1/profile/forget", json={"session_id": session_id})
    assert forget_resp.status_code == 200
    assert forget_resp.json()["status"] == "forgotten"

    async def check():
        async with get_session() as db:
            user = await db.scalar(select(User).where(User.external_ref == session_id))
            profile = await db.get(UserProfile, user.id)
            assert profile.display_name is None
            assert profile.year_of_study is None
            assert profile.risk_level == "none"

            topics = (
                await db.execute(select(ProfileTopic).where(ProfileTopic.user_id == user.id))
            ).scalars().all()
            assert topics == []

            summaries = (
                await db.execute(select(SessionSummary).where(SessionSummary.user_id == user.id))
            ).scalars().all()
            assert len(summaries) == 1
            assert summaries[0].deleted_at is not None

    _run(check())
    _run(_cleanup(session_id))
