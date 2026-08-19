from __future__ import annotations

from typing import Protocol

# Phase 0：方法改為 async，讓 PostgresConversationStore 能用 async SQLAlchemy
# 而不需要在事件迴圈中做阻塞式呼叫。InMemoryConversationStore 的實作沒有真正
# 的 I/O，但仍需宣告為 async def 才符合這個 Protocol。


class ConversationStore(Protocol):
    """Storage contract for conversation memory."""

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        ...

    async def append(
        self, session_id: str, role: str, content: str, persona_id: str | None = None
    ) -> None:
        ...

    async def reset(self, session_id: str) -> None:
        ...
