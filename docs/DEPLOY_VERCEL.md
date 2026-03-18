# Vercel 部署指南 — PeaceMind

本專案採用**前後端分離部署**，在 Vercel 建立兩個獨立 Project。

---

## 架構圖

```
[用戶瀏覽器]
      │
      ▼
[Vercel — peacemind-app]     React 前端 (Static)
      │  VITE_API_URL
      ▼
[Vercel — peacemind-api]     FastAPI 後端 (Python Serverless)
      │  Session Memory（PoC: in-memory, per instance）
      │
      ▼
[Azure OpenAI]               GPT-4o
```

---

## Step 1：部署後端 (peacemind-api)

### 1.1 在 Vercel 建立新 Project

1. 前往 [vercel.com/new](https://vercel.com/new)
2. Import `erickh826/PeaceMind` repo
3. **Root Directory** 設為 `.`（repo 根目錄，因為 `vercel.json` 在根目錄）
4. Framework Preset 選 **Other**
5. 點 **Deploy**

### 1.2 設定環境變數

進入 Project Settings → **Environment Variables**，新增以下 4 個變數：

| 變數名稱 | 值 |
|---------|---|
| `AZURE_OPENAI_API_KEY` | 你的 Azure OpenAI API Key |
| `AZURE_OPENAI_ENDPOINT` | `https://your-resource.openai.azure.com/` |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | 你的 Deployment 名稱（如 `gpt-4o`）|
| `AZURE_OPENAI_API_VERSION` | `2024-02-01` |

設定完成後，回到 **Deployments** 重新部署（Redeploy）。

### 1.3 記下後端 URL

部署完成後，記下你的後端網址，例如：
```
https://peacemind-api.vercel.app
```

### 1.4 測試後端

```bash
curl https://peacemind-api.vercel.app/health
# 應回傳：{"status":"ok","service":"PeaceMind","version":"0.1.0"}

curl -X POST https://peacemind-api.vercel.app/api/v1/reset \
      -H "Content-Type: application/json" \
      -d '{"session_id":"deploy-check-session"}'
# 應回傳：{"status":"cleared"}
```

---

## Step 2：部署前端 (peacemind-app)

### 2.1 在 Vercel 建立第二個 Project

1. 再次前往 [vercel.com/new](https://vercel.com/new)，Import 同一個 repo
2. **Root Directory** 設為 `frontend`
3. **Framework Preset** 選 **Vite**
4. **Build Command**: `npm run build`
5. **Output Directory**: `dist/public`

### 2.2 設定環境變數

| 變數名稱 | 值 |
|---------|---|
| `VITE_API_URL` | `https://peacemind-api.vercel.app`（你的後端 URL）|

### 2.3 部署

點 **Deploy**，完成後即可訪問前端網址。

---

## CORS 設定（重要）

後端需要允許前端網域。部署前端後，記下前端 URL（如 `https://peacemind-app.vercel.app`），然後在後端 `app/main.py` 更新 `allow_origins`：

```python
# app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://peacemind-app.vercel.app",  # 你的前端 URL
        "http://localhost:5000",             # 本地開發
    ],
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)
```

更新後 commit & push，Vercel 會自動重新部署後端。

---

## 本地開發（不變）

```bash
# Terminal 1 — 後端
cd PeaceMind
cp .env.example .env   # 填入 Azure OpenAI 憑證
uvicorn app.main:app --reload --port 8000

# Terminal 2 — 前端
cd PeaceMind/frontend
npm run dev            # :5000 → proxy → :8000
```

---

## 注意事項

| 項目 | 說明 |
|------|------|
| **Cold Start** | Vercel Serverless 首次請求約 2–4 秒，後續請求正常 |
| **Timeout** | Vercel Hobby tier 函式 timeout 為 10 秒，足夠 LLM 呼叫 |
| **免費額度** | Hobby tier 每月 100GB bandwidth + 100K function invocations |
| **Secrets** | `.env` 不要 commit，環境變數只在 Vercel Dashboard 設定 |
| **Session Memory（PoC）** | 目前為 in-memory；在多實例或服務重啟時可能遺失，正式環境建議改用 Redis/DB |
| **Reset API** | `POST /api/v1/reset` 會清空指定 `session_id` 的後端會話記憶 |
