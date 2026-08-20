# PeaceMind Phase 2 Implementation Plan: Profile, Topic Evolution & Cross-Session Memory

This document outlines the design and step-by-step implementation strategy for **Phase 2 (Q1–Q6 & Q17)** of the PeaceMind Clinical Framework, compiled by **Antigravity**.

---

## 1. Goal Description

In Phase 1, we introduced a manageable Persona system. However, the system is still functionally "forgetful" across sessions and lacks customized contextual understanding of the individual student. 

The objective of **Phase 2** is to build the complete **Context Assembly Service** and the **Post-processing Service** as outlined in [CLINICAL_FRAMEWORK_ARCHITECTURE.md](file:///D:/PeaceMind/docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md). By implementing this, the chatbot will achieve clinical memory and customization:
1. **Dynamic Profiling**: Extract and maintain structure on students' year of study, display names, risk levels, and background context.
2. **Topic Evolution**: Keep count of semantic concerns raised by the student, evolving them into core clinical topics.
3. **Clinical Memory (Cross-Session Summaries)**: Summarize each session upon reset/end and retrieve relevant past summaries to ensure counseling continuity.
4. **Persona Auto-Matching**: Fully implement Phase 1.5 automatic persona selection based on the newly available user profile details and evolved topics.
5. **Backend Recommendations**: Align resource recommendations with the student's clinical profile.

---

## 2. User Review Required

Two design issues were found during review against the actual codebase and this project's
deployment constraints (see `CLAUDE.md`), and resolved before implementation:

> [!WARNING]
> **RESOLVED — Post-processing must run synchronously, not via `BackgroundTasks`**
> The original proposal was to run profile/topic extraction asynchronously via FastAPI
> `BackgroundTasks` *after* the reply is returned, framed purely as a latency optimization.
> This is actually a **reliability bug**: this project deploys to Vercel via a plain ASGI
> passthrough (`api/index.py`), with no `waitUntil`-equivalent — the execution environment
> freezes right after the HTTP response is sent (the same serverless statelessness documented
> in `CLINICAL_FRAMEWORK_ARCHITECTURE.md` §0 that forced Phase 0 off in-process memory in the
> first place). `BackgroundTasks` scheduled work is not guaranteed to finish in production,
> even though it looks fine under local `uvicorn` testing.
>
> **Decision**: drop `BackgroundTasks`. Profile extraction and topic classification are merged
> into a **single** LLM call (see §4 below) and run **synchronously**, `await`ed directly in
> the `/chat` handler after the reply is finalized, before the response is returned. Accepted
> tradeoff: every `/chat` turn now costs one extra LLM call's worth of latency. Judged
> acceptable at this PoC's scale; revisit only if this becomes a measured problem.

> [!WARNING]
> **RESOLVED — `session_summaries` was being cascade-deleted by the endpoint that writes it**
> `session_summaries.session_id` originally used `ON DELETE CASCADE` to `sessions.id`. But
> `/api/v1/reset` (the frontend's "新對話" button) hard-deletes the `sessions` row after
> writing the summary — cascade delete would wipe the summary in the same request, regardless
> of write order. Fixed to `ON DELETE SET NULL` (same pattern already used correctly for
> `ProfileChangeLog.session_id`), in both this doc (§ Database Layer) and
> `CLINICAL_FRAMEWORK_ARCHITECTURE.md` §2.1. See §6 below for the corresponding idempotency
> fix to the two endpoints that trigger summarization.

> [!NOTE]
> **Triggering Session Summaries**
> Session summaries are generated when a session is closed. We will trigger this:
> 1. Automatically inside `/api/v1/reset` *before* the session messages are deleted.
> 2. Via a new explicit endpoint `POST /api/v1/chat/end` which marks the session as ended (`ended_at = now()`) and generates the summary.
>
> Both paths now go through one shared, idempotent function — see §6 below — so calling both
> on the same session (e.g. `chat/end` then `reset`) does not generate two summaries.

---

## 3. Open Questions

> [!WARNING]
> **Clinical Topic Extraction Method**
> *Option A*: Use a rigid keyword matching list.
> *Option B (Recommended)*: Use a lightweight structured LLM prompt during post-processing to classify user messages into a set of standard clinical concern topics (e.g. `Relationship`, `Academic Stress`, `Family Conflict`, `Self-Esteem`, `Career Uncertainty`).
> *We have proposed **Option B** in this plan to support semantic understanding. Please let us know if you prefer a rigid keyword approach.*

---

## 4. Proposed Changes

We will introduce a new module `app/db/models_profile.py` for DB schemas, a service layer in `app/core/context_assembler.py` and `app/core/profile_service.py`, modify routers, and add migrations.

```
                         ┌────────────────────────────────────────────────────────┐
                         │              Context Assembly (chat.py)               │
                         │  - Load User Profile & Active Topics                  │
                         │  - Resolve Auto-Persona (Profile + Topic rules)       │
                         │  - Retrieve Past Summaries (if "上次" is mentioned)   │
                         └─────────────────────────┬──────────────────────────────┘
                                                   │ Builds context injection
                                                   ▼
                         ┌────────────────────────────────────────────────────────┐
                         │                       L2 LLM Core                      │
                         └─────────────────────────┬──────────────────────────────┘
                                                   │ Returns response to User
                                                   ▼
                         ┌────────────────────────────────────────────────────────┐
                         │       Post-processing (synchronous, pre-response)      │
                         │  - Single merged LLM call: profile extraction          │
                         │    + change logs + topic counts & evolution            │
                         │  (not BackgroundTasks — see §2 "RESOLVED" note)        │
                         └────────────────────────────────────────────────────────┘
```

---

### Database Layer (Models & Migrations)

#### [NEW] `app/db/models_profile.py`
This file will contain the SQLAlchemy models for Phase 2.

```python
import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, ForeignKey, Text, Integer, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    year_of_study: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False, server_default="none")
    profile_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "risk_level IN ('none','low','medium','high')", name="ck_user_profiles_risk"
        ),
    )

class ProfileChangeLog(Base):
    __tablename__ = "profile_change_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    field_path: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "source IN ('user_correction','system_inferred','therapist_edit')", name="ck_profile_change_source"
        ),
    )

class ProfileTopic(Base):
    __tablename__ = "profile_topics"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    topic: Mapped[str] = mapped_column(Text, primary_key=True)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())

class SessionSummary(Base):
    __tablename__ = "session_summaries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # SET NULL, not CASCADE: /api/v1/reset hard-deletes the sessions row right after this
    # summary is written. CASCADE would delete the summary we just wrote in the same request —
    # the summary must outlive the session it was generated from (see §2 "RESOLVED" note above).
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    key_events: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="[]")
    emotions: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="[]")
    strategies_used: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="[]")
    retention_policy: Mapped[str] = mapped_column(Text, nullable=False, server_default="permanent")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "retention_policy IN ('permanent','90d','30d')", name="ck_session_summaries_retention"
        ),
    )
```

#### [NEW] `migrations/versions/xxxx_phase2_profiles_and_memory.py`
Alembic migration script to create tables `user_profiles`, `profile_change_log`, `profile_topics`, and `session_summaries`.

---

### Context Assembly & Resolution Layer

#### [NEW] `app/core/context_assembler.py`
Handles loading/formatting user profiles and retrieving relevant cross-session memory summaries.

> **Known trade-off, not fixed in this phase**: `assemble_context()` does its own `User`
> lookup by `external_ref`, independent of the lookup `resolve_persona()` already does — two
> DB round trips instead of one. Left as-is here (correctness-neutral, small); revisit if
> per-turn latency becomes a measured concern.

```python
import os
from dataclasses import dataclass
from sqlalchemy import select
from app.db import get_session
from app.db.models import User
from app.db.models_profile import UserProfile, ProfileTopic, SessionSummary

@dataclass
class AssembledContext:
    profile_text: str | None
    past_summaries_text: str | None

MEMORY_TRIGGER_KEYWORDS = ["上次", "之前", "上次講", "上次說", "前幾次", "last time", "previously", "before", "yesterday"]

async def assemble_context(session_id: str, user_message: str) -> AssembledContext:
    if not os.environ.get("DATABASE_URL") or not session_id:
        return AssembledContext(None, None)

    async with get_session() as db:
        # 1. Resolve User via session client_key
        user_row = await db.scalar(
            select(User).where(User.external_ref == session_id)
        )
        if not user_row:
            return AssembledContext(None, None)

        # 2. Load or Create UserProfile
        profile_row = await db.get(UserProfile, user_row.id)
        if not profile_row:
            profile_row = UserProfile(user_id=user_row.id)
            db.add(profile_row)
            await db.commit()
            await db.refresh(profile_row)

        # 3. Load Evolved Topics (count >= 3)
        topic_result = await db.execute(
            select(ProfileTopic)
            .where(ProfileTopic.user_id == user_row.id)
            .order_by(ProfileTopic.mention_count.desc())
        )
        topics = topic_result.scalars().all()
        evolved_topics = [t.topic for t in topics if t.mention_count >= 3]

        # 4. Format Profile text
        profile_lines = [
            "[STUDENT PROFILE]",
            f"Display Name: {profile_row.display_name or 'Anonymous'}",
            f"Year of Study: {profile_row.year_of_study or 'Unknown'}",
            f"Clinical Risk Level: {profile_row.risk_level}",
        ]
        if evolved_topics:
            profile_lines.append(f"Identified Core Topics: {', '.join(evolved_topics)}")
        if profile_row.profile_json:
            profile_lines.append("Additional Background:")
            for k, v in profile_row.profile_json.items():
                profile_lines.append(f"  - {k}: {v}")
        profile_text = "\n".join(profile_lines)

        # 5. Retrieve Cross-Session Memory Summaries
        has_trigger = any(kw in user_message for kw in MEMORY_TRIGGER_KEYWORDS)
        # Always provide the last session's summary for immediate clinical continuity;
        # Retrieve up to 3 summaries if the user explicitly references past conversations.
        limit = 3 if has_trigger else 1

        summary_result = await db.execute(
            select(SessionSummary)
            .where(SessionSummary.user_id == user_row.id, SessionSummary.deleted_at.is_(None))
            .order_by(SessionSummary.created_at.desc())
            .limit(limit)
        )
        summaries = list(reversed(summary_result.scalars().all()))

        past_summaries_text = None
        if summaries:
            summary_lines = ["[RELEVANT PAST SESSIONS CONTEXT]"]
            for s in summaries:
                summary_lines.append(
                    f"- Session on {s.created_at.strftime('%Y-%m-%d')}: {s.summary_text}\n"
                    f"  * Emotions: {s.emotions}\n"
                    f"  * Core Events: {s.key_events}\n"
                    f"  * Coping Strategies: {s.strategies_used}"
                )
            past_summaries_text = "\n".join(summary_lines)

        return AssembledContext(profile_text, past_summaries_text)
```

#### [MODIFY] `app/core/persona_resolver.py`
Expand `resolve_persona` to support **automatic persona selection** (matching conditions):
1. **Rule Match**: Check `persona_match_conditions` sorted by priority. Condition parameters are matched against `UserProfile` attributes and active topics (`mention_count >= 3`).
2. **Fallback**: Fallback to clinician manual指派 (T-Q15), then default persona "Boon", then hardcoded fallback.

---

### Prompt & Post-processing (Analysis) Layer

#### [MODIFY] `app/prompts/system_prompt.py`
Update `build_prompt` to cleanly inject `profile_text` and `past_summaries_text` within the Sandwich Prompt boundaries.

```python
def build_prompt(
    user_message: str,
    persona_name: str = "Boon",
    persona_fragment: str = DEFAULT_PERSONA_FRAGMENT,
    security_hint: str | None = None,
    profile_text: str | None = None,
    past_summaries_text: str | None = None,
) -> str:
    context_blocks = []
    if profile_text:
        context_blocks.append(profile_text)
    if past_summaries_text:
        context_blocks.append(past_summaries_text)

    context_str = "\n\n" + "\n\n".join(context_blocks) if context_blocks else ""

    top_layer = f"{LANGUAGE_CONSTRAINT}\n\n{persona_fragment}{context_str}\n\n{SAFETY_CORE}"
    # Rest of sandwich logic remains identical...
```

#### [NEW] `app/core/clinical_topics.py`
Single source of truth for the standard clinical concern topic list, shared by the classifier
prompt below and, later, Phase 2.8 (auto persona matching) and Phase 4 (Rule Engine) — avoids
the topic list drifting between independently-edited prompt strings.

```python
STANDARD_CLINICAL_TOPICS = [
    "Relationship", "Academic Stress", "Family Conflict",
    "Self-Esteem", "Career Uncertainty", "Anxiety", "Others",
]
```

#### [NEW] `app/core/profile_service.py`
Encapsulates LLM-based analysis to dynamically extract profile updates, record change logs,
and log topic frequency.

> **Runs synchronously, not as a background task** (see §2 "RESOLVED" note): this project's
> Vercel deployment does not guarantee `BackgroundTasks` scheduled after the response completes.
> `process_post_chat_updates()` is `await`ed directly from `/chat`, after the reply is
> finalized, before the response returns. Profile extraction and topic classification are
> **merged into one LLM call** (rather than the two originally proposed) to minimize the added
> per-turn latency this synchronous call now costs.

```python
import json
import logging
import os
from sqlalchemy import func, select
from app.db import get_session
from app.db.models import User, ConversationSession
from app.db.models_profile import UserProfile, ProfileChangeLog, ProfileTopic
from app.core.llm_client import get_azure_client
from app.core.clinical_topics import STANDARD_CLINICAL_TOPICS

logger = logging.getLogger(__name__)

async def process_post_chat_updates(session_client_key: str, user_msg: str, assistant_reply: str) -> None:
    """Called synchronously from /chat after the reply is finalized, before the response
    is returned. Wrapped in try/except so an analysis failure never breaks the chat response."""
    try:
        async with get_session() as db:
            session_row = await db.scalar(
                select(ConversationSession).where(ConversationSession.client_key == session_client_key)
            )
            if not session_row:
                return
            await analyze_and_update_profile(db, session_row.user_id, session_row.id, user_msg, assistant_reply)
    except Exception as e:
        logger.error("Failed post-chat updates: %s", str(e))

async def analyze_and_update_profile(db, user_id, session_id, user_msg, assistant_reply) -> None:
    """Single merged LLM call: profile extraction + topic classification together."""
    profile = await db.get(UserProfile, user_id)
    if not profile:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
        await db.flush()

    topics_list = ", ".join(STANDARD_CLINICAL_TOPICS)
    prompt = f"""
Analyze the conversation turn and output a single JSON object covering two tasks:
1. Profile extraction — detect display name, year of study, and any corrections to
   previously known attributes.
2. Topic classification — classify the user's message into standard clinical concern topics.

Current User Profile: {profile.profile_json}
Current Name: {profile.display_name}
Current Year of Study: {profile.year_of_study}
Standard Topics: {topics_list}

Turn:
User: {user_msg}
Assistant: {assistant_reply}

Return JSON format:
{{
  "display_name": "extracted_name or null",
  "year_of_study": "extracted_year_of_study or null",
  "profile_updates": {{ "hobby": "value", ... }},
  "corrections": [
    {{ "field": "display_name", "old_value": "...", "new_value": "..." }}
  ],
  "topics": ["Topic1", "Topic2"]
}}
"""
    client = get_azure_client()
    res = client.chat.completions.create(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"}
    )
    data = json.loads(res.choices[0].message.content)

    # 1. Apply display name & year updates
    if data.get("display_name"):
        profile.display_name = data["display_name"]
    if data.get("year_of_study"):
        profile.year_of_study = data["year_of_study"]

    # 2. Merge details into profile_json
    if data.get("profile_updates"):
        merged = dict(profile.profile_json)
        merged.update(data["profile_updates"])
        profile.profile_json = merged

    # 3. Log corrections in ProfileChangeLog
    for corr in data.get("corrections", []):
        db.add(ProfileChangeLog(
            user_id=user_id,
            field_path=corr["field"],
            old_value=corr["old_value"],
            new_value=corr["new_value"],
            source="user_correction",
            session_id=session_id
        ))

    # 4. Topic evolution
    for t in data.get("topics", []):
        topic_row = await db.get(ProfileTopic, (user_id, t))
        if topic_row:
            topic_row.mention_count += 1
            topic_row.last_seen_at = func.now()
        else:
            db.add(ProfileTopic(user_id=user_id, topic=t, mention_count=1))

    # Single commit for the whole merged update (profile + change log + topics)
    await db.commit()
```

#### [NEW] `app/core/session_summarizer.py`
Service to analyze a session's message history using LLM to compile standard clinical summaries.

Exposes one shared, idempotent entrypoint used by **both** `/api/v1/reset` and
`POST /api/v1/chat/end` (see §2 "Triggering Session Summaries" note), so calling both on the
same session never produces two `session_summaries` rows:

```python
async def end_session_and_summarize(session_client_key: str) -> None:
    async with get_session() as db:
        session_row = await db.scalar(
            select(ConversationSession).where(ConversationSession.client_key == session_client_key)
        )
        if not session_row:
            return
        if session_row.ended_at is not None:
            return  # already ended/summarized by the other trigger path — no-op

        # ... load messages, generate summary via LLM, write SessionSummary ...

        session_row.ended_at = func.now()
        await db.commit()
```

---

### Router Layer (APIs)

#### [MODIFY] `app/routers/chat.py`
1. In `/api/v1/chat`, after the reply is finalized (and before the response is returned),
   `await process_post_chat_updates(...)` directly — synchronous, not scheduled via
   `BackgroundTasks` (see §2 "RESOLVED" note).
2. Update `/api/v1/reset`: call `end_session_and_summarize()` first (writes the summary, sets
   `ended_at`), *then* proceed with the existing delete-messages/session behavior unchanged.
3. **[NEW] Endpoint `POST /api/v1/chat/end`**: calls the same `end_session_and_summarize()`,
   marks the session ended, generates the summary — without deleting anything (unlike `/reset`).
4. **[NEW] Endpoint `POST /api/v1/profile/forget`**: Soft-deletes user session summaries (`deleted_at = now()`) and resets `UserProfile` entries to comply with PDPO guidelines (Q6).

---

## 5. Verification Plan

### Automated Tests
We will write a comprehensive new integration test suite `tests/test_phase2_profiles.py` mimicking the student journey:
1. First message: Student chats about studies and relationship stress.
2. Post-processing verification: Assert `user_profiles` contains study year/name updates, and `profile_topics` registers topic hits — synchronously available immediately after the `/chat` call returns (no polling/waiting needed, since this no longer runs in the background).
3. Second message: Student mentions "上次" (last time).
4. Prompt check: Verify that context assembly retrieves and injects profile info and correct summaries into the L2 prompt.
5. Soft-deletion: Call `POST /api/v1/profile/forget` and verify summaries are flagged as soft-deleted (`deleted_at` is set) and profile metrics are cleared.
6. **FK fix**: Reset a session that has a `session_summaries` row → confirm the summary row still exists afterward with `session_id = NULL` (proves `ON DELETE SET NULL` actually works, not just that the code compiles).
7. **Idempotency**: Call `POST /api/v1/chat/end` then `/api/v1/reset` on the same session → confirm exactly **one** `session_summaries` row exists, not two.
8. **Single LLM call**: Assert the merged `analyze_and_update_profile()` path makes exactly one call to the LLM client per chat turn (e.g. by mocking/counting calls to `get_azure_client()`), not two.

```bash
# Execute Phase 2 test suite
pytest tests/test_phase2_profiles.py -v
```

### Manual Verification
1. Run local uvicorn server on Windows:
   ```powershell
   uvicorn app.main:app --reload
   ```
2. Interact using `/api/v1/chat` simulating "考試好焦慮" (Anxious about exams).
3. Query database tables: `user_profiles` and `profile_topics` to verify automatic extraction succeeded.
4. Issue a reset call `/api/v1/reset` and confirm `session_summaries` successfully saved the summary of the exam anxiety conversation.
5. Begin a new chat session and say "好似上次咁好擔心" (Like what I was worried about last time). Confirm that the bot replies with contextual knowledge of the previous anxiety.
