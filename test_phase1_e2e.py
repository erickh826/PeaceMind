"""Phase 1 End-to-end test against real Postgres.

Steps:
1. Create a second persona ("Winnie" - 冷静理性, different tone from Boon)
2. Activate it
3. Create a session (simulate user chatting) → verify Boon (default) is used
4. Assign Winnie to that session's user
5. Resolve persona again → verify Winnie is used
6. Verify persona_switch_log was written
7. Verify persona_id is stored in messages
"""
import asyncio
import sys
import uuid

from dotenv import load_dotenv

load_dotenv()

# Windows 預設 ProactorEventLoop 與 psycopg async 不相容，需改用 SelectorEventLoop
# （同 migrations/env.py 的修正，本機直接跑這支腳本一樣會踩到）。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.db.models import ConversationSession, Message, User
from app.db.models_persona import (
    Persona,
    PersonaAssignment,
    PersonaSwitchLog,
    Therapist,
)
from app.core.persona_resolver import ResolvedPersona, resolve_persona, record_persona_usage
from app.main import app
from app.storage.postgres_store import PostgresConversationStore
from sqlalchemy import select, text


async def main():
    store = PostgresConversationStore(max_messages=10)
    test_session_id = "phase1-e2e-" + uuid.uuid4().hex[:12]
    print(f"Test session_id: {test_session_id}")

    # ── Step 0: Create test therapist (required for assigned_by FK) ────────
    print("\n" + "=" * 60)
    print("Step 0: Create test therapist (required for persona assignments)")
    async with get_session() as db:
        therapist = Therapist(
            name="Test Therapist",
            email="test-therapist@peacemind.local",
            role="admin",
        )
        db.add(therapist)
        await db.commit()
        await db.refresh(therapist)
        therapist_id = therapist.id
        print(f"  Created therapist: id={therapist_id}")
        print("  [PASS] Therapist ready")

    # ── Step 1: Create second persona ───────────────────────────────────────
    print("\n" + "=" * 60)
    print("Step 1: Create second persona ('Winnie' - calm + rational)")
    async with get_session() as db:
        winnie = Persona(
            name="Winnie（第二人格）",
            description="冷静理性风格，适合高焦虑但需要结构化引导的情境",
            tone="冷静理性",
            response_length="medium",
            therapy_style="CBT",
            system_prompt_fragment=(
                "你是「Winnie」，一位冷静理性的心理健康支持助理。\n"
                "你的风格是：先帮对方理清思绪，再用结构化方式引导对方找到自己的答案。"
            ),
            status="draft",
            is_default=False,
        )
        db.add(winnie)
        await db.commit()
        await db.refresh(winnie)
        winnie_id = str(winnie.id)
        print(f"  Created Winnie: id={winnie_id}, status={winnie.status}")

    # ── Step 2: Activate Winnie ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Step 2: Activate Winnie")
    async with get_session() as db:
        winnie = await db.get(Persona, uuid.UUID(winnie_id))
        winnie.status = "active"
        await db.commit()
        await db.refresh(winnie)
        assert winnie.status == "active", f"Expected active, got {winnie.status}"
        print(f"  Winnie status: {winnie.status}")
        print("  [PASS] Winnie activated")

    # ── Step 3: Create a session + messages (simulate user chatting) ────────
    print("\n" + "=" * 60)
    print("Step 3: Simulate user chatting → creates session + user in DB")
    await store.append(test_session_id, "user", "I feel anxious about my exam.")
    await store.append(test_session_id, "assistant", "唔使擔心...")

    # Verify user was auto-created via PostgresConversationStore
    async with get_session() as db:
        user = await db.scalar(select(User).where(User.external_ref == test_session_id))
        print(f"  Auto-created user: id={user.id}")
        session = await db.scalar(
            select(ConversationSession).where(ConversationSession.client_key == test_session_id)
        )
        print(f"  Session created: id={session.id}, persona_id={session.persona_id}")
        print("  [PASS] Session + user auto-created")

    # ── Step 4: Resolve persona → should get Boon (default) ─────────────────
    print("\n" + "=" * 60)
    print("Step 4: resolve_persona() → should return Boon (default)")
    persona = await resolve_persona(user_client_key=test_session_id)
    print(f"  Resolved: name='{persona.name}', id={persona.id}")
    assert persona.name == "Boon（預設）", f"Expected Boon, got {persona.name}"
    # Default Boon has fixed UUID
    assert persona.id == "00000000-0000-0000-0000-000000000001"
    print("  [PASS] Default Boon persona resolved")

    # ── Step 5: Assign Winnie to this user via real HTTP endpoint ───────────
    print("\n" + "=" * 60)
    print("Step 5: POST /api/v1/admin/personas/assign (real HTTP call, not direct ORM)")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/admin/personas/assign",
            json={
                "session_id": test_session_id,
                "persona_id": winnie_id,
                "assigned_by": str(therapist_id),
                "note": "Phase 1 E2E test",
            },
        )
    print(f"  HTTP {response.status_code}: {response.json()}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert body["status"] == "assigned"
    assert body["persona_id"] == winnie_id
    async with get_session() as db:
        user = await db.scalar(select(User).where(User.external_ref == test_session_id))
        assignment = await db.get(PersonaAssignment, user.id)
        assert assignment is not None, "PersonaAssignment row should exist after API call"
        assert assignment.persona_id == uuid.UUID(winnie_id)
        print(f"  Assignment persisted: user_id={user.id} → persona_id={winnie_id}")
        print("  [PASS] Persona assigned via real API endpoint")

    # ── Step 6: Resolve persona again → should get Winnie ───────────────────
    print("\n" + "=" * 60)
    print("Step 6: resolve_persona() again → should return Winnie (assigned)")
    persona2 = await resolve_persona(user_client_key=test_session_id)
    print(f"  Resolved: name='{persona2.name}', id={persona2.id}")
    assert persona2.name == winnie.name, f"Expected Winnie, got {persona2.name}"
    assert persona2.id == winnie_id
    assert persona2.system_prompt_fragment != persona.system_prompt_fragment
    print("  [PASS] Assigned persona (Winnie) takes priority over default (Boon)")

    # ── Step 7: record_persona_usage → should write switch_log ──────────────
    print("\n" + "=" * 60)
    print("Step 7: record_persona_usage() → should write persona_switch_log")
    await record_persona_usage(test_session_id, persona2)

    async with get_session() as db:
        session = await db.scalar(
            select(ConversationSession).where(ConversationSession.client_key == test_session_id)
        )
        assert session.persona_id == uuid.UUID(winnie_id), f"session.persona_id should be updated"
        print(f"  sessions.persona_id updated to: {session.persona_id}")

        logs = (await db.execute(
            select(PersonaSwitchLog).where(PersonaSwitchLog.session_id == session.id)
        )).scalars().all()
        assert len(logs) == 1, f"Expected 1 switch log, got {len(logs)}"
        assert logs[0].trigger_reason == "resolver_reassignment"
        print(f"  switch_log written: from={logs[0].from_persona_id} → to={logs[0].to_persona_id}")
        print("  [PASS] session + switch_log updated")

        # Verify messages still have persona_id=NULL (append was before assignment)
        msg_count = await db.scalar(
            select(text("count(*)")).select_from(Message).where(Message.persona_id.is_(None))
        )
        print(f"  Messages without persona_id: {msg_count} (expected: all, since append was before assignment)")

    # ── Step 8: New message after assignment → persona_id stored ────────────
    print("\n" + "=" * 60)
    print("Step 8: New message after assignment → persona_id should be stored")
    await store.append(test_session_id, "user", "I'm still worried.", persona_id=persona2.id)
    await store.append(test_session_id, "assistant", "Let's break this down.", persona_id=persona2.id)

    async with get_session() as db:
        msgs = (await db.execute(
            select(Message).where(Message.persona_id.isnot(None))
        )).scalars().all()
        assert len(msgs) == 2, f"Expected 2 messages with persona_id, got {len(msgs)}"
        for m in msgs:
            assert str(m.persona_id) == winnie_id
            print(f"  Message: role={m.role}, persona_id={m.persona_id}")
        print("  [PASS] New messages carry persona_id correctly")

    # ── Step 9: Another session (no assignment) → still gets Boon ───────────
    print("\n" + "=" * 60)
    print("Step 9: Different session → should still get Boon (default)")
    other_session = "phase1-e2e-other-" + uuid.uuid4().hex[:8]
    persona3 = await resolve_persona(user_client_key=other_session)
    print(f"  Resolved: name='{persona3.name}'")
    assert persona3.name == "Boon（預設）", f"Expected Boon, got {persona3.name}"
    print("  [PASS] Unassigned session still gets default persona")

    # ── Cleanup ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    await store.reset(test_session_id)
    async with get_session() as db:
        # Delete test user
        user = await db.scalar(select(User).where(User.external_ref == test_session_id))
        if user:
            # Delete assignment first (FK to user)
            await db.execute(text("DELETE FROM persona_assignments WHERE user_id = :uid"), {"uid": user.id})
            await db.commit()
            await db.delete(user)
            await db.commit()
        # Delete Winnie persona
        await db.execute(text("DELETE FROM persona_match_conditions WHERE persona_id = :pid"), {"pid": uuid.UUID(winnie_id)})
        await db.execute(text("DELETE FROM persona_switch_log WHERE to_persona_id = :pid OR from_persona_id = :pid"), {"pid": uuid.UUID(winnie_id)})
        await db.execute(text("DELETE FROM persona_assignments WHERE persona_id = :pid"), {"pid": uuid.UUID(winnie_id)})
        await db.delete(await db.get(Persona, uuid.UUID(winnie_id)))
        await db.commit()
        # Delete test therapist
        therapist_obj = await db.get(Therapist, therapist_id)
        if therapist_obj:
            await db.delete(therapist_obj)
            await db.commit()
        print("  Test data cleaned up")

    print("\n" + "=" * 60)
    print("[ALL TESTS PASSED]")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
