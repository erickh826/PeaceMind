# Vercel 部署指南 — PeaceMind

本專案採用**前後端分離部署**，在 Vercel 建立兩個獨立 Project。
trigger
---

## 已部署 URL

| 端 | URL |
|----|-----|
| **後端** | `https://peace-mind.vercel.app` |
| **前端** | `https://peace-mind-sjiv.vercel.app` |

---

## 架構圖

```
[用戶瀏覽器]
      │
      ▼
[Vercel — peace-mind-sjiv]   React 前端 (Vite Static)
      │  VITE_API_URL
      ▼
[Vercel — peace-mind]        FastAPI 後端 (Python Serverless)
      │
      ▼
[Azure OpenAI]               GPT-4o
[Azure AI Content Safety]    Prompt Shields（Phase 5a）
```

---

## ⚠️ Vercel Hobby Tier — 重要限制

本專案使用 **Vercel Hobby tier（個人免費方案）**，存在以下限制：

### Commit Author 問題
Vercel Hobby 只允許單一 owner 的 commit 觸發自動部署。  
**自動化工具（如 Perplexity Computer）的 commit 會被 Vercel 標記為 "Blocked"。**

**解決方法（每次 push 後在本地執行）**：
```bash
git pull
git commit --amend --reset-author --no-edit
git push --force-with-lease
```
這樣把最新 commit 的 author 改成你自己，Vercel 就會正常 deploy。

### Timeout 限制
- Serverless function timeout：**10 秒**（Hobby tier）
- Azure OpenAI 通常在 3–8 秒內回應，一般足夠
- 如遇 timeout 問題，考慮升級 Pro tier

---

## Step 1：部署後端 (peace-mind)

### 1.1 在 Vercel 建立 Project

1. 前往 [vercel.com/new](https://vercel.com/new)
2. Import `erickh826/PeaceMind` repo
3. **Root Directory** 設為 `.`（repo 根目錄，`vercel.json` 在根目錄）
4. Framework Preset 選 **Other**
5. 點 **Deploy**

### 1.2 設定環境變數

進入 Project Settings → **Environment Variables**，新增以下變數：

| 變數名稱 | 說明 |
|---------|------|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API Key |
| `AZURE_OPENAI_ENDPOINT` | `https://your-resource.openai.azure.com/` |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Deployment 名稱（如 `gpt-4o`） |
| `AZURE_OPENAI_API_VERSION` | `2024-02-01` |
| `AZURE_CONTENT_SAFETY_ENDPOINT` | Azure AI Content Safety endpoint（Phase 5a）|
| `AZURE_CONTENT_SAFETY_KEY` | Azure AI Content Safety key（Phase 5a）|
| `SEMANTIC_GATEWAY_FAIL_OPEN` | `true`（API 失敗時 fail-open，不阻擋用戶）|

> **注意**：`AZURE_CONTENT_SAFETY_*` 為 Phase 5a Semantic Gateway 所需。  
> 若未設定，Semantic Gateway 會自動 SKIP（fail-open），系統照常運作。

設定完成後，回到 **Deployments** 重新部署（Redeploy）。

### 1.3 測試後端

```bash
curl https://peace-mind.vercel.app/health
# 應回傳：{"status":"ok","service":"PeaceMind","version":"0.1.0"}
```

---

## Step 2：部署前端 (peace-mind-sjiv)

### 2.1 在 Vercel 建立第二個 Project

1. 再次前往 [vercel.com/new](https://vercel.com/new)，Import 同一個 repo
2. **Root Directory** 設為 `frontend`
3. **Framework Preset** 選 **Vite**
4. **Build Command**: `npm run build`
5. **Output Directory**: `dist/public`

### 2.2 設定環境變數

| 變數名稱 | 值 |
|---------|---|
| `VITE_API_URL` | `https://peace-mind.vercel.app` |

### 2.3 部署

點 **Deploy**，完成後即可訪問前端網址。

---

## CORS 設定（重要）

後端 `app/main.py` 的 `allow_origins` 已包含前端網址：

```python
allow_origins=[
    "https://peace-mind-sjiv.vercel.app",  # 生產前端
    "http://localhost:5000",               # 本地開發
]
```

如果前端 URL 有變更，記得更新 `app/main.py` 並重新部署後端。

---

## 本地開發

```bash
# Terminal 1 — 後端
cd PeaceMind
cp .env.example .env   # 填入 Azure 憑證
uvicorn app.main:app --reload --port 8000

# Terminal 2 — 前端
cd PeaceMind/frontend
npm run dev            # :5000 → proxy → :8000
```

本地 `.env` 範例：
```bash
AZURE_OPENAI_API_KEY=your_key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_API_VERSION=2024-02-01
AZURE_CONTENT_SAFETY_ENDPOINT=https://your-cs-resource.cognitiveservices.azure.com
AZURE_CONTENT_SAFETY_KEY=your_cs_key
SEMANTIC_GATEWAY_FAIL_OPEN=true
```

---

## 前端頁面路由

| 路徑 | 頁面 | 說明 |
|------|------|------|
| `/#/` | ChatPage | 主聊天頁（Boon 對話） |
| `/#/courses` | CoursesPage | 自學資源 · 影片課堂 |

> 使用 wouter hash routing — URL 格式為 `https://peace-mind-sjiv.vercel.app/#/courses`

---

## 注意事項

| 項目 | 說明 |
|------|------|
| **Cold Start** | Vercel Serverless 首次請求約 2–4 秒，後續請求正常 |
| **Session Memory** | 目前使用 InMemory store，每個 serverless instance 獨立。Phase 5c（Redis）升級後才能跨 instance 共享 |
| **Hobby Tier Timeout** | 10 秒；LLM 請求通常在限制內 |
| **Commit Author** | 見上方「⚠️ Vercel Hobby Tier」說明 |
| **Secrets** | `.env` 不要 commit，環境變數只在 Vercel Dashboard 設定 |

---

*文件更新時間：2026-03-19*
