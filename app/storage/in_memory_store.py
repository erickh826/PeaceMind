from __future__ import annotations

import time
from threading import RLock

from app.storage.conversation_store import ConversationStore


class InMemoryConversationStore(ConversationStore):
    """Session-scoped conversation memory for PoC use."""

    def __init__(self, max_messages: int = 20, ttl_seconds: int = 60 * 60):
        self.max_messages = max_messages
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, list[dict[str, str]]] = {}
        self._updated_at: dict[str, float] = {}
        self._lock = RLock()

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        with self._lock:
            self._prune_expired()
            history = self._sessions.get(session_id, [])
            return [msg.copy() for msg in history]

    async def append(self, session_id: str, role: str, content: str) -> None:
        if role not in ("user", "assistant"):
            return

        text = content.strip()
        if not text:
            return

        with self._lock:
            self._prune_expired()
            bucket = self._sessions.setdefault(session_id, [])
            bucket.append({"role": role, "content": text})
            if len(bucket) > self.max_messages:
                self._sessions[session_id] = bucket[-self.max_messages :]
            self._updated_at[session_id] = time.time()

    async def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._updated_at.pop(session_id, None)

    def _prune_expired(self) -> None:
        if self.ttl_seconds <= 0:
            return

        now = time.time()
        expired = [
            sid
            for sid, updated_at in self._updated_at.items()
            if now - updated_at > self.ttl_seconds
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._updated_at.pop(sid, None)
