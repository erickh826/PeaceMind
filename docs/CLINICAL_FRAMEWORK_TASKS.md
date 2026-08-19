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

## Phase 1 — Persona 系統（T-Q11–T-Q15 基礎）✅ 完成（2026-08-12）
> 目標：`system_prompt.py` 從寫死字串改為讀 `personas` 表；先只做「單一 default persona」跑通，暫不做自動匹配。

- [x] 1.1 Migration：`personas`, `persona_match_conditions`, `persona_assignments`, `persona_switch_log`（`migrations/versions/102d7d73bbd7_*.py`），並補上 Phase 0 留空的 `sessions.persona_id` / `messages.persona_id` FK
- [x] 1.2 把現有「Boon」人格寫入一筆 `personas`（status=active, is_default=true，固定 UUID `00000000-0000-0000-0000-000000000001`）
- [x] 1.3 `build_prompt()` 改為吃 `persona_name` + `persona_fragment` 參數；`SAFETY_CORE`（絕對禁止事項 + 熱線）拆成獨立常數，永遠固定、不受 persona 影響
- [x] 1.4 `chat.py` 加入 Persona Resolver 呼叫（`resolve_persona()`）
- [ ] 1.5 Persona 自動匹配邏輯 —— **延到 Phase 2**（需要 `user_profiles` / `profile_topics`，Phase 1 還沒有這些表）
- [x] 1.6 治療師手動指派 API：`POST /api/v1/admin/personas/assign`（+ `GET/POST /personas`, `PATCH /personas/{id}/activate`）
- [x] 1.7 Persona 切換記錄寫入 `persona_switch_log`（`record_persona_usage()`，比對 session 目前 persona 與新解析結果）

**完成判準**：可以在資料庫新增第二個 persona，指派給某個 user，該 user 下次對話行為改變；預設使用者不受影響。 ✅ 邏輯已實作並通過現有測試（169 passed，含 red-team 洩露偵測），**尚未在真實 Postgres 上實測手動指派流程**——下一步需要你在部署環境跑一次：建立第二個 persona → activate → assign 給某個 session_id → 確認下一次對話真的換了語氣。

**實作筆記（與原設計的差異）**：
- `ConversationStore.append()` 新增可選的 `persona_id` 參數，讓 `messages` 表能記錄每則訊息當時用的 persona（原設計 `messages.persona_id` 早就有欄位，但 Phase 0 沒有寫入邏輯，這次補上）。`InMemoryConversationStore` 收到這個參數會靜默忽略（沒有結構化欄位可放）。
- 手動指派（T-Q15）目前透過 `users.external_ref` 對應 `session_id` 查找使用者，這是沿用 Phase 0 的暫時身份綁定方式（一個 session_id = 一個匿名 user）。**這只在該 session_id 已經至少對話過一次、User 記錄已建立後才查得到** —— Phase 2 導入真實 Profile/帳號系統後，這裡的身份識別邏輯需要重新設計。
- Admin API（`admin_personas.py`）**目前沒有真正的治療師登入驗證**——`created_by` / `assigned_by` 只是 request body 裡的欄位，呼叫端自己填，不會驗證權限。這是刻意的暫時妥協（治療師登入機制排在 Phase 8 跟 Admin Console 前端一起做），上線前這些端點必須加上真實 auth，目前僅供後端邏輯驗證/測試用。
- Persona 切換記錄（1.7）只有在 session 已存在（至少 append 過一次）時才會寫入；全新 session 的第一則訊息不算「切換」。

---

## Phase 2 — Profile / 主題演化 / 跨 Session 摘要（Q1–Q6）✅ 程式碼完成（2026-08-19）
> 目標：Context Assembly Service 正式成形，Persona 自動匹配補完。
> 設計依據：`docs/Phase2_implement_plan_Antigravity.md`（實作前已修正兩個問題，見該文件 §2）。

- [x] 2.1 Migration：`user_profiles`, `profile_change_log`, `profile_topics`, `session_summaries`（`migrations/versions/1ade07baedf2_*.py`）
- [x] 2.2 `Context Assembly Service`（`app/core/context_assembler.py`）：對話開始時載入 profile + 相關摘要，組進 system prompt
- [x] 2.3 Profile 動態更新偵測：LLM 判斷使用者是否在更正先前資訊 → 寫 `profile_change_log` + 更新 `user_profiles`（Q2/Q15）
- [x] 2.4 主題標籤自動演化：LLM 語意分類（`app/core/clinical_topics.py` 共用標準主題清單）→ `profile_topics` 計數，達門檻（>=3）新增主題（Q3）
- [x] 2.5 Session 結束時生成摘要：`app/core/session_summarizer.py`，用 LLM 生成結構化摘要寫入 `session_summaries`（Q4/Q5）——**同步執行，不是背景任務**，見下方實作筆記
- [x] 2.6 跨 Session 記憶檢索：使用者提及「上次」時，撈取最相關 `session_summaries` 注入 context（Q4）
- [x] 2.7 刪除/遺忘 API：`POST /api/v1/profile/forget`，軟刪除 `session_summaries` + 清空 `user_profiles`/`profile_topics`（Q6，PDPO）
- [x] 2.8 補完 Phase 1.5 的 Persona 自動匹配（`persona_match_conditions`，`app/core/persona_resolver.py`）
- [ ] 2.9 推薦策略依最新 Profile 動態調整（Q17）— **未實作**，Antigravity 原始計畫文件對這項只有目標敘述、沒有設計細節，實作時判斷屬於獨立範圍，先跳過，之後需要另外設計（前端 `REC_RULES` 怎麼接後端 profile）

**完成判準**：模擬「上次講嗰個朋友」的對話，system 能撈到正確摘要並回應連貫。 ✅ 邏輯已實作並通過 `tests/test_phase2_profiles.py`（本機 Docker Postgres 驗證，5 項全過），**尚未在 Supabase 正式環境跑過 migration / E2E 驗證**，也還沒 merge 回 `main`。

**實作筆記（與 Antigravity 原始計畫的差異，落地前已修正）**：
- **`session_summaries.session_id` 改成 `ON DELETE SET NULL`**（原設計是 `CASCADE`）。原設計會讓 `/api/v1/reset` 在寫入摘要後緊接著把 `sessions` 那筆刪掉，`CASCADE` 會讓剛寫入的摘要在同一輪請求裡被連坐刪除——Q4–Q6 的跨 session 記憶會永遠留不住任何東西。這個修正已同步進 `docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md` 的 DDL，兩份文件目前一致。
- **Profile 抽取 + 主題分類不用 `BackgroundTasks`，改成同步、合併成一次 LLM 呼叫**。原設計提議用 FastAPI `BackgroundTasks` 在回應送出後才跑，理由是「延遲優化」；但這個專案部署在 Vercel（`api/index.py` 是純 ASGI passthrough，沒有 `waitUntil` 機制），回應送出後執行環境幾乎立刻會被凍結回收——`BackgroundTasks` 排程的工作不保證真的執行完，本機 `uvicorn` 測試會看起來正常，Vercel 上可能悄悄失效。改成 `process_post_chat_updates()` 在 `/chat` 回應送出前直接 `await`，profile 抽取跟主題分類合併成一次 LLM 呼叫（不是原設計的兩次），把多加的延遲代價降到最低。**接受的取捨**：每輪 `/chat` 多一次 LLM 呼叫的延遲。
- **`/api/v1/reset` 和 `POST /api/v1/chat/end` 共用同一個冪等函式** `end_session_and_summarize()`，用 `sessions.ended_at` 當旗標——已經結束過的 session 直接 no-op，避免同一個 session 被呼叫兩次而生成兩筆 `session_summaries`（多花一次 LLM 成本）。
- **`app/core/context_assembler.py` 不會預先建立空的 `UserProfile` 記錄**（原設計會）。首次對話時交給 `profile_service.py` 在對話結束後依實際抽取結果建立，避免每個新使用者的第一句話就多一次寫入。
- **已知取捨、未在本階段修正**：`assemble_context()` 對 `users` 表做了一次獨立查詢，跟 `resolve_persona()` 各自查一次，沒有共用同一次查詢結果——正確性無虞，只是多一次 DB round trip。
- **測試踩過的坑**：`tests/test_phase2_profiles.py` 一開始沿用了 `test_phase1_e2e.py` 的 `load_dotenv()` 寫法，結果讓 `.env` 裡的 Supabase `DATABASE_URL` 悄悄帶進沒有明確設定 DB 的預設 `pytest tests/` 執行，導致測試一度真的打到 Supabase 正式環境（已確認沒有留下殘留資料，但拿掉了 `load_dotenv()`，改成完全依賴呼叫端自己 `export DATABASE_URL`，跟其他測試檔案的慣例一致）。另外發現 Phase 2 的新程式碼讓既有的 `tests/test_chat_memory.py`（沒有 mock 新的 Phase 2 hook）在有設定 `DATABASE_URL` 時會意外打真的 Azure OpenAI、寫入真的 profile 資料——已修正該檔案補上對應的 mock。

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
5. **目前狀態**：Phase 0 已完成、merge 進 `main`、**Vercel 部署成功確認**，且已經歷一輪資料庫連線層的 hotfix（詳見下方記錄），目前正式環境穩定運作於 Supabase + psycopg + Transaction Pooler。Phase 1（Persona 系統）已在 `upgrade/phase1` branch 完成程式碼與本地測試，接下來要把這輪 hotfix 帶進該 branch，繼續驗證 persona 指派流程。

### ⚠️ 上線後的 Hotfix 記錄（Phase 0 資料庫連線層，2026-08-12）

Phase 0 merge 進 `main` 並確認部署成功後，切換 Vercel `DATABASE_URL` 為雲端 Postgres（Supabase）時，接連遇到幾個獨立問題，記錄下來避免以後重踩：

| # | 問題 | 根因 | 解法 |
|---|------|------|------|
| 1 | 本機 Docker 的 `DATABASE_URL` 誤設到 Vercel | Vercel 伺服器連不到 `localhost`（那是使用者自己電腦） | 換成雲端 Postgres（Supabase） |
| 2 | Supabase 直連（`db.xxx.supabase.co:5432`）只回傳 IPv6，本機網路不支援 | Supabase 近期直連位址變成 IPv6-only | 改用 Supabase Pooler（IPv4） |
| 3 | `?pgbouncer=true` 參數 + prepared statement 快取，導致 Transaction Pooler（6543）連線失敗 | Prisma 風格參數 driver 不認得；transaction-mode pooler 不支援 server-side prepared statement 快取 | 程式碼過濾掉該參數 + 關閉 prepared statement 快取 |
| 4 | `OSError: Device or resource busy`（Vercel 上，SSL 連線建立階段） | `asyncpg` + `uvloop` 在 AWS Lambda 類沙盒環境的已知相容性問題 | **換 driver：`asyncpg` → `psycopg`（v3）** |
| 5 | `failed to resolve host 'xxx@aws-0-...'` | 資料庫密碼含特殊字元（如 `@`），未做 URL encoding，破壞連線字串解析 | 密碼做 URL encode，或重設成純英數字密碼 |
| 6 | Serverless process 「暖機」重用導致連線池 socket 狀態壞掉 | 模組層級全域連線池跨 invocation 重用 | 改用 `NullPool`，每次全新連線 |

**最終確認可用的正式環境設定**：
- Driver：`postgresql+psycopg://`（不是 `+asyncpg`）
- 連線對象：Supabase **Transaction Pooler**（port 6543）
- 密碼：URL encoded
- `app/db/__init__.py`：`poolclass=NullPool` + `connect_args={"prepare_threshold": None}` + 過濾 `pgbouncer` 查詢參數

**本機執行 migration 用 Supabase Session Pooler（port 5432）**，跟正式環境的 Transaction Pooler（6543）分開，見 `.env.example` 說明。

✅ **2026-08-12 確認：Vercel 部署成功，`/api/v1/chat` POST 請求正常運作，讀寫 Supabase 無誤。**

---

*文件建立時間：2026-08-12*
*對應架構文件：`docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md`*
