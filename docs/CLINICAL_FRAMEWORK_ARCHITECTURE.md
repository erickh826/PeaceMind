# 治療師介入框架 — 資料模型與架構設計
**專案**：PeaceMind — 「阿本 / Boon」
**基於**：現有 Phase 5 架構（三層防禦 + InMemory Session Memory）
**目標**：落地四個 User Story 場景（Admin Console / Human-in-the-loop / Persona / 範例庫）
**狀態**：設計草案，待逐步實作

---

## 0. 為什麼要先做這個設計

現有系統的記憶是 `InMemoryConversationStore`：存在 process 記憶體裡，TTL 60 分鐘，且部署在 Vercel Serverless（`api/index.py` → `app.main.app`）。Serverless instance 本身就是無狀態、隨時可能被回收的，所以：

- 現在的「記憶」在生產環境並不可靠（reload / 冷啟動就消失）
- 四個 User Story 全部依賴「持久化、可查詢、可追溯」的資料 — Profile、Persona、Rule、Example、Risk 都不能活在 process 記憶體裡

**結論：這次要做的第一件事不是某個功能，而是引入一個真正的資料庫。** `frontend/shared/schema.ts` 裡已經有 Drizzle + PostgreSQL 的 scaffolding（雖然目前是空的樣板），代表 Postgres 是最順的路徑。後端目前用 Python/FastAPI，建議用 **SQLAlchemy + asyncpg**（或 psycopg3）對接同一個 Postgres，不需要在 Node 和 Python 之間重複定義兩套 schema — Python 端是 source of truth，Drizzle schema 之後可視情況同步或直接棄用。

---

## 1. 整體模組架構（在現有五層防禦上疊加）

```
                         ┌─────────────────────────┐
                         │   Admin Console (新)     │  ← 治療師登入後台
                         │  - 規則 builder           │
                         │  - Persona 管理           │
                         │  - 範例庫管理             │
                         │  - Oversight Dashboard    │
                         └───────────┬───────────────┘
                                     │ REST API (/api/v1/admin/*)
                                     ▼
┌────────────────────────────────────────────────────────────────────┐
│                         FastAPI App                                  │
│                                                                        │
│  使用者輸入                                                            │
│      │                                                                │
│      ▼                                                                │
│  L1a Input Gateway → L1b Semantic Gateway → L1c Multi-turn Scorer     │
│      │ 通過                                                           │
│      ▼                                                                │
│  ┌──────────────────────────────────────────────┐                    │
│  │  Context Assembly Service（新）                │                    │
│  │  1. 讀取 User Profile（含主題標籤演化）          │                    │
│  │  2. 讀取跨 Session 摘要（相關的 summary_text）   │                    │
│  │  3. Persona Resolver → 決定用哪個 Persona       │                    │
│  │  4. Rule Engine → 比對條件 → 命中規則的 action  │                    │
│  │  5. Example Selector → 依規則/條件挑選範例       │                    │
│  └───────────────────┬──────────────────────────┘                    │
│                       ▼                                               │
│  L2 LLM Core（Sandwich Prompt，改為動態組裝：                          │
│              Persona + Rule Action + Examples 注入）                   │
│                       │                                               │
│                       ▼                                               │
│  L3 Output Gateway                                                    │
│                       │                                               │
│                       ▼                                               │
│  ┌──────────────────────────────────────────────┐                    │
│  │  Post-processing（新）                         │                    │
│  │  - 寫入 Message + Session                       │                    │
│  │  - 更新 Profile（偵測更正 / 主題演化）           │                    │
│  │  - Crisis Escalation（分層：熱線→通知→強制轉真人）│                    │
│  │  - Persona 切換記錄                             │                    │
│  │  - Example 使用記錄                             │                    │
│  └──────────────────────────────────────────────┘                    │
└────────────────────────────────────────────────────────────────────┘
```

**Context Assembly Service** 是這次設計的核心新模組 — 它是現有 `chat.py` 在呼叫 `chat_with_llm()` 之前，新增的一個步驟，負責把 Profile / Persona / Rule / Example 组裝成最終要注入 `system_prompt.py` 的內容。這樣做的好處：規則引擎、Persona、範例庫彼此獨立開發測試，最後只在這一層匯合，不會互相污染既有的三層防禦邏輯。

---

## 2. 資料模型（PostgreSQL DDL）

以下依四個 Story 分區塊，但共用同一個 schema。所有 `id` 用 UUID，時間戳一律 `timestamptz`。

### 2.1 學生旅程（Q1–Q17）：Profile / Memory / Resource

```sql
-- 使用者（學生），可先用匿名 device/session 綁定，之後可接真實帳號
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_ref    TEXT UNIQUE,              -- 學號 / SSO id，可為 NULL（訪客）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Q1/Q2/Q17: 結構化 Profile，可動態更新，每次對話載入 system prompt
CREATE TABLE user_profiles (
    user_id         UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    year_of_study   TEXT,                     -- 例：Year 1 / Year 4
    display_name    TEXT,
    risk_level      TEXT NOT NULL DEFAULT 'none' CHECK (risk_level IN ('none','low','medium','high')),
    profile_json    JSONB NOT NULL DEFAULT '{}',  -- 彈性欄位：家庭狀況、關注事項等
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Q2: Profile 變更歷史，區分「使用者更正」vs「系統推斷」，供 Q15/Q16 回溯真實性用
CREATE TABLE profile_change_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    field_path      TEXT NOT NULL,            -- 例："profile_json.relationship_status"
    old_value       TEXT,
    new_value       TEXT,
    source          TEXT NOT NULL CHECK (source IN ('user_correction','system_inferred','therapist_edit')),
    session_id      UUID REFERENCES sessions(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Q3: 主題標籤，帶次數計數，用於「演化」判斷（例如 >=3 次觸發新主題）
CREATE TABLE profile_topics (
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic           TEXT NOT NULL,            -- 例："感情關係"
    mention_count   INT NOT NULL DEFAULT 1,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, topic)
);

-- Sessions：一次連續對話（沿用現有 session_id 概念，改為持久化）
CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    persona_id      UUID REFERENCES personas(id),   -- 目前使用的 persona（見 2.3）
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ
);

-- 逐句訊息（取代 InMemoryConversationStore 的 in-process list）
CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content         TEXT NOT NULL,
    persona_id      UUID REFERENCES personas(id),   -- 該則回覆用的 persona（供 T-Q14 分析）
    rule_id         UUID REFERENCES rules(id),       -- 若命中規則
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Q4/Q5/Q6: 跨 Session 結構化摘要（事件 + 情緒 + 策略），非純文字標籤
CREATE TABLE session_summaries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    summary_text    TEXT NOT NULL,
    key_events      JSONB NOT NULL DEFAULT '[]',     -- [{"event": "提到朋友背叛", "topic": "友誼"}]
    emotions        JSONB NOT NULL DEFAULT '[]',     -- [{"emotion": "憤怒", "intensity": "medium"}]
    strategies_used JSONB NOT NULL DEFAULT '[]',     -- [{"strategy": "ACT", "resource_ids": [...]}]
    retention_policy TEXT NOT NULL DEFAULT 'permanent' CHECK (retention_policy IN ('permanent','90d','30d')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ                       -- Q6: 使用者可要求刪除/遺忘（軟刪除，符合 PDPO）
);

-- Q7/Q8/Q9: 資源目錄 + 觀看紀錄 + 回饋
CREATE TABLE resources (
    id              TEXT PRIMARY KEY,          -- 例："M07", "M10"
    title           TEXT NOT NULL,
    category        TEXT NOT NULL,
    level           TEXT NOT NULL DEFAULT 'basic' CHECK (level IN ('basic','advanced')),
    url             TEXT NOT NULL
);

CREATE TABLE resource_views (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id     TEXT NOT NULL REFERENCES resources(id),
    session_id      UUID REFERENCES sessions(id),
    viewed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    feedback        TEXT CHECK (feedback IN ('helpful','not_helpful', NULL))  -- Q9
);
```

### 2.2 危機分級（Q10–Q14）

```sql
-- Q10: 獨立、不可刪除的風險紀錄表，供治療師審閱
CREATE TABLE risk_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id),
    session_id          UUID NOT NULL REFERENCES sessions(id),
    message_id          UUID REFERENCES messages(id),
    trigger_snippet     TEXT NOT NULL,          -- 觸發危機的原文片段
    risk_tier           SMALLINT NOT NULL CHECK (risk_tier IN (1,2,3)),  -- Q12/Q13: 分層
    -- tier 1 = 熱線 + 預設訊息
    -- tier 2 = 同 session 第二次 → 通知 supervisor
    -- tier 3 = 第三次 → 強制轉真人
    supervisor_notified_at TIMESTAMPTZ,
    escalated_to_human_at  TIMESTAMPTZ,
    resolved_by         UUID REFERENCES therapists(id),
    resolution_note      TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
    -- 刻意不設 deleted_at / ON DELETE CASCADE：此表永久保留，應用層禁止刪除
);

-- Q11: 判斷「跟進對話」是否指涉過去的 crisis — 直接查詢同一 user 過去的 risk_events
--（不需要新表，用 user_id + created_at 排序即可在 Context Assembly Service 判斷）
```

**應用層規則（非資料庫，寫在 Rule/Escalation Service）**：
同一個 `session_id` 內，`risk_tier` 依觸發次數遞增：第 1 次固定 tier=1，第 2 次 tier=2（觸發 supervisor 通知），第 3 次以上 tier=3（強制轉真人，UI 顯示轉接訊息並鎖定該 session 的 AI 回覆）。

### 2.3 治療師介入框架（T-Q1–T-Q20）

```sql
CREATE TABLE therapists (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    role            TEXT NOT NULL DEFAULT 'therapist' CHECK (role IN ('therapist','senior_therapist','admin')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- T-Q11–T-Q15: Persona
CREATE TABLE personas (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,               -- 「新生適應型」
    description     TEXT,
    tone            TEXT NOT NULL,                -- 「溫暖接納」「挑戰型」
    response_length TEXT NOT NULL DEFAULT 'medium' CHECK (response_length IN ('short','medium','long')),
    therapy_style   TEXT,                          -- 慣用療法傾向，非強制
    system_prompt_fragment TEXT NOT NULL,           -- 會被組裝進三明治 Prompt 的頂層片段
    status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','archived')),
    created_by      UUID REFERENCES therapists(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- T-Q12: 自動匹配條件（也可被 Rule Engine 取代/整合，但拆開讓「匹配」邏輯單純）
CREATE TABLE persona_match_conditions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    persona_id      UUID NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    condition_json  JSONB NOT NULL,   -- {"year_of_study": "Year 1", "topics_include": ["社交焦慮"]}
    priority        INT NOT NULL DEFAULT 0
);

-- T-Q15: 治療師手動指派 persona 給特定學生，覆寫自動匹配
CREATE TABLE persona_assignments (
    user_id         UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    persona_id      UUID NOT NULL REFERENCES personas(id),
    assigned_by     UUID NOT NULL REFERENCES therapists(id),
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    note            TEXT
);

-- T-Q13/T-Q14: 同一對話中切換 persona 的紀錄
CREATE TABLE persona_switch_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    from_persona_id UUID REFERENCES personas(id),
    to_persona_id   UUID NOT NULL REFERENCES personas(id),
    trigger_reason  TEXT,             -- "topic_change:家庭關係"
    switched_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- T-Q1–T-Q5: 規則引擎
CREATE TABLE rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    conditions_json JSONB NOT NULL,   -- {"topics_include": ["社交焦慮"], "risk_level": "low", "min_topic_mentions": 3}
    action_json     JSONB NOT NULL,   -- {"therapy": "ACT", "tone": "溫暖接納", "persona_id": "...", "example_ids": [...]}
    priority        INT NOT NULL DEFAULT 0,     -- T-Q4: 數字越大越優先，衝突時取最高
    scope           TEXT NOT NULL DEFAULT 'new_conversations_only'
                    CHECK (scope IN ('new_conversations_only','immediate')),  -- T-Q5
    status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','in_review','active','archived')),
    created_by      UUID NOT NULL REFERENCES therapists(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- T-Q8: 版本歷史 + 一鍵回滾
CREATE TABLE rule_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id         UUID NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    version_number  INT NOT NULL,
    snapshot_json   JSONB NOT NULL,   -- 完整 rule 內容快照
    changed_by      UUID NOT NULL REFERENCES therapists(id),
    change_note     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (rule_id, version_number)
);

-- T-Q6: 沙盒測試紀錄
CREATE TABLE rule_test_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id         UUID REFERENCES rules(id),           -- 可為 NULL：測試尚未存檔的草稿
    draft_config_json JSONB,                              -- 若測試未存檔規則，存草稿內容
    sample_input    TEXT NOT NULL,
    generated_output TEXT NOT NULL,
    tested_by       UUID NOT NULL REFERENCES therapists(id),
    tested_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- T-Q7: 審核工作流（可配置單人/雙人）
CREATE TABLE rule_review_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id         UUID NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    submitted_by    UUID NOT NULL REFERENCES therapists(id),
    reviewer_id     UUID REFERENCES therapists(id),
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    review_note     TEXT,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at     TIMESTAMPTZ
);

-- T-Q9: 治療師對特定對話手動覆寫回應
CREATE TABLE manual_overrides (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      UUID NOT NULL REFERENCES messages(id),
    original_reply  TEXT NOT NULL,
    overridden_reply TEXT NOT NULL,
    overridden_by   UUID NOT NULL REFERENCES therapists(id),
    used_for_training BOOLEAN NOT NULL DEFAULT false,   -- T-Q10 閉環的一部分
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- T-Q10: 回饋標籤
CREATE TABLE feedback_tags (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      UUID NOT NULL REFERENCES messages(id),
    tag             TEXT NOT NULL,     -- "太正式" / "缺乏同理心" / "療法選擇不當"
    tagged_by       UUID NOT NULL REFERENCES therapists(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- T-Q16–T-Q19: 範例庫
CREATE TABLE response_examples (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content             TEXT NOT NULL,
    applicable_conditions_json JSONB NOT NULL DEFAULT '{}',  -- 細粒度條件，同 rules.conditions_json 格式
    usage_mode          TEXT NOT NULL DEFAULT 'style_learning'
                        CHECK (usage_mode IN ('style_learning','direct_quote')),  -- T-Q17
    attribution_mode    TEXT NOT NULL DEFAULT 'anonymous'
                        CHECK (attribution_mode IN ('anonymous','attributed')),   -- T-Q20
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_by          UUID NOT NULL REFERENCES therapists(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- T-Q18: 有效性追蹤
CREATE TABLE example_usage_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    example_id      UUID NOT NULL REFERENCES response_examples(id),
    message_id      UUID NOT NULL REFERENCES messages(id),
    used_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    effectiveness_feedback TEXT  -- 之後可連結 feedback_tags 或使用者反應
);
```

---

## 3. 每個場景如何對應到既有 pipeline

### 3.1 Persona Resolver（對應 T-Q11–T-Q15）
在 Context Assembly Service 中，決定 persona 的優先序：
1. `persona_assignments`（治療師手動指派）— 最高優先
2. `persona_match_conditions` 依 `priority` 排序，比對 `user_profiles` + `profile_topics`
3. 若都不命中 → fallback 到現有的預設「阿本」人格（即目前 `system_prompt.py` 內容，會被搬進一筆 `personas` 資料當 default）

`system_prompt.py` 的 `TOP_LAYER` 從寫死的字串，改為 `persona.system_prompt_fragment`，`build_prompt()` 簽章加一個 `persona` 參數。三明治結構（`<user_input>` 包裝 + `BOTTOM_LAYER` 安全重申）維持不變 — persona 只換頂層人格描述，底層護欄永遠是同一份，這樣才不會因為換 persona 而弱化安全機制。

### 3.2 Rule Engine（對應 T-Q1–T-Q5）
`rules` 表用簡單的條件比對（不需要複雜規則引擎套件，PoC 規模用 Python dict 比對 JSONB 條件即可）：
- 條件比對：`topics_include`（查 `profile_topics`）、`risk_level`（查 `user_profiles.risk_level`）、`min_topic_mentions`、`history_therapy_used`
- 命中多條規則時，取 `priority` 最高者（T-Q4）
- `scope='new_conversations_only'` 時，規則變更只在下一個新 `session` 生效；`immediate` 時對進行中 session 立即生效（T-Q5，用一個簡單的 `active_rules_cache` 版本號機制實現，session 開始時鎖定當時生效的規則版本）

命中的 `action_json` 提供 `persona_id`（覆寫 Persona Resolver 結果）、`example_ids`（餵給 Example Selector）、`therapy` 和 `tone`（組裝進 prompt）。

### 3.3 Human-in-the-loop（對應 T-Q6–T-Q10）
新增 Admin API：
- `POST /api/v1/admin/rules/{id}/test` → 呼叫同一個 `chat_with_llm()`，但用草稿規則 + 治療師輸入的模擬對話，不寫入任何正式 session（T-Q6）
- `POST /api/v1/admin/rules/{id}/submit-review` → 建立 `rule_review_requests`；機構設定決定是否需要 `reviewer_id` 才能把 `rules.status` 轉為 `active`（T-Q7）
- 每次規則變更都寫一筆 `rule_versions`；回滾 = 把某個舊版本的 `snapshot_json` 複製回 `rules` 並產生新版本號（保留歷史，不覆寫）（T-Q8）
- `manual_overrides` 和 `feedback_tags` 是獨立寫入端點，供 Oversight Dashboard 呼叫（T-Q9/T-Q10）

### 3.4 危機分層升級（對應 Q10–Q14）
`Post-processing` 步驟中，`crisis_handler.py` 從「回一句話」升級為呼叫 Escalation Service：
1. 查詢該 `session_id` 內已有幾筆 `risk_events` → 決定這次是 tier 1/2/3
2. tier 1：照現行邏輯回熱線訊息
3. tier 2：額外寫入 `supervisor_notified_at`，觸發通知（email/webhook，之後可接 Slack）
4. tier 3：`escalated_to_human_at` 寫入，回覆內容改為「已為你轉接真人」，該 session 後續訊息在前端顯示為「等待治療師接手」狀態

`risk_events` 表在應用層（不只資料庫層）強制不可刪除、不提供刪除 API，只允許 `resolved_by` / `resolution_note` 補充。

---

## 4. 建議的實作順序

1. **資料庫層**：Postgres schema 建立（above DDL）+ `requirements.txt` 加 `sqlalchemy`, `asyncpg`（或 `psycopg[binary]`），設定 `DATABASE_URL`
2. **把現有 InMemoryConversationStore 換成 Postgres-backed store**（`sessions` + `messages` 表），這一步先讓現有功能在新資料層上跑起來，不改變任何行為 — 是後面所有功能的地基
3. **Persona 系統**：把現有 `system_prompt.py` 的固定內容轉成 `personas` 表的一筆 default 資料，`build_prompt()` 改吃 persona 參數 — 先讓「單一 persona 換皮膚」可運作，再做自動匹配
4. **Profile + 主題演化 + 跨 Session 摘要**（Q1–Q6）：新增 Context Assembly 讀取邏輯 + 一個背景任務（每次 session 結束時用 LLM 生成 `session_summaries`）
5. **範例庫**（T-Q16–T-Q19）：獨立 CRUD + Example Selector，先不接 Rule Engine，用固定條件測試
6. **Rule Engine**（T-Q1–T-Q5）：把 Persona 覆寫 + Example 選取都收進 action，這時候前面兩塊才真正被規則統一調度
7. **Crisis 分層 + Risk Table**（Q10–Q14）：獨立於其他功能，可以和步驟 2 之後的任何時間點並行做
8. **資源觀看紀錄**（Q7–Q9）：獨立小功能，隨時可插入
9. **Admin Console 前端**（T-Q6–T-Q10 的 UI）+ **Oversight Dashboard**：等後端 API 穩定後最後做，避免前端跟著後端 schema 一直改

---

*文件建立時間：2026-08-12*
*延續自：`docs/PHASE5_ARCHITECTURE.md`*
