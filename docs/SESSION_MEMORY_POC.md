# Session Memory PoC 說明

## 目標

為聊天流程加入伺服器端會話記憶，並提供 Reset 控制，讓使用者可一鍵回到全新對話。

## 資料流

1. 前端建立並持久化 `session_id`（localStorage）。
2. 送出訊息時，`POST /api/v1/chat` 攜帶 `session_id`。
3. 後端依 `session_id` 從 Session Memory Store 取回歷史，送入 LLM。
4. 回覆通過輸出閘門後，將 user/assistant 訊息寫回該 session。
5. 使用者按 Reset，前端呼叫 `POST /api/v1/reset` 清空伺服器該 session 記憶，並重置 UI。

## API

### POST /api/v1/chat

Request:

```json
{
  "session_id": "uuid-or-stable-id",
  "message": "我最近有點累",
  "history": []
}
```

Response:

```json
{
  "reply": "我有聽到你說你很累...",
  "intercepted": false,
  "crisis": false
}
```

> `history` 目前僅保留向後相容用途，伺服器端會話記憶為主要上下文來源。

### POST /api/v1/reset

Request:

```json
{
  "session_id": "uuid-or-stable-id"
}
```

Response:

```json
{
  "status": "cleared"
}
```

## 目前實作邊界（PoC）

- 儲存型態：in-memory（process 內記憶體）。
- 會話上限：每個 session 僅保留最近 20 則訊息。
- 到期策略：預設 TTL 60 分鐘未活動即清理。
- 服務重啟：記憶清空。
- 多實例部署：不同實例間不共享記憶，可能出現記憶漂移。

## 後續演進

1. 將 store adapter 由 in-memory 替換為 Redis 或資料庫。
2. 以 user identity 綁定 session lifecycle。
3. 加入 reset 審計事件（例如 who/when/session_id）。
