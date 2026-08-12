import os

from app.storage.conversation_store import ConversationStore
from app.storage.in_memory_store import InMemoryConversationStore

# Phase 0：有設定 DATABASE_URL 才用 Postgres-backed store，
# 沒設定時（本地快速開發 / 測試環境）退回 InMemory，避免每個開發者都要先起資料庫才能跑。
if os.environ.get("DATABASE_URL"):
    from app.storage.postgres_store import PostgresConversationStore

    conversation_store: ConversationStore = PostgresConversationStore()
else:
    conversation_store: ConversationStore = InMemoryConversationStore()
