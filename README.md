# PeaceMind — 心理諮詢 AI 助理 PoC「Boon」

> 一個具備企業級三層防禦架構的心理健康聊天機器人 PoC

---

## 架構概覽

```
用戶輸入
    │
    ▼
┌─────────────────────────┐
│  Layer 1: Input Gateway  │  長度限制 + Prompt Injection 過濾 + 危機偵測
└────────────┬────────────┘
             │ OK
             ▼
┌─────────────────────────┐
│  Layer 2: LLM Core       │  Azure OpenAI GPT-4o + 三明治結構 Prompt
│  (Sandwich Prompting)    │  [頂層護欄] <user_input>...</user_input> [底層重申]
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  Layer 3: Output Gateway │  Regex 掃描藥名 / 診斷術語 / 系統洩漏
└────────────┬────────────┘
             │ SAFE
             ▼
          回覆用戶
```

### 危機介入流程

```
輸入偵測到危機關鍵字 (Layer 1)
    │
    ▼
跳過 LLM，直接觸發 crisis_handler
    │
    ▼
回傳溫暖危機回覆 + 香港緊急求助熱線
```

---

## 快速開始

### 1. 安裝依賴

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 設定環境變數

```bash
cp .env.example .env
# 編輯 .env，填入你的 Azure OpenAI 憑證
```

`.env` 需填寫：

| 變數 | 說明 |
|------|------|
| `AZURE_OPENAI_API_KEY` | Azure Portal 取得的 API Key |
| `AZURE_OPENAI_ENDPOINT` | 如 `https://your-resource.openai.azure.com/` |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | 你的 Deployment 名稱，如 `gpt-4o` |
| `AZURE_OPENAI_API_VERSION` | 預設 `2024-02-01` |

### 2.5 啟動本地 Postgres（Phase 0 — 持久化 Session 記憶）

```bash
docker compose up -d
```

會在本機 `5433` port 起一個 `postgres:16`（避開常見的 5432 佔用）。啟動後在 `.env` 加上：

```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/peacemind
```

接著跑 migration：

```bash
alembic upgrade head
```

會建立 `users` / `sessions` / `messages` 三張表（+ `alembic_version`）。

> 沒有設定 `DATABASE_URL` 時，系統會自動退回 `InMemoryConversationStore`（純記憶體、重啟即消失），適合快速本地開發或跑測試，不強制要求先起資料庫。
>
> 生產環境（Vercel 部署）建議用 Supabase / Neon / Vercel Postgres 等雲端服務，把它們給的連線字串填進 `DATABASE_URL` 即可，架構上不需要改任何程式碼。

### 3. 啟動 API

```bash
uvicorn app.main:app --reload --port 8000
```

API 文件：[http://localhost:8000/docs](http://localhost:8000/docs)

### 4. 測試對話

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "我最近壓力很大，不知道怎麼辦。", "history": []}'
```

---

## 執行單元測試

```bash
pytest tests/ -v
```

Gateway 測試不需要真實 API Key，可離線執行。

---

## API 規格

### `POST /api/v1/chat`

**Request Body**

```json
{
  "message": "用戶輸入文字（最多 1500 字）",
  "history": [
    {"role": "user", "content": "上一輪用戶訊息"},
    {"role": "assistant", "content": "上一輪 AI 回覆"}
  ]
}
```

**Response**

```json
{
  "reply": "AI 回覆文字",
  "intercepted": false,
  "crisis": false
}
```

| 欄位 | 說明 |
|------|------|
| `reply` | AI 回覆（或安全替換回覆） |
| `intercepted` | `true` 表示觸發了 Gateway 攔截 |
| `crisis` | `true` 表示偵測到危機情況，回覆已替換為緊急資源 |

---

## 安全防禦機制

| 層級 | 防禦內容 | 觸發行為 |
|------|---------|---------|
| Layer 1 - Input | 輸入超過 1500 字 | 阻擋，提示分段輸入 |
| Layer 1 - Input | Prompt Injection 黑名單 | 阻擋，回覆友善提示 |
| Layer 1 - Input | 危機關鍵字偵測 | 跳過 LLM，強制危機介入 |
| Layer 2 - LLM | 三明治結構 Prompt | XML 標籤隔離用戶輸入 |
| Layer 3 - Output | 處方藥名 Regex | 攔截，替換安全回覆 |
| Layer 3 - Output | 診斷術語 Regex | 攔截，替換安全回覆 |
| Layer 3 - Output | 系統資訊洩漏 | 攔截，替換安全回覆 |

---

## 專案結構

```
PeaceMind/
├── app/
│   ├── main.py                 # FastAPI 入口
│   ├── routers/
│   │   └── chat.py             # /api/v1/chat 端點（整合三層防禦）
│   ├── gateways/
│   │   ├── input_gateway.py    # Layer 1：輸入檢查
│   │   └── output_gateway.py   # Layer 3：輸出掃描
│   ├── core/
│   │   ├── llm_client.py       # Layer 2：Azure OpenAI 客戶端
│   │   └── crisis_handler.py   # 危機介入回覆
│   └── prompts/
│       └── system_prompt.py    # 三明治結構 System Prompt
├── tests/
│   └── test_gateways.py        # Gateway 單元測試
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 開發進度

- [x] Phase 1：後端 API 架構 + Azure OpenAI 串接
- [x] Phase 2：三層防禦機制 + 危機介入邏輯
- [x] Phase 3：前端 Web 聊天介面
- [x] Phase 4：Red Teaming 壓力測試

---

## 香港緊急求助熱線

| 服務 | 電話 | 時間 |
|------|------|------|
| 生命熱線 | 2382 0000 | 24小時 |
| 撒瑪利亞防止自殺會 | 2389 2222 | 24小時 |
| 醫管局精神健康直通車 | 18111 | 24小時 |
| 緊急求助 | 999 | 24小時 |
