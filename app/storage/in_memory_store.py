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

    async def ensure_session(self, session_id: str) -> None:
        # 沒有結構化 session（也沒有 DB，Rule Engine 不會啟用），不需要判定基準
        return None

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
        # persona_id / rule_id / rule_version_id / output_replaced 目前只有 PostgresConversationStore 會儲存（見 T-Q14），
        # InMemoryConversationStore 沒有結構化欄位可放，靜默忽略即可。
        # 同理沒有結構化的 message id 可回傳，一律回傳 None（Phase 3 的
        # example_usage_log 只在有 DB 時才會寫入，不會用到這個回傳值）。
        if role not in ("user", "assistant"):
            return None

        text = content.strip()
        if not text:
            return None

        with self._lock:
            self._prune_expired()
            bucket = self._sessions.setdefault(session_id, [])
            bucket.append({"role": role, "content": text})
            if len(bucket) > self.max_messages:
                self._sessions[session_id] = bucket[-self.max_messages :]
            self._updated_at[session_id] = time.time()
        return None

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
