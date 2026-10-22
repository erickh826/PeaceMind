"""
規則沙盒（Phase 5.1，T-Q6）

用治療師輸入的單句或多輪模擬對話，強制套用「被測規則的 action」（therapy / tone /
persona / 指定範例），走真的 chat_with_llm()。L1a / L1b / L1c 與 L3 安全層照樣跑，
所以沙盒看到的就是學生會看到的安全行為。

跟正式 /chat 的差異（刻意的）：
- 不寫 messages / sessions / profile / example_usage_log，不呼叫 Rule Engine 與 Context
  Assembly；沙盒結果只寫進 rule_test_runs（由 router 負責）。
- 不做範例自動匹配（沒有真實 profile），只載入規則指定的範例（含非 active：
  測試 draft 規則時引用的範例也可能還是 draft）。
- 模擬 profile 只用來回報「這份 profile 會不會命中規則條件」，不影響上面的強制套用。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace

from app.core.condition_matching import rule_condition_matches
from app.core.crisis_handler import get_crisis_response
from app.core.example_selector import EXAMPLE_MATCH_LIMIT
from app.core.llm_client import chat_with_llm
from app.core.persona_resolver import FALLBACK_PERSONA
from app.gateways.input_gateway import InputStatus, check_input
from app.gateways.multiturn_gateway import RiskAction, evaluate_multiturn_risk
from app.gateways.output_gateway import OutputStatus, check_output
from app.gateways.semantic_gateway import SemanticStatus, check_semantic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxResult:
    output: str
    safety_outcome: str
    persona_name: str | None = None
    example_ids: list[str] = field(default_factory=list)


def simulate_profile_match(conditions: dict, profile: dict | None) -> tuple[bool, dict[str, bool]]:
    """回傳（整體是否命中, 每個條件鍵各自是否成立）。profile 為 None 時不呼叫。"""
    profile = profile or {}
    row = SimpleNamespace(
        year_of_study=profile.get("year_of_study"),
        risk_level=profile.get("risk_level", "none"),
    )
    topic_counts = dict(profile.get("topic_counts") or {})

    per_key: dict[str, bool] = {}
    for key, value in conditions.items():
        if key == "min_topic_mentions":
            continue
        single = {key: value}
        if key == "topics_include" and "min_topic_mentions" in conditions:
            single["min_topic_mentions"] = conditions["min_topic_mentions"]
        per_key[key] = rule_condition_matches(single, row, topic_counts)

    return rule_condition_matches(conditions, row, topic_counts), per_key


async def _load_persona(db, persona_id: str | None):
    from sqlalchemy import select

    from app.db.models_persona import Persona

    row = None
    if persona_id:
        row = await db.get(Persona, uuid.UUID(persona_id))
    if row is None:
        row = await db.scalar(
            select(Persona).where(Persona.is_default.is_(True), Persona.status == "active")
        )
    if row is None:
        return FALLBACK_PERSONA.name, FALLBACK_PERSONA.system_prompt_fragment
    return row.name, row.system_prompt_fragment


async def _load_examples(db, example_ids: list[str]):
    from sqlalchemy import select

    from app.db.models_example import ResponseExample

    ordered: list[uuid.UUID] = []
    for raw in example_ids:
        value = uuid.UUID(str(raw))
        if value not in ordered:
            ordered.append(value)
    ordered = ordered[:EXAMPLE_MATCH_LIMIT]
    if not ordered:
        return []
    rows = (
        await db.execute(select(ResponseExample).where(ResponseExample.id.in_(ordered)))
    ).scalars().all()
    by_id = {r.id: r for r in rows}
    return [by_id[i] for i in ordered if i in by_id]


async def run_sandbox(db, content: dict, turns: list[dict]) -> SandboxResult:
    """turns 最後一則必須是 user（router 驗證）；其餘當作對話歷史。LLM 失敗時往外拋例外。"""
    from app.routers.chat import ATTRIBUTION_TAG, _BLOCK_MSG, _MULTITURN_BLOCK_MSG

    history = turns[:-1]
    message = turns[-1]["content"].strip()

    input_result = check_input(message)
    if input_result.status == InputStatus.BLOCKED:
        return SandboxResult(input_result.message, "input_blocked")
    if input_result.status == InputStatus.CRISIS:
        return SandboxResult(get_crisis_response(message), "crisis")

    semantic_result = await check_semantic(message)
    if semantic_result.status == SemanticStatus.ATTACK:
        return SandboxResult(_BLOCK_MSG, "semantic_blocked")

    mt_result = evaluate_multiturn_risk(history=history, current_message=message)
    if mt_result.action == RiskAction.BLOCK:
        return SandboxResult(_MULTITURN_BLOCK_MSG, "multiturn_blocked")
    security_hint = "HIGH_RISK" if mt_result.action == RiskAction.WARN else None

    action = content.get("action_json") or {}
    persona_name, persona_fragment = await _load_persona(db, action.get("persona_id"))
    examples = await _load_examples(db, action.get("example_ids") or [])
    strategy = (
        {"therapy": action.get("therapy"), "tone": action.get("tone")}
        if action.get("therapy") or action.get("tone")
        else None
    )

    reply = await asyncio.to_thread(
        chat_with_llm,
        message,
        conversation_history=history,
        security_hint=security_hint,
        persona_name=persona_name,
        persona_fragment=persona_fragment,
        examples=[{"usage_mode": ex.usage_mode, "content": ex.content} for ex in examples],
        strategy=strategy,
    )

    output_result = check_output(reply)
    if output_result.status == OutputStatus.INTERCEPTED:
        return SandboxResult(
            output_result.response, "output_replaced", persona_name, [str(ex.id) for ex in examples]
        )

    final = output_result.response
    if any(ex.usage_mode == "direct_quote" and ex.attribution_mode == "attributed" for ex in examples):
        final += ATTRIBUTION_TAG
    return SandboxResult(final, "passed", persona_name, [str(ex.id) for ex in examples])
