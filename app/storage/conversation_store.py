from __future__ import annotations

from typing import Protocol


class ConversationStore(Protocol):
    """Storage contract for conversation memory."""

    def get_history(self, session_id: str) -> list[dict[str, str]]:
        ...

    def append(self, session_id: str, role: str, content: str) -> None:
        ...

    def reset(self, session_id: str) -> None:
        ...
