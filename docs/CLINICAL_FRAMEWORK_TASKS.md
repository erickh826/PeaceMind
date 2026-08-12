# 治療師介入框架 — 任務分拆與進度追蹤

**設計依據**：`docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md`
**原則**：每個 Phase 完成後現有功能必須維持可運作（不破壞現有五層防禦 pipeline），且盡量獨立可測試。

---

## Phase 0 — 資料庫地基 ✅ 完成（2026-08-12）
> 目標：把 Postgres 接上，取代 InMemoryConversationStore；不新增任何功能，行為 100% 相容。

- [x] 0.1 `requirements.txt` 加 `sqlalchemy`, `asyncpg`, `alembic`（migration 工具）
- [x] 0.2 建立 `app/db/` 模組：engine, session factory, base model
- [x] 0.3 建立 Alembic migration：`users`, `sessions`, `messages` 三張表（`migrations/versions/b6dcae4f4555_*.py`）
- [x] 0.4 實作 `PostgresConversationStore`（實作既有 `ConversationStore` Protocol），取代 `InMemoryConversationStore`
- [x] 0.5 `chat.py` 切換 store 實作，跑現有 `tests/test_chat_memory.py` 確認不 regress（169 passed）
- [x] 0.6 `.env.example` 加 `DATABASE_URL`

**完成判準**：現有 chat API 行為不變，但重啟服務後 session 記憶仍在。 ✅ 達成（未跑 DATABASE_URL 時退回 InMemory，行為與之前完全相同；設定 DATABASE_URL 後改用 Postgres 持久化）

**實作筆記（與原設計的差異）**：
- `ConversationStore` Protocol 從同步改為 **async** 方法（`get_history`/`append`/`reset` 皆為 `async def`），`InMemoryConversationStore` 與 `chat.py` 呼叫端同步更新為 `await`。原因：`PostgresConversationStore` 用 async SQLAlchemy（asyncpg），避免在 async route handler 裡阻塞事件迴圈。
- `sessions` 表新增 `client_key TEXT UNIQUE` 欄位，與內部 UUID 主鍵分開。原因：前端與既有測試用的 `session_id` 是任意字串（`crypto.randomUUID()` 或測試固定字串），不保證是合法 UUID 格式，不能直接當 Postgres UUID 主鍵。
- 每個新的 `client_key` 會自動建立一個匿名 `User`（`external_ref = client_key`）。這是暫時措施，Phase 2 導入真實 Profile／帳號系統後需要重新設計使用者識別方式（目前是「一個 session_id 一個匿名 user」，之後應該是「一個真實使用者可以有多個 session」）。
- **尚未在真實 Postgres 上跑過 migration**（sandbox 環境沒有可用的 Postgres/Docker）。上線前必須：①找一個 Postgres（本地 docker 或 Supabase/Neon）②跑 `alembic upgrade head` ③實際打一次 `/api/v1/chat` 驗證讀寫。這步驟我還沒做，記在待辦。
- README 的「本地起 Postgres 方式」說明尚未補上（原 0.6 的一部分），待這輪 Postgres 實測後一併寫。
- 發現一個既有的、與本次改動無關的問題：`tests/test_phase5.py` 用了 `@pytest.mark.asyncio`，但 `requirements.txt` 從未包含 `pytest-asyncio`，所以這 5 個測試一直是失敗的（不是我這次造成的 regression，但值得之後找時間修）。

### ✅ 真實 Postgres 實測結果（使用者於 Docker postgres:16, port 5433 執行，2026-08-12）

| 測試項 | 結果 |
|--------|------|
| `alembic upgrade head` 成功建立 4 張表（users/sessions/messages/alembic_version） | ✅ PASS |
| append + get_history（正確讀寫） | ✅ PASS |
| max_messages=5 截斷（寫入 6 條，讀取 5 條，最舊的被截斷） | ✅ PASS |
| 直查 Postgres（users=1, sessions=1, messages=6） | ✅ PASS |
| 無效 role / 空白 content 被正確拒絕 | ✅ PASS |
| reset → cascade delete（messages 歸零） | ✅ PASS |

Phase 0 **正式結案**。新增 `docker-compose.yml`（本地 Postgres，port 5433 避開常見的 5432 佔用）+ README 補上完整本地啟動步驟。

---

## Phase 1 — Persona 系統（T-Q11–T-Q15 基礎）
> 目標：`system_prompt.py` 從寫死字串改為讀 `personas` 表；先只做「單一 default persona」跑通，暫不做自動匹配。

- [ ] 1.1 Migration：`personas`, `persona_match_conditions`, `persona_assignments`, `persona_switch_log`
- [ ] 1.2 把現有「阿本」人格寫入一筆 `personas`（status=active, 作為 default）
- [ ] 1.3 `build_prompt()` 簽章改為吃 `persona: Persona` 參數，`TOP_LAYER` 改為 `persona.system_prompt_fragment`；`BOTTOM_LAYER` 安全重申維持寫死，不受 persona 影響
- [ ] 1.4 `chat.py` 加入 Persona Resolver（此階段先固定回傳 default persona）
- [ ] 1.5 Persona 自動匹配邏輯：讀 `user_profiles` + `profile_topics`（此時這兩表還沒建，先 stub／延到 Phase 2 補上真實資料後再接）
- [ ] 1.6 治療師手動指派 API：`POST /api/v1/admin/personas/{id}/assign`
- [ ] 1.7 Persona 切換記錄寫入 `persona_switch_log`

**完成判準**：可以在資料庫新增第二個 persona，指派給某個 user，該 user 下次對話行為改變；預設使用者不受影響。

---

## Phase 2 — Profile / 主題演化 / 跨 Session 摘要（Q1–Q6）
> 目標：Context Assembly Service 正式成形，Persona 自動匹配補完。

- [ ] 2.1 Migration：`user_profiles`, `profile_change_log`, `profile_topics`, `session_summaries`
- [ ] 2.2 `Context Assembly Service`：對話開始時載入 profile + 相關摘要，組進 system prompt
- [ ] 2.3 Profile 動態更新偵測：LLM 判斷使用者是否在更正先前資訊 → 寫 `profile_change_log` + 更新 `user_profiles`（Q2/Q15）
- [ ] 2.4 主題標籤自動演化：關鍵字/語意分類 → `profile_topics` 計數，達門檻新增主題（Q3）
- [ ] 2.5 Session 結束背景任務：用 LLM 生成結構化摘要寫入 `session_summaries`（Q4/Q5）
- [ ] 2.6 跨 Session 記憶檢索：使用者提及「上次」時，撈取最相關 `session_summaries` 注入 context（Q4）
- [ ] 2.7 刪除/遺忘 API：`POST /api/v1/profile/forget`，軟刪除 `session_summaries`（Q6，PDPO）
- [ ] 2.8 補完 Phase 1.5 的 Persona 自動匹配（現在有真實 profile/topics 可用）
- [ ] 2.9 推薦策略依最新 Profile 動態調整（Q17）— 接到現有的 `REC_RULES`（前端）或搬到後端統一決策

**完成判準**：模擬「上次講嗰個朋友」的對話，system 能撈到正確摘要並回應連貫。

---

## Phase 3 — 範例庫（T-Q16–T-Q19）
> 目標：獨立 CRUD + Selector，先用固定條件測試，不接 Rule Engine。

- [ ] 3.1 Migration：`response_examples`, `example_usage_log`
- [ ] 3.2 CRUD API：`POST/GET/PATCH /api/v1/admin/examples`
- [ ] 3.3 Example Selector：依 `applicable_conditions_json` 比對目前 context，回傳命中範例
- [ ] 3.4 `style_learning` vs `direct_quote` 兩種注入方式接進 prompt 組裝（T-Q17）
- [ ] 3.5 使用記錄寫入 `example_usage_log`（T-Q18）
- [ ] 3.6 引用模式（anonymous/attributed）接進最終回覆組裝（T-Q20）

**完成判準**：治療師新增一則範例＋條件，符合條件的對話回覆風格明顯貼近範例。

---

## Phase 4 — Rule Engine（T-Q1–T-Q5）
> 目標：規則統一調度 Persona + Example + 療法/語氣設定。

- [ ] 4.1 Migration：`rules`, `rule_versions`
- [ ] 4.2 條件比對引擎（Python，比對 `conditions_json` vs profile/topics/risk/history）
- [ ] 4.3 CRUD API + 每次變更寫 `rule_versions`（T-Q8 先做基礎版本，Phase 5 補審核流程）
- [ ] 4.4 優先級排序、多規則衝突取最高優先（T-Q4）
- [ ] 4.5 `scope` 生效範圍邏輯：new_conversations_only vs immediate（T-Q5）
- [ ] 4.6 Rule action 接進 Context Assembly：覆寫 persona / 帶入 example_ids / 設定 therapy+tone

**完成判準**：新增一條規則（如原文件範例：社交焦慮+low risk+第3次），命中時可觀察到 ACT 語氣、溫暖接納風格的回覆。

---

## Phase 5 — Human-in-the-loop（T-Q6–T-Q10）
> 目標：沙盒測試、審核工作流、手動覆寫、回饋標籤。

- [ ] 5.1 沙盒測試 API：`POST /api/v1/admin/rules/{id}/test`（不寫入正式 session）
- [ ] 5.2 Migration：`rule_test_runs`, `rule_review_requests`, `manual_overrides`, `feedback_tags`
- [ ] 5.3 審核工作流 API：提交審核、核准/退回，機構層級開關（單人直發 vs 雙人審核）
- [ ] 5.4 一鍵回滾：從 `rule_versions` 還原
- [ ] 5.5 手動覆寫 API + 標記 `used_for_training`
- [ ] 5.6 回饋標籤 API

**完成判準**：治療師能在沙盒驗證規則效果，走完「提交審核→核准→上線」流程，並能回滾到前一版本。

---

## Phase 6 — 危機分層 + Risk Table（Q10–Q14）
> 目標：可與 Phase 2 之後任意階段並行做。

- [ ] 6.1 Migration：`risk_events`（應用層強制不可刪除，不寫刪除 API）
- [ ] 6.2 Escalation Service：查詢同 session 觸發次數 → 決定 tier 1/2/3
- [ ] 6.3 Tier 2：supervisor 通知（先用 log/webhook stub，之後可接 email/Slack）
- [ ] 6.4 Tier 3：強制轉真人，前端顯示「等待治療師接手」狀態
- [ ] 6.5 「跟進對話」偵測：使用者提及先前危機時，查 `risk_events` 歷史，回應更謹慎連貫（Q11）
- [ ] 6.6 Oversight Dashboard 用的查詢 API：依 user 拉出完整 risk 時間軸（Q14 資料面）

**完成判準**：模擬同一 session 內連續三次觸發危機關鍵字，能觀察到三種不同層級的系統行為。

---

## Phase 7 — 資源觀看紀錄（Q7–Q9）
> 目標：獨立小功能，隨時可插入。

- [ ] 7.1 Migration：`resources`, `resource_views`
- [ ] 7.2 觀看紀錄寫入 API（前端播放影片時呼叫）
- [ ] 7.3 推薦邏輯排除已看過的資源，或推進階版（Q8）
- [ ] 7.4 回饋（有用/沒用）影響後續推薦（Q9）

**完成判準**：同一使用者第二次被推薦時，不會重複收到已標記「沒用」的資源。

---

## Phase 8 — Admin Console 前端 + Oversight Dashboard
> 目標：等 Phase 3–6 的 API 穩定後最後做。

- [ ] 8.1 治療師登入/權限（`therapists` 表 + auth）
- [ ] 8.2 規則 builder UI（條件式表單）
- [ ] 8.3 Persona 管理 UI
- [ ] 8.4 範例庫管理 UI
- [ ] 8.5 沙盒測試 UI
- [ ] 8.6 審核工作流 UI
- [ ] 8.7 Oversight Dashboard：risk 時間軸、persona 有效性分析、範例效果分析

---

## 執行原則

1. **每個 Phase 內按 checkbox 順序做**，做完一項才進下一項。
2. **每個 Phase 完成後跑一次現有測試**（`pytest tests/ -v`），確保沒有破壞既有五層防禦。
3. **Migration 一律用 Alembic**，不手動改 schema，保留可回溯的版本歷史（呼應 T-Q8 的精神，連工程流程本身都要可回滾）。
4. **Git 工作流程**：
   - 每個 Phase 從 `main` 開一個新 branch（例：`upgrade/phase0`, `upgrade/phase1`）
   - Phase 完成、測試通過後，merge 回 `main`
   - **merge 後先確認 Vercel deployment 沒問題，才從 `main` 開下一個 Phase 的新 branch**
   - 這樣任何時候 `main` 都是「已知可部署」的狀態，不會有半成品疊半成品的風險
5. **目前狀態**：Phase 0 已完成並在真實 Postgres 驗證通過，`upgrade/phase0` 已推上 GitHub（commit `b4ba045`）。準備 merge 進 `main`，確認 deployment 後開始 Phase 1。

---

*文件建立時間：2026-08-12*
*對應架構文件：`docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md`*
