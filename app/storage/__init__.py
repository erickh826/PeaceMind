from app.storage.conversation_store import ConversationStore
from app.storage.in_memory_store import InMemoryConversationStore

# PoC default store. Can be swapped to DB/Redis implementation later.
conversation_store: ConversationStore = InMemoryConversationStore()
