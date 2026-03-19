# Phase 5 — 架構升級設計文件
**專案**：PeaceMind — 心理諮詢 AI 助理「阿本」  
**版本**：Phase 5 + Memory + Cross-lingual + CoursesPage  
**日期**：2026-03-19  
**基於**：Phase 4 Red Team Report 未修補風險

---

## 背景

Phase 4 Red Team 達到 99.3% 測試通過率，防禦機制以 Regex 黑名單為核心。  
報告中識別出兩類根本性架構弱點，純 Regex 無法解決：

| 風險 | Regex 的極限 |
|------|------------|
| 語義/編碼繞過 | 死背單字，不理解意圖；Base64、Leetspeak、同義詞全部可繞過 |
| 多輪對話注入 | Stateless（無狀態），只看單句，無法偵測「溫水煮青蛙」式漸進越獄 |

Phase 5 目標：**不取代 Regex**，而是在其上疊加語意感知防禦，構成真正的 Defense-in-Depth。

---

## 當前架構總覽（Phase 5 + Memory + Cross-lingual）

```
使用者輸入
        │
        ▼
┌──────────────────────────────────────────┐
│  Layer 1a: Input Gateway（現有 + 強化）  │
│  • 語言偵測（langdetect）                │
│    - SHORT_TEXT_THRESHOLD = 15           │
│    - CJK Han → 支援（zh-tw / yue）       │
│    - Kana / Hangul / Cyrillic → BLOCKED  │
│  • 長度限制（後端 1500 / 前端 1000）      │
│  • Prompt Injection 關鍵字黑名單 (EN+ZH) │
│  • 危機關鍵字偵測（優先）                 │
│  速度: ~2ms                              │
└───────────────────┬──────────────────────┘
                    │ 通過
                    ▼
┌──────────────────────────────────────────┐  ← Phase 5a ✅ 已上線
│  Layer 1b: Semantic Gateway              │
│  • Azure AI Content Safety Prompt Shield │
│  • SEMANTIC_GATEWAY_FAIL_OPEN=true       │
│    (API 失敗 → SKIPPED，服務不中斷)       │
│  速度: ~50-100ms（Azure API call）        │
└───────────────────┬──────────────────────┘
                    │ 通過
                    ▼
┌──────────────────────────────────────────┐  ← Phase 5b ✅ 已上線
│  Layer 1c: Multi-turn Risk Scorer        │
│  • WINDOW_SIZE = 8 輪                    │
│  • DECAY_FACTOR = 0.7（時間衰減）         │
│  • WARN_THRESHOLD = 0.50                 │
│  • BLOCK_THRESHOLD = 0.80               │
│  速度: ~2ms（本地計算）                  │
└───────────────────┬──────────────────────┘
                    │ 允許通過
                    ▼
┌──────────────────────────────────────────┐
│  Layer 2: LLM Core（現有）               │
│  • Azure OpenAI GPT-4o                   │
│  • Sandwich Prompt（XML 隔離）            │
│  • [LANGUAGE CONSTRAINT]（繁中/粵語）     │
│  • HIGH_RISK_HINT（5b warn 時注入）       │
└───────────────────┬──────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────┐
│  Layer 3: Output Gateway（現有）          │
│  • 處方藥（EN + ZH）                      │
│  • 診斷術語                               │
│  • 系統提示外洩（EN + ZH）                │
│  • 非支援文字系統（≥8 chars, ≥25% ratio） │
└───────────────────┬──────────────────────┘
                    │
                    ▼
                使用者回應
```

---

## Phase 5a：Semantic Gateway（Prompt Shields）

### 實作狀態：✅ 已上線

**檔案**：`app/gateways/semantic_gateway.py`

**行為**：
- 呼叫 Azure AI Content Safety Prompt Shields API
- `attackDetected: true` → 返回溫暖阻擋訊息
- API 超時/失敗 + `SEMANTIC_GATEWAY_FAIL_OPEN=true` → SKIPPED（不阻擋）
- API 超時/失敗 + `SEMANTIC_GATEWAY_FAIL_OPEN=false` → BLOCKED（保守模式）

**環境變數**：
```bash
AZURE_CONTENT_SAFETY_ENDPOINT=https://<resource>.cognitiveservices.azure.com
AZURE_CONTENT_SAFETY_KEY=<key>
SEMANTIC_GATEWAY_FAIL_OPEN=true
```

---

## Phase 5b：Multi-turn Risk Scorer

### 實作狀態：✅ 已上線

**檔案**：`app/gateways/multiturn_gateway.py`

**三級行動**：

| 累積分數 | 行動 | 說明 |
|---------|------|------|
| < 0.50 | Allow | 正常通過 |
| 0.50–0.80 | Warn | 不阻擋，但在系統提示注入 HIGH_RISK_HINT |
| > 0.80 | Block | 返回溫暖阻擋訊息，不呼叫 LLM |

---

## Cross-lingual Security（跨語言防禦）

### 實作狀態：✅ 已上線（2026-03-18）

**檔案**：`app/core/language_detector.py`

**設計重點**：

```python
SHORT_TEXT_THRESHOLD = 15  # 純 ASCII ≤15 字元 → 直接視為 EN，跳過 langdetect

# Hard script gates（快速判斷，無需 langdetect）
# Kana / Hangul / Cyrillic → 立即不支援
# CJK Han → zh-tw / yue（支援）

# 人道主義設計：短法文危機句（Je veux mourir）
# 即使被 langdetect 判為 FR 也刻意通過 → LLM 正常回應（用戶看到熱線）
```

**L3 輸出閘門補充**：
- 非支援文字腳本 ≥8 字元 **且** 非支援腳本比例 ≥25% → INTERCEPTED
- 防止 LLM 意外以不支援語言回應

---

## Session Memory（伺服器端記憶）

### 實作狀態：✅ 已上線（InMemory PoC）

**設計**：
- `session_id` 為 Optional — 有 → 使用 server-side memory；無 → stateless fallback
- 前端：`crypto.randomUUID()` 存於 React state（非 localStorage）
- `InMemoryConversationStore`：每 session 最多 20 條訊息，TTL 60 分鐘，RLock 防競態

**API**：
- `POST /api/v1/chat` — 帶 `session_id` 時使用 server-side memory
- `POST /api/v1/reset` — 清除指定 session，前端生成新 session_id

**Phase 5c（待做）**：Redis / Upstash 持久化，防止 session reset 繞過多輪評分。

---

## CoursesPage + Inline Recommendation Engine

### 實作狀態：✅ 已上線（2026-03-19）

**前端新增功能**：

### CoursesPage（`/courses`）
- 7 個 YouTube 影片（靜觀冥想、情緒管理、大腦科學、自我提升、正向思維、人際關係、內在療癒）
- 類別 filter pills + 「阿本今日推薦」section
- 影片 modal（YouTube embed + autoplay）

### Inline Recommendation Engine（ChatPage）
- 每條 Boon 回覆後，掃描用戶訊息關鍵字 → 匹配最相關影片
- `introLine`：自然引入句（例：「你可以先看下影片裡提到的…」）
- `RecCard`：縮圖 + 類別 tag + 標題 + 阿本推薦理由
- 點擊 RecCard → **原頁 VideoModal**（不跳轉，保留對話情境）

**關鍵字規則**（`ChatPage.tsx` `REC_RULES`）：

| 關鍵字 | 觸發影片 |
|--------|---------|
| 靜觀/冥想/呼吸/平靜 | 【靜觀冥想】靜觀三分鐘 |
| 焦慮/擔心/緊張/不安 | 當焦慮感來襲，心理師教你這樣做 |
| 情緒/憤怒/難過/崩潰 | 如何管理情緒？從大腦科學角度 |
| 拖延/冇動力/deadline | 三步驟教你改善拖延症！ |
| 正向/肯定/自信 | 每日 10 分鐘廣東話肯定句 |
| 關係/愛情/伴侶/分手 | 沉船其實不是愛 |
| 內在/童年/療癒/創傷 | 療癒你的內在小孩 |
| 壓力/burnout/累/攰 | 【靜觀冥想】靜觀三分鐘（fallback） |

---

## 效能影響分析

| 層 | 延遲 | 說明 |
|----|------|------|
| L1a 語言偵測 + Regex | ~2ms | 本地，快速 |
| L1b Prompt Shields | +50ms | Azure API，可並行 |
| L1c Multi-turn Scorer | +2ms | 本地計算 |
| L2 LLM | ~800ms | 主要延遲 |
| L3 Output | ~1ms | 本地 Regex |
| **總端對端** | **~855ms** | Prompt Shields 可與 LLM 並行優化 |

---

## 升級路徑

```
Phase 4（Regex Only）
  ↓ ✅ 完成
Phase 5a：Semantic Gateway（Prompt Shields）
  ↓ ✅ 完成
Phase 5b：Multi-turn Risk Scorer
  ↓ ✅ 完成
Cross-lingual Security（SHORT_TEXT_THRESHOLD 修補）
  ↓ ✅ 完成
Session Memory（InMemory PoC）
  ↓ ✅ 完成
CoursesPage + Inline Rec Engine
  ↓ ✅ 完成
Phase 5c（待做）：Redis Session Store
  • 持久化 Multi-turn 分數，防 session reset 繞過
  • 預估工時：3 小時
```

---

## 未解決的根本限制

| 風險 | 說明 | 建議 |
|------|------|------|
| Session Reset 繞過 | 重新整理後 Multi-turn 分數歸零 | Phase 5c Redis |
| LLM 本身越獄脆弱性 | 複雜攻擊最終可能穿透所有外部防禦 | Azure OpenAI 內建 Content Filters |
| 「我想死去活來」假陽性 | 粵語慣用語 | 白名單分類器 |
| InMemory TTL | 60 分鐘後 session 自動失效 | Phase 5c Redis 持久化 |

---

*文件更新時間：2026-03-19*  
*參考資料：*
- *[Azure Prompt Shields](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection)*
- *[Deceptive Delight — Palo Alto Unit 42](https://unit42.paloaltonetworks.com/jailbreak-llms-through-camouflage-distraction/)*
- *[ZEDD — arXiv 2601.12359](https://arxiv.org/html/2601.12359v1)*
