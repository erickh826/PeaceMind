from __future__ import annotations

from typing import Protocol

# Phase 0：方法改為 async，讓 PostgresConversationStore 能用 async SQLAlchemy
# 而不需要在事件迴圈中做阻塞式呼叫。InMemoryConversationStore 的實作沒有真正
# 的 I/O，但仍需宣告為 async def 才符合這個 Protocol。


class ConversationStore(Protocol):
    """Storage contract for conversation memory."""

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        ...

    async def ensure_session(self, session_id: str) -> None:
        """
        確保 session 已存在（Phase 4）。Rule Engine 依建立 session 當下的資料庫快照判定
        規則版本，原本 session 要到回覆後 append() 才建立，首輪沒有判定基準。沒有結構化
        session 的實作什麼都不做。
        """
        ...

    async def append(
        self,
        session_id: str,
        role: str,
        content: str,
        persona_id: str | None = None,
        rule_id: str | None = None,
        rule_version_id: str | None = None,
        output_replaced: bool = False,
    ) -> str | None:
        """
        寫入一則訊息，回傳新 message 的 id（沒有結構化 id 的實作、或訊息被略過時回傳 None）。
        rule_id / rule_version_id / output_replaced 是 Phase 4 的每輪規則紀錄，只有
        PostgresConversationStore 會儲存。
        """
        ...

    async def reset(self, session_id: str) -> None:
        ...
