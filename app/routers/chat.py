"""
Chat Router — 主要對話端點
整合五層防禦機制：

  Layer 1a: Input Gateway    — Regex 黑名單（長度/注入/危機）
  Layer 1b: Semantic Gateway — Azure AI Content Safety Prompt Shields
  Layer 1c: Multi-turn Scorer — Sliding Window 時序風險評分
  Layer 2:  LLM Core         — Azure OpenAI + Sandwich Prompt
  Layer 3:  Output Gateway   — 藥名/診斷/系統洩露 Regex 過濾
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.gateways.input_gateway import check_input, InputStatus
from app.gateways.semantic_gateway import check_semantic, SemanticStatus
from app.gateways.multiturn_gateway import evaluate_multiturn_risk, RiskAction
from app.gateways.output_gateway import check_output, OutputStatus
from app.core.llm_client import chat_with_llm
from app.core.crisis_handler import get_crisis_response

logger = logging.getLogger(__name__)
router = APIRouter()

# ── 共用阻擋訊息 ──────────────────────────────────────────────────────────────
_BLOCK_MSG = (
    "我感受到你可能有些困惑或憤怒，"
    "但我只是一個心理支持助理，"
    "有什麼情緒上的事情想跟我說嗎？"
)
_MULTITURN_BLOCK_MSG = (
    "我注意到我們的對話方向有些偏離了。"
    "我是阿本，只能提供情緒支持，"
    "不是一個可以被重新設定的系統。"
    "有什麼真實的感受想和我分享嗎？"
)


class Message(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1600)
    history: list[Message] = Field(default_factory=list, max_length=20)


class ChatResponse(BaseModel):
    reply: str
    intercepted: bool = False
    crisis: bool = False


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    主要對話端點，執行完整五層防禦流程。

    防禦層順序（由快到慢）：
      L1a → L1b → L1c → L2 → L3
    """
    user_message = request.message.strip()
    history = [msg.model_dump() for msg in request.history]

    # ── Layer 1a: Input Gateway（Regex）───────────────────────────────────────
    input_result = check_input(user_message)

    if input_result.status == InputStatus.BLOCKED:
        logger.warning("L1a BLOCKED | len=%d | snippet=%.50s", len(user_message), user_message)
        return ChatResponse(reply=input_result.message, intercepted=True)

    if input_result.status == InputStatus.CRISIS:
        logger.warning("L1a CRISIS | snippet=%.50s", user_message)
        crisis_reply = get_crisis_response(user_message)
        return ChatResponse(reply=crisis_reply, crisis=True)

    # ── Layer 1b: Semantic Gateway（Prompt Shields）──────────────────────────
    # async call — 不阻塞，fail-open（見 semantic_gateway.py）
    semantic_result = await check_semantic(user_message)

    if semantic_result.status == SemanticStatus.ATTACK:
        logger.warning("L1b BLOCKED (semantic) | snippet=%.50s", user_message)
        return ChatResponse(reply=_BLOCK_MSG, intercepted=True)

    # ── Layer 1c: Multi-turn Risk Scorer（Sliding Window）───────────────────
    mt_result = evaluate_multiturn_risk(
        history=history,
        current_message=user_message,
    )

    if mt_result.action == RiskAction.BLOCK:
        logger.warning(
            "L1c BLOCKED (multi-turn) | score=%.3f | snippet=%.50s",
            mt_result.cumulative_score, user_message,
        )
        return ChatResponse(reply=_MULTITURN_BLOCK_MSG, intercepted=True)

    # WARN → 傳遞 security_hint 給 LLM，靜默強化角色護欄
    security_hint = "HIGH_RISK" if mt_result.action == RiskAction.WARN else None

    if security_hint:
        logger.warning(
            "L1c WARN (multi-turn) | score=%.3f | injecting security hint",
            mt_result.cumulative_score,
        )

    # ── Layer 2: LLM Core（Azure OpenAI + Sandwich Prompt）──────────────────
    try:
        llm_reply = chat_with_llm(
            user_message,
            conversation_history=history,
            security_hint=security_hint,
        )
    except Exception as e:
        logger.error("LLM call failed: %s", str(e))
        raise HTTPException(
            status_code=503,
            detail="AI 服務暫時無法使用，請稍後再試。",
        )

    # ── Layer 3: Output Gateway（Regex 輸出過濾）────────────────────────────
    output_result = check_output(llm_reply)

    if output_result.status == OutputStatus.INTERCEPTED:
        logger.warning(
            "L3 INTERCEPTED | rule=%s | snippet=%.80s",
            output_result.triggered_rule,
            llm_reply,
        )
        return ChatResponse(reply=output_result.response, intercepted=True)

    return ChatResponse(reply=output_result.response)
