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

以下問題是在對照現有 codebase（`app/storage/postgres_store.py`、`app/core/persona_resolver.py`）與 `CLAUDE.md` 的部署限制後發現的，實作前已修正／已決定，記錄如下（沿用 Phase 2 計畫文件的做法，見 `Phase2_implement_plan_Antigravity.md` §2）：

> [!IMPORTANT]
> **Example Injection Formats (`style_learning` vs `direct_quote`)**
> - `style_learning`: Instructs the LLM to learn and match the tone, empathy, and phrasing of the clinical example without copying it verbatim.
> - `direct_quote`: Injects the example as a recommended template, allowing the LLM to directly quote or heavily adapt the response to the student.
> *Both patterns are supported in our prompt design below. No change needed here.*

> [!WARNING]
> **RESOLVED — Attribution（T-Q20）改用固定格式引用標記，不交給 LLM 自然帶出**
> 原計畫留了一個開放問題：`attribution_mode = 'attributed'` 時，要嘛在 prompt 裡指示 LLM
> 自然帶出來源（例如「臨床心理師曾建議...」），要嘛用固定格式附加引用標記。
>
> **決定：採用固定格式、由程式碼決定性附加，不依賴 LLM 指令遵循。** 理由：這個專案從
> L1/L3 Regex 閘門到三明治 Prompt 的 `SAFETY_CORE` 固定不變，一貫的工程原則是「能用決定性
> 邏輯做的事，不要交給 LLM 自由發揮」——LLM 是否真的照 prompt 指示帶出歸屬語句無法保證，
> 也難以寫成穩定的自動化測試斷言（字串比對 LLM 自然語言輸出容易 flaky）。改成在 L2 產出
> 回覆後、寫入 memory 前，由 `chat.py` 用固定字串決定性附加（見 §3 `[MODIFY] app/routers/chat.py`），
> 可以直接在 `tests/test_phase3_examples.py` 斷言精確字串是否存在。
>
> 範圍限定：只有當本輪注入的範例中，存在 `usage_mode='direct_quote'` **且**
> `attribution_mode='attributed'` 的範例時才附加（`style_learning` 只是風格參考，不是逐字引用，
> 附加「引用自」字樣語意不通，因此不附加）。若同時有多個符合條件的範例，只附加一次，不重複。
> 因此原本規劃「在 prompt 裡插入 `Note: For Example {idx}...`」這段指令整段拿掉，改由
> `app/routers/chat.py` 決定性處理（順帶解決下面第 3 點的 f-string bug，因為這段程式碼直接不存在了）。

---

## 3. Proposed Changes

---

### Component A: Phase 3 — Example Library

We will introduce `app/db/models_example.py` for database schemas, create the Alembic migration, build the Example Selector, and implement prompt injection.

> [!WARNING]
> **RESOLVED — `example_usage_log.message_id` 會被 `/api/v1/reset` 波及（跟 Phase 2 的
> `session_summaries` 是同一類坑）**
> `PostgresConversationStore.reset()`（`app/storage/postgres_store.py:112`）會直接 `db.delete(session_row)`，
> 底下的 `messages` 靠 `sessions.messages` 的 `ON DELETE CASCADE` 連帶刪除。原計畫的
> `example_usage_log.message_id` 寫成 `NOT NULL REFERENCES messages(id)`（架構文件 DDL 沒寫
> `ondelete`，SQLAlchemy 版本寫的是 `ondelete="CASCADE"`）：
> - 沒寫 `ondelete`（預設 `NO ACTION`/`RESTRICT`）→ `/api/v1/reset` 刪除 session 時，Postgres
>   會因為外鍵限制擋下對 `messages` 的刪除，**整個 `/reset` 直接失敗**。
> - 寫 `ondelete="CASCADE"` → messages 被刪，`example_usage_log` 跟著被連坐刪除，T-Q18 的
>   有效性追蹤資料永遠留不住。
>
> **決定**：`message_id` 改成可為 `NULL` + `ON DELETE SET NULL`，並額外記下 `session_id`
> （同樣 `ON DELETE SET NULL`）和 `user_id`（`ON DELETE CASCADE`，使用者本身被刪除時才真的清除）。
> 這跟 Phase 2 `session_summaries.session_id` 的處理方式一致（見
> `CLINICAL_FRAMEWORK_ARCHITECTURE.md` §2.1 的既有註記），已同步修改該檔案的 DDL。
>
> [!WARNING]
> **RESOLVED — `ConversationStore.append()` 目前回傳 `None`，寫入使用記錄時拿不到 `message_id`**
> 上面這個 FK 改成 nullable 之後，理論上不寫 `message_id` 也不會出錯，但既然要做 T-Q18
> 的有效性追蹤，還是應該盡量記下是「哪一則回覆」用了這個範例。目前
> `ConversationStore.append()`（Protocol + `InMemoryConversationStore` + `PostgresConversationStore`）
> 回傳型別是 `None`，呼叫端（`chat.py`）拿不到剛寫入那筆 `messages` 的 id。
>
> **決定**：把 `append()` 的回傳型別改成 `str | None`（`PostgresConversationStore` 回傳新建
> `Message.id` 的字串形式；`InMemoryConversationStore` 沒有結構化 id 可用，跟現有
> `persona_id` 參數一樣靜默回傳 `None`；角色/內容不合法被跳過時也回傳 `None`）。這是
> `ConversationStore` Protocol 的簽章變更，三個實作檔都要同步改，`chat.py` 呼叫
> `append(..., role="assistant", ...)` 時取得回傳值供 Example Selector 使用記錄寫入。

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
    # 刻意可為 NULL + SET NULL，不是 NOT NULL + CASCADE：/api/v1/reset 會連帶刪除 messages，
    # 寫死 NOT NULL 會讓 /reset 因外鍵限制失敗；寫 CASCADE 則使用記錄會跟著憑空消失（見上方 §3 說明）。
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    used_at: Mapped[datetime] = mapped_column(server_default=func.now())
    effectiveness_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
```

#### [NEW] `app/core/condition_matching.py`

> [!WARNING]
> **RESOLVED — 比對條件的邏輯不能在 Example Selector 重寫一份**
> `app/core/persona_resolver.py:136` 已經有 `_condition_matches()`（比對
> `{"year_of_study": ..., "topics_include": [...]}` 這種條件），如果範例選擇器另寫一份
> `_match_conditions()`，兩邊的比對規則（例如主題門檸 `>= 3`、未知條件鍵的處理方式）
> 會隨著兩邊各自改動而慢慢不一致。Phase 4 的 Rule Engine 也需要同一套比對邏輯
> （原計畫的 `_rule_matches()` 又是第三份重複）。
>
> **決定**：把 `_condition_matches()` 與 `EVOLVED_TOPIC_THRESHOLD` 從 `persona_resolver.py`
> 抽出到新檔 `app/core/condition_matching.py`，`persona_resolver.py`、`example_selector.py`、
> 未來的 `rule_engine.py` 都從這裡 import。`persona_resolver.py` 本身行為不變（只是
> 把實作移位，本身已合併進 `main`，這是小幅重構，不是新功能）。

```python
# 共用的「條件 JSON 比對」邏輯，被 persona_resolver.py、example_selector.py，
# 未來 rule_engine.py 一起使用，避免三份邏輯各自漂移。

EVOLVED_TOPIC_THRESHOLD = 3  # 主題累積達到這個次數才視為「已演化」的核心主題


def condition_matches(condition_json: dict, profile_row, evolved_topics: set[str]) -> bool:
    """
    比對單一條件 JSON（例：{"year_of_study": "Year 1", "topics_include": ["社交焦慮"]}）。
    所有出現的鍵都必須成立（AND）；空字典或含未知鍵一律視為不匹配（安全預設，
    避免設定打錯字反而意外匹配到所有人）。
    """
    if not condition_json:
        return False

    for key, value in condition_json.items():
        if key == "year_of_study":
            if profile_row is None or profile_row.year_of_study != value:
                return False
        elif key == "topics_include":
            if not evolved_topics.intersection(value or []):
                return False
        else:
            return False  # 未知條件鍵，安全預設不匹配

    return True
```

`app/core/persona_resolver.py` 的 `_condition_matches()` 改為從這個模組 re-export
（`from app.core.condition_matching import condition_matches as _condition_matches`），
呼叫端（`_match_persona_by_conditions`）不需要改動。

#### [NEW] `app/core/example_selector.py`
Determines which Response Examples match the student's active profile and topics.

> [!WARNING]
> **RESOLVED — 命中的範例數量沒上限，會全部塔進 prompt**
> 原計畫的 `select_applicable_examples()` 回傳所有符合條件的範例，沒有上限。若同時
> 有十幾則範例命中，會把 prompt 擐得很長，也稀釋每則範例對 LLM 的影響力。
>
> **決定**：設上限 `EXAMPLE_MATCH_LIMIT = 2`，並依「條件特定程度」排序——因為
> `condition_matches()` 是 AND 邏輯，一條範例的 `applicable_conditions_json` 鍵數越多，
> 代表它要求的條件越具體、越不容易誤命中，優先使用；鍵數相同時用 `created_at`
> 時間較新的優先（沿用相同依據：目前 `response_examples` 還沒有独立的 `priority`
> 欄位，不引進新欄位也能決定性排序，若之後發現不夠用再考慮新增 `priority` 欄位）。
> 並在 `app/prompts/system_prompt.py` 實作筆記里說明：範例區塊只插入在 `context_blocks`
> 這層（跟 `profile_text`/`past_summaries_text` 同層），**不改動 `TOP_LAYER`（persona）與
> `BOTTOM_LAYER`（`SAFETY_CORE`）**，符合 `CLAUDE.md` 鐵律：persona/規則等功能不得讓
> `SAFETY_CORE` 被稀釋或覆寫。

```python
import os
from sqlalchemy import select
from app.core.condition_matching import EVOLVED_TOPIC_THRESHOLD, condition_matches
from app.db import get_session
from app.db.models import User
from app.db.models_profile import UserProfile, ProfileTopic
from app.db.models_example import ResponseExample

EXAMPLE_MATCH_LIMIT = 2  # 單輪對話最多注入幾則範例，避免 prompt 過長、稀釋範例的影響力


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

        # Load evolved topics（跟 persona_resolver.py 用同一個門檻常數，兩邊判斷「已演化」的定義一致）
        topic_result = await db.execute(
            select(ProfileTopic.topic).where(
                ProfileTopic.user_id == user_row.id,
                ProfileTopic.mention_count >= EVOLVED_TOPIC_THRESHOLD,
            )
        )
        evolved_topics = set(topic_result.scalars().all())

        # Load active examples
        examples_result = await db.execute(
            select(ResponseExample).where(ResponseExample.status == "active")
        )
        all_active = examples_result.scalars().all()

        matched = [
            example
            for example in all_active
            if condition_matches(example.applicable_conditions_json, profile_row, evolved_topics)
        ]

        # 依「條件特定程度」（條件鍵數量）由高到低排序，同分時新的範例優先，取前 EXAMPLE_MATCH_LIMIT 則
        matched.sort(
            key=lambda ex: (len(ex.applicable_conditions_json), ex.created_at),
            reverse=True,
        )
        return matched[:EXAMPLE_MATCH_LIMIT]
```

#### [MODIFY] `app/prompts/system_prompt.py`
Extend `build_prompt` to accept matching Response Examples and format them with distinct instructions.

> [!WARNING]
> **RESOLVED — 原計畫的 attribution 提示字串少了 f 前綴，且改用決定性引用後已不需要**
> 原計畫的 `"Note: For Example {idx}..."` 沒加 `f` 前綴，`{idx}` 會原樣送進 prompt，
> LLM 收到的會是字面上的 `Example {idx}` 而不是實際編號。但根據上方 §2 的決定（attribution
> 改由 `chat.py` 決定性附加），這段 attribution 提示字串整段不再需要，下面直接拿掉，
> 不是修 bug。

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

    # Inject Examples（只影響中段 context_blocks，不觸碰 TOP_LAYER 的 persona_fragment
    # 或 BOTTOM_LAYER 的 SAFETY_CORE —— 呼應 CLAUDE.md 鐵律，見上方 §3 說明）
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
            # 不在這裡注入 attribution 指令 —— 引用標記改由 chat.py 在 LLM 產出回覆後
            # 決定性附加（見 §2 RESOLVED 段落），不依賴 LLM 是否遵循 prompt 指示。

        context_blocks.append("\n".join(example_blocks))

    # Reassemble top layer...
```

#### [MODIFY] `app/storage/conversation_store.py` + `in_memory_store.py` + `postgres_store.py`
`ConversationStore.append()` 的回傳型別從 `None` 改成 `str | None`（新寫入的 message id）：

```python
# app/storage/conversation_store.py（Protocol）
class ConversationStore(Protocol):
    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        ...

    async def append(
        self, session_id: str, role: str, content: str, persona_id: str | None = None
    ) -> str | None:
        ...

    async def reset(self, session_id: str) -> None:
        ...
```

```python
# app/storage/postgres_store.py（節錄，append() 內）
async def append(
    self, session_id: str, role: str, content: str, persona_id: str | None = None
) -> str | None:
    if role not in ("user", "assistant"):
        return None

    text = content.strip()
    if not text:
        return None

    async with get_session() as db:
        session_row = await self._get_or_create_session(db, session_id)
        message = Message(
            session_id=session_row.id,
            role=role,
            content=text,
            persona_id=uuid.UUID(persona_id) if persona_id else None,
        )
        db.add(message)
        await db.flush()  # 取得 DB 產生的 message.id，還不需要 commit 後才能讀
        message_id = str(message.id)
        await db.commit()
        return message_id
```

```python
# app/storage/in_memory_store.py（節錄，append() 內）
# InMemoryConversationStore 沒有結構化的 message id 可用，跟現有 persona_id 參數
# 一樣靜默忽略，統一回傳 None（呼叫端本來就只在 DATABASE_URL 有設定、examples 非空時
# 才會用到這個回傳值，InMemory 模式下 select_applicable_examples() 一定回傳 []）。
async def append(
    self, session_id: str, role: str, content: str, persona_id: str | None = None
) -> str | None:
    if role not in ("user", "assistant"):
        return None
    text = content.strip()
    if not text:
        return None
    with self._lock:
        self._prune_expired()
        bucket = self._sessions.setdefault(session_id, [])
        bucket.append({"role": role, "content": text})
    return None
```

#### [MODIFY] `app/routers/chat.py`
在既有五層防禦 pipeline 之外，加入範例選取、使用記錄寫入、以及決定性引用附加：

```python
from app.core.example_selector import select_applicable_examples

# ...在 assemble_context() 之後、呼叫 chat_with_llm() 之前：
examples = await select_applicable_examples(session_id) if session_id else []
examples_payload = [
    {
        "usage_mode": ex.usage_mode,
        "attribution_mode": ex.attribution_mode,
        "content": ex.content,
    }
    for ex in examples
]

llm_reply = chat_with_llm(
    user_message,
    conversation_history=history,
    security_hint=security_hint,
    persona_name=persona.name,
    persona_fragment=persona.system_prompt_fragment,
    profile_text=context.profile_text,
    past_summaries_text=context.past_summaries_text,
    examples=examples_payload,
)

# ...Layer 3 Output Gateway 檢查完、決定 final_reply 之後，寫入 memory 之前：
# 決定性附加引用標記（T-Q20，見上方 §2 RESOLVED）：
# 只要本輪注入的範例中，有任何一則是 direct_quote + attributed，就附加一次固定格式標記，
# 不管有幾則符合都只附加一次。
ATTRIBUTION_TAG = "\n\n— 部分內容參考自臨床心理師建議"
if any(ex["usage_mode"] == "direct_quote" and ex["attribution_mode"] == "attributed" for ex in examples_payload):
    final_reply = final_reply + ATTRIBUTION_TAG

# ...寫入 memory 時，append() 現在會回傳 message_id（見上方 §2 RESOLVED）：
if session_id:
    await conversation_store.append(session_id, "user", user_message, persona_id=persona.id)
    assistant_message_id = await conversation_store.append(
        session_id, "assistant", final_reply, persona_id=persona.id
    )
    if examples and assistant_message_id:
        await record_example_usage(session_id, assistant_message_id, examples)
```

`record_example_usage()`（放在 `app/core/example_selector.py`）依 `session_client_key` 查出
`user_id`，替本輪注入的每一則範例各寫一筆 `example_usage_log`（`message_id`/`session_id`/`user_id`
都寫入，`effectiveness_feedback` 留空，之後由治療師人工標註或接 `feedback_tags`）。

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
    # NOTE（Phase 4 動工時處理，這裡先不動）：year_of_study / topics_include 這兩個鍵的比對
    # 邏輯應改呼叫 app/core/condition_matching.py 的 condition_matches()，不要維持第三份重複
    # 邏輯；risk_level / message_count_at_least 是 rule 專屬的額外鍵，屆時再擴充共用函式或在這裡
    # 疊加判斷。此處僅記錄設計意圖，Phase 3 階段不需要真的修改這段程式碼。
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
   - **（本次修正新增的斷言）**
     - 呼叫 `/api/v1/reset` 後，該 session 產生過的 `example_usage_log` 資料列仍然存在，且
       `message_id`/`session_id` 已被 `SET NULL`（驗證 FK cascade 修正真的生效，`/reset` 本身
       不噴錯）。
     - `select_applicable_examples()` 在符合條件的範例超過 `EXAMPLE_MATCH_LIMIT` 時，只回傳
       上限數量，且排序符合「條件鍵數多者優先」。
     - `conversation_store.append(..., role="assistant", ...)` 對 Postgres 實作回傳非 None
       的字串 id；對 InMemory 實作回傳 `None`。
     - 命中 `direct_quote` + `attributed` 範例時，`final_reply` 結尾包含固定的
       `ATTRIBUTION_TAG`；命中 `style_learning` 範例（不論 attribution_mode 為何）時不附加。
     - `app.core.persona_resolver._condition_matches` 與 `app.core.example_selector` 對同一組
       `condition_json` 給出一致的比對結果（間接驗證兩邊共用 `condition_matching.py`，不是各自
       維護一份）。
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

### 合併前的 Supabase 正式環境驗證（建議，非本次程式碼變更範圍）

Phase 1、Phase 2 目前都只驗證過本機 Docker Postgres，尚未在 Supabase 正式環境個別驗證過
（見 `docs/CLINICAL_FRAMEWORK_TASKS.md` 對應章節）。Phase 3 會再疊加一個新 migration
（`response_examples` / `example_usage_log`）。建議在 `upgrade/phase3` merge 進 `main` 之前，
找一次時間把三個 Phase 的驗證債務一次補完，而不是等三個 Phase 疊在一起才發現問題：

1. 用 Supabase **Session Pooler**（port 5432，非正式環境用的 Transaction Pooler）在本機執行
   `alembic upgrade head`，確認 Phase 1/2/3 的 migration 都能在 Supabase 上乾淨套用。
2. 對正式環境（或指向 Supabase 的本機服務）實際打一次
   `POST /api/v1/admin/personas/assign`，確認 Phase 1 的手動指派流程在 Supabase 上如預期運作。
3. 跑一次完整對話流程，確認 Phase 2 的 profile / topics / session_summaries 寫入正常，
   再確認 Phase 3 的 `response_examples` 選取與 `example_usage_log` 寫入正常。

