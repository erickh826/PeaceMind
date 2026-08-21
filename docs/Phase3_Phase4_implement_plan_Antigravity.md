# PeaceMind Phase 3 & Phase 4 Implementation Plan: Example Library & Rule Engine

This document outlines the design and step-by-step implementation plan for **Phase 3 (範例庫, T-Q16–T-Q19)** and **Phase 4 (規則引擎, T-Q1–T-Q5)** of the PeaceMind Clinical Framework, compiled by **Antigravity**.

---

## 1. Goal Description

### Phase 3 — 範例庫 (Example Library)
The objective is to establish an **Example Library** that clinical supervisors can populate with high-quality, professional therapist responses. When a student's context matches an example's criteria, the system will inject the example into the L2 prompt to guide the LLM's styling or to supply direct quotations. This allows real-world clinical expertise to steer the AI's conversation in a controlled, contextual manner.

### Phase 4 — 規則引擎 (Rule Engine)
The **Rule Engine** acts as the central orchestrator of the clinical framework. Instead of independent resolver logic, rules dynamically coordinate Personas, Examples, Therapy Styles, and Tones based on the student's background, active topics, risk profile, and history. 

---

## 2. User Review Required

Please review the following architectural and design decisions:

> [!IMPORTANT]
> **Example Injection Formats (`style_learning` vs `direct_quote`)**
> - `style_learning`: Instructs the LLM to learn and match the tone, empathy, and phrasing of the clinical example without copying it verbatim.
> - `direct_quote`: Injects the example as a recommended template, allowing the LLM to directly quote or heavily adapt the response to the student.
> *Both patterns are supported in our prompt design below.*

> [!WARNING]
> **Attribution Note Injection (T-Q20)**
> If `attribution_mode` is `'attributed'`, how should the student see the citation? We propose telling the LLM via prompt context to naturally introduce the source (e.g., *"臨床心理師曾建議，在面對這種情況時..."*), or appending a structured citation tag to the response.

---

## 3. Proposed Changes

---

### Component A: Phase 3 — Example Library

We will introduce `app/db/models_example.py` for database schemas, create the Alembic migration, build the Example Selector, and implement prompt injection.

#### [NEW] `app/db/models_example.py`
Defines the SQLAlchemy tables for examples and usage logs.

```python
import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class ResponseExample(Base):
    __tablename__ = "response_examples"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Matching rules, e.g. {"year_of_study": "Year 1", "topics_include": ["Relationship"]}
    applicable_conditions_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    usage_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="style_learning")
    attribution_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="anonymous")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("usage_mode IN ('style_learning', 'direct_quote')", name="ck_response_examples_usage_mode"),
        CheckConstraint("attribution_mode IN ('anonymous', 'attributed')", name="ck_response_examples_attribution_mode"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_response_examples_status"),
    )

class ExampleUsageLog(Base):
    __tablename__ = "example_usage_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    example_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("response_examples.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    used_at: Mapped[datetime] = mapped_column(server_default=func.now())
    effectiveness_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
```

#### [NEW] `app/core/example_selector.py`
Determines which Response Examples match the student's active profile and topics.

```python
import os
from sqlalchemy import select
from app.db import get_session
from app.db.models import User
from app.db.models_profile import UserProfile, ProfileTopic
from app.db.models_example import ResponseExample

async def select_applicable_examples(session_client_key: str) -> list[ResponseExample]:
    if not os.environ.get("DATABASE_URL") or not session_client_key:
        return []

    async with get_session() as db:
        user_row = await db.scalar(
            select(User).where(User.external_ref == session_client_key)
        )
        if not user_row:
            return []

        profile_row = await db.get(UserProfile, user_row.id)
        
        # Load evolved topics
        topic_result = await db.execute(
            select(ProfileTopic.topic).where(
                ProfileTopic.user_id == user_row.id,
                ProfileTopic.mention_count >= 3
            )
        )
        evolved_topics = set(topic_result.scalars().all())

        # Load active examples
        examples_result = await db.execute(
            select(ResponseExample).where(ResponseExample.status == "active")
        )
        all_active = examples_result.scalars().all()

        matched = []
        for example in all_active:
            conds = example.applicable_conditions_json
            if _match_conditions(conds, profile_row, evolved_topics):
                matched.append(example)
        return matched

def _match_conditions(conditions: dict, profile, evolved_topics: set[str]) -> bool:
    if not conditions:
        return False
    for key, value in conditions.items():
        if key == "year_of_study":
            if profile is None or profile.year_of_study != value:
                return False
        elif key == "topics_include":
            if not evolved_topics.intersection(value or []):
                return False
    return True
```

#### [MODIFY] `app/prompts/system_prompt.py`
Extend `build_prompt` to accept matching Response Examples and format them with distinct instructions:

```python
def build_prompt(
    user_message: str,
    persona_name: str = "Boon",
    persona_fragment: str = DEFAULT_PERSONA_FRAGMENT,
    security_hint: str | None = None,
    profile_text: str | None = None,
    past_summaries_text: str | None = None,
    examples: list[dict] = None,  # List of matching examples with usage_mode and content
) -> str:
    # Context Assembly...
    context_blocks = []
    if profile_text:
        context_blocks.append(profile_text)
    if past_summaries_text:
        context_blocks.append(past_summaries_text)

    # Inject Examples
    if examples:
        example_blocks = ["[CLINICAL RESPONSE EXAMPLES]"]
        for idx, ex in enumerate(examples, 1):
            if ex["usage_mode"] == "style_learning":
                example_blocks.append(
                    f"Example {idx} (Style Learning Reference):\n"
                    f"Please adapt your tone, empathy, and structure to match this clinical style. Do not copy words verbatim:\n"
                    f"\"\"\"\n{ex['content']}\n\"\"\""
                )
            elif ex["usage_mode"] == "direct_quote":
                example_blocks.append(
                    f"Example {idx} (Direct Phrasing Reference):\n"
                    f"This is an exemplary response. You may adapt, incorporate, or directly quote this phrasing if appropriate:\n"
                    f"\"\"\"\n{ex['content']}\n\"\"\""
                )
            if ex.get("attribution_mode") == "attributed":
                example_blocks.append("Note: For Example {idx}, ensure you attribute this wisdom or clinical advice naturally to a 'clinical counselor' (臨床心理師).")
        
        context_blocks.append("\n".join(example_blocks))

    # Reassemble top layer...
```

#### [NEW] `app/routers/admin_examples.py`
Endpoints for clinical supervisors to CRUD response examples.

- `POST /api/v1/admin/examples`: Create a response example.
- `GET /api/v1/admin/examples`: List response examples.
- `PATCH /api/v1/admin/examples/{id}`: Modify or archive an example.

---

### Component B: Phase 4 — Rule Engine

We will introduce `app/db/models_rule.py` to support rules, rule versions, and the rule evaluation logic.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Rule Matching Engine                            │
│  Loads active rules, evaluates conditions_json against student state  │
├───────────────────────────────────┬────────────────────────────────────┤
│                                   │ Selects Highest Priority Match
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                          Apply Rule Action                             │
│  - Overwrites resolved Persona                                         │
│  - Sets custom Therapy Style and Tone                                  │
│  - Restricts or specifies custom Response Examples                     │
└────────────────────────────────────────────────────────────────────────┘
```

#### [NEW] `app/db/models_rule.py`
Defines tables `rules` and `rule_versions`.

```python
import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class ClinicalRule(Base):
    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    conditions_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    actions_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="immediate")
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active', 'archived')", name="ck_rules_status"),
        CheckConstraint("scope IN ('new_conversations_only', 'immediate')", name="ck_rules_scope"),
    )

class RuleVersion(Base):
    __tablename__ = "rule_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    conditions_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    actions_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("therapists.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

#### [NEW] `app/core/rule_engine.py`
Implements the condition-matching and action override engine.

```python
import os
from sqlalchemy import select
from app.db import get_session
from app.db.models import User, ConversationSession
from app.db.models_profile import UserProfile, ProfileTopic
from app.db.models_rule import ClinicalRule

async def evaluate_rules(session_client_key: str) -> dict | None:
    """
    Evaluates rules and returns the actions dict of the highest-priority matching rule.
    Returns None if no rules match.
    """
    if not os.environ.get("DATABASE_URL") or not session_client_key:
        return None

    async with get_session() as db:
        # Load user context
        session_row = await db.scalar(
            select(ConversationSession).where(ConversationSession.client_key == session_client_key)
        )
        if not session_row:
            return None
        
        user_row = await db.get(User, session_row.user_id)
        profile_row = await db.get(UserProfile, user_row.id)
        
        # Count total messages in active session
        from app.db.models import Message
        msg_count = await db.scalar(
            select(func.count(Message.id)).where(Message.session_id == session_row.id)
        )

        topic_result = await db.execute(
            select(ProfileTopic.topic).where(
                ProfileTopic.user_id == user_row.id,
                ProfileTopic.mention_count >= 3
            )
        )
        evolved_topics = set(topic_result.scalars().all())

        # Load active rules ordered by priority DESC
        rules_result = await db.execute(
            select(ClinicalRule)
            .where(ClinicalRule.status == "active")
            .order_by(ClinicalRule.priority.desc())
        )
        active_rules = rules_result.scalars().all()

        for rule in active_rules:
            if _rule_matches(rule.conditions_json, profile_row, evolved_topics, msg_count):
                return rule.actions_json
        return None

def _rule_matches(conds: dict, profile, evolved_topics: set[str], msg_count: int) -> bool:
    if not conds:
        return False
    
    for key, value in conds.items():
        if key == "year_of_study":
            if profile is None or profile.year_of_study != value:
                return False
        elif key == "topics_include":
            if not evolved_topics.intersection(value or []):
                return False
        elif key == "risk_level":
            if profile is None or profile.risk_level != value:
                return False
        elif key == "message_count_at_least":
            if msg_count < value:
                return False
    return True
```

---

## 4. Verification Plan

### Automated Tests

We will create two dedicated verification suites:
1. `tests/test_phase3_examples.py`:
   - Seed custom Response Examples.
   - Mock LLM calls and verify correct selection of examples and correct style-instructions inside the L2 sandwich prompt.
   - Assert usage is written into `example_usage_log`.
2. `tests/test_phase4_rules.py`:
   - Seed clinical rules with priority levels.
   - Verify rules override Personas and Tones correctly when matching criteria (e.g. Study Year and topics).
   - Assert rule execution is safe-gated.

```bash
# Run Phase 3 & 4 tests
pytest tests/test_phase3_examples.py -v
pytest tests/test_phase4_rules.py -v
```

### Manual Verification
1. Open the Admin Console / Swagger docs, create a `Response Example` of type `style_learning` targeting `Relationship` topic.
2. Seed 3 messages mentioning relationship stress to trigger topic evolution.
3. Observe in server console logs (or response) that the LLM response adapts to mimic the empathy and phrasing of the added therapist example!
