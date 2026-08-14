# CLAUDE.md — PeaceMind 專案脈絡

給接手這個 repo 的 Claude（Claude Code 或 claude.ai）看的背景說明。這份文件是「記憶」，不是任務清單——實際任務進度在 `docs/CLINICAL_FRAMEWORK_TASKS.md`，架構設計在 `docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md`，兩份都要看。

## 專案是什麼

**PeaceMind**（原本代號「阿本 / Boon」）是樹仁大學輔導中心的心理健康支持 PoC chatbot。原本是一個安全過濾做得不錯、但功能單薄的 chatbot（三層防禦 + 危機關鍵字偵測 + 單一寫死人格），目前正在依照使用者提供的完整臨床規格（學生旅程 Q1–Q17 + 治療師介入框架 T-Q1–T-Q20）擴充成一套完整系統。

- 部署：Vercel（https://peace-mind-sjiv.vercel.app/），Python FastAPI 後端（`api/index.py` → `app.main.app`），`frontend/` 是 Node/Vite 的靜態前端 + 一個純 dev proxy（不是正式後端）
- 資料庫：Supabase Postgres（見下方「資料庫連線層」的血淚教訓）

## 必看文件（依重要性排序）

1. **`docs/CLINICAL_FRAMEWORK_ARCHITECTURE.md`** — 完整資料模型（Postgres DDL）+ 整體模組架構圖。任何新功能開發前先看這份，確認資料表設計跟現有規劃一致，不要憑空另起一套。
2. **`docs/CLINICAL_FRAMEWORK_TASKS.md`** — 9 個 Phase 的任務分拆、目前進度、每個 Phase 完成後的「實作筆記」（記錄跟原設計的差異、已知限制、待辦事項）。**開始做任何 Phase 前先讀完前面 Phase 的實作筆記**，很多暫時性設計決策（例如身份綁定方式、auth 缺口）會影響後面怎麼接。
3. 這份 `CLAUDE.md` — 給你的口頭交接，講規則跟已經踩過的坑。

## 鐵律：Git 工作流程

**每個 Phase 從 `main` 開一個新 branch（`upgrade/phaseN`）→ 完成後 merge 回 `main` → 確認 Vercel deployment 沒問題 → 才從 `main` 開下一個 Phase 的新 branch。**

這樣任何時候 `main` 都是「已知可部署」的狀態。獨立的 bug fix（不屬於任何 Phase 的邏輯，例如資料庫連線層問題）用 `hotfix/xxx` branch，同樣的流程：完成 → merge `main` → 確認部署 → 才繼續。

**例外**：使用者有時候會想直接在 `main` 上快速試東西（尤其是除錯連線問題這種需要反覆試錯的事），這是可以接受的——但如果你剛好也在對應的 hotfix branch 上做同一件事，記得 merge 前先 diff 兩邊，不要盲目 `--ours`/`--theirs`，之前發生過使用者直接改的版本漏掉一個關鍵設定（見下方 psycopg 那段）。

## 資料庫連線層：踩過的坑（不要重踩）

Phase 0 的 `InMemoryConversationStore` → `PostgresConversationStore` 上線後，切到 Supabase 時連續踩了好幾個坑，完整記錄在 `docs/CLINICAL_FRAMEWORK_TASKS.md` 的「上線後的 Hotfix 記錄」表格。**最重要的結論**：

- **DB driver 用 `psycopg`（v3），不是 `asyncpg`。** `asyncpg` + `uvloop` 在 Vercel 的 Lambda 類沙盒環境上會在 SSL 連線建立階段噴 `OSError: Device or resource busy`，這是已知的環境相容性問題，不是連線字串設錯，也不是程式碼邏輯錯。已經改掉了，**不要改回 `asyncpg`**。
- Engine 用 `poolclass=NullPool`（serverless 環境不該自己維護連線池，反正 Supabase Transaction Pooler 已經在做這件事了）。
- `connect_args={"prepare_threshold": None}`（psycopg 對應 asyncpg 的 `statement_cache_size=0`，關掉 server-side prepared statement，PgBouncer transaction-mode 需要這個）。
- `_normalize_database_url()` 會過濾掉 Supabase 連線字串常帶的 `?pgbouncer=true` 參數（psycopg 不認得這個 Prisma 風格的參數）。
- **正式環境（Vercel）用 Supabase 的 Transaction Pooler（port 6543）**；**本機跑 `alembic upgrade head` 用 Session Pooler（port 5432）**——兩個 pooler 用途不同，不要搞混。
- 密碼含特殊字元（如 `@`）記得做 URL encoding，不然連線字串解析會壞掉且錯誤訊息會很難懂（host 名稱會混進密碼片段）。

這些全部都在 `app/db/__init__.py`，改這個檔案前一定要先看檔案開頭的註解，裡面每個決定都寫了原因。

## 目前進度速覽

- ✅ **Phase 0**（資料庫地基）：完成，已上線驗證
- ✅ **Phase 1**（Persona 系統）：程式碼完成，本地測試通過，`upgrade/phase1` branch 已同步最新的 hotfix，**還沒做真實 Postgres 上的手動指派流程實測**，也還沒 merge 回 `main`
- ⬜ Phase 2–8：規劃在 `docs/CLINICAL_FRAMEWORK_TASKS.md`，還沒開始

詳細狀態、每個 checkbox 的完成情況，去看 `docs/CLINICAL_FRAMEWORK_TASKS.md` 最新版本，這份 `CLAUDE.md` 不會逐項同步更新（避免兩份文件互相打架），有衝突以 `CLINICAL_FRAMEWORK_TASKS.md` 為準。

## 其他約定

- 每個 Phase/hotfix 完成後跑 `pytest tests/ -v` 確認沒有 regression。目前有 5 個 `test_phase5.py` 的測試因為缺 `pytest-asyncio` 套件一直是失敗的——這是既有問題，不是你造成的，看到這 5 個失敗不用緊張，除非你剛好有空想順手修掉它。
- Migration 一律用 Alembic，不要手動改 schema。
- 三明治 Prompt 結構（`app/prompts/system_prompt.py`）的 `SAFETY_CORE`（絕對禁止事項 + 危機熱線）永遠固定，不管做什麼 persona/rule engine 功能，都不能讓這段被 persona 內容覆寫或稀釋掉。
