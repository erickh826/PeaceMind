"""
Example Selector（Phase 3，T-Q16–T-Q18）
依目前使用者的 Profile + 已演化主題，挑出 applicable_conditions_json 命中的
治療師範例，注入 L2 prompt（見 app/prompts/system_prompt.py），並在回覆寫入
memory 後記錄到 example_usage_log（T-Q18 有效性追蹤）。

條件比對共用 app/core/condition_matching.py（跟 Persona Resolver 同一套規則：
AND 邏輯、空條件或未知鍵一律不匹配）。

排序與上限：命中的範例依「條件鍵數量」由多到少排序（AND 邏輯下鍵越多代表條件
越具體、越不容易誤命中），同分時 created_at 較新者優先，最後用 id 當決定性的
tie-break（同一個 transaction 種入的多筆範例 created_at 會相同，因為 now() 是
transaction 開始時間）。最多取 EXAMPLE_MATCH_LIMIT 則，避免 prompt 過長、稀釋
每則範例的影響力。

DATABASE_URL 未設定、沒有 session_id、或該 session 還沒有對應 User 記錄時，
回傳空結果，不阻擋對話。

已知取捨：這裡又對 users 表做了一次獨立查詢（Persona Resolver、Context Assembly
各一次，這裡第三次）。為了不讓使用記錄寫入再查第四次，查到的 user_id 會一起
回傳給呼叫端沿用。
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field

from app.core.condition_matching import EVOLVED_TOPIC_THRESHOLD, condition_matches

logger = logging.getLogger(__name__)

EXAMPLE_MATCH_LIMIT = 2  # 單輪對話最多注入幾則範例


@dataclass(frozen=True)
class SelectedExample:
    id: str
    content: str
    usage_mode: str
    attribution_mode: str

    def to_prompt_dict(self) -> dict:
        return {"usage_mode": self.usage_mode, "content": self.content}


@dataclass(frozen=True)
class ExampleSelection:
    user_id: str | None = None
    examples: list[SelectedExample] = field(default_factory=list)


EMPTY_SELECTION = ExampleSelection()


async def select_applicable_examples(session_client_key: str | None) -> ExampleSelection:
    if not os.environ.get("DATABASE_URL") or not session_client_key:
        return EMPTY_SELECTION

    from sqlalchemy import select

    from app.db import get_session
    from app.db.models import User
    from app.db.models_example import ResponseExample
    from app.db.models_profile import ProfileTopic, UserProfile

    async with get_session() as db:
        user_row = await db.scalar(select(User).where(User.external_ref == session_client_key))
        if user_row is None:
            return EMPTY_SELECTION

        profile_row = await db.get(UserProfile, user_row.id)

        topic_result = await db.execute(
            select(ProfileTopic.topic).where(
                ProfileTopic.user_id == user_row.id,
                ProfileTopic.mention_count >= EVOLVED_TOPIC_THRESHOLD,
            )
        )
        evolved_topics = set(topic_result.scalars().all())

        examples_result = await db.execute(
            select(ResponseExample).where(ResponseExample.status == "active")
        )
        matched = [
            ex
            for ex in examples_result.scalars().all()
            if condition_matches(ex.applicable_conditions_json, profile_row, evolved_topics)
        ]
        matched.sort(
            key=lambda ex: (len(ex.applicable_conditions_json), ex.created_at, str(ex.id)),
            reverse=True,
        )

        return ExampleSelection(
            user_id=str(user_row.id),
            examples=[
                SelectedExample(
                    id=str(ex.id),
                    content=ex.content,
                    usage_mode=ex.usage_mode,
                    attribution_mode=ex.attribution_mode,
                )
                for ex in matched[:EXAMPLE_MATCH_LIMIT]
            ],
        )


async def log_example_usage(
    selection: ExampleSelection, session_client_key: str, message_id: str | None
) -> None:
    """
    把本輪注入 prompt 的範例寫入 example_usage_log（T-Q18）。
    message_id 是這輪 assistant 回覆的 messages.id（InMemory store 時為 None）。
    寫入失敗只記 log，不讓記錄問題中斷已經產生好的回覆。
    """
    if not os.environ.get("DATABASE_URL") or not selection.examples or selection.user_id is None:
        return

    from sqlalchemy import select

    from app.db import get_session
    from app.db.models import ConversationSession
    from app.db.models_example import ExampleUsageLog

    try:
        async with get_session() as db:
            session_db_id = await db.scalar(
                select(ConversationSession.id).where(
                    ConversationSession.client_key == session_client_key
                )
            )
            for ex in selection.examples:
                db.add(
                    ExampleUsageLog(
                        example_id=uuid.UUID(ex.id),
                        message_id=uuid.UUID(message_id) if message_id else None,
                        session_id=session_db_id,
                        user_id=uuid.UUID(selection.user_id),
                    )
                )
            await db.commit()
    except Exception:
        logger.exception("example_usage_log write failed | session=%s", session_client_key)
