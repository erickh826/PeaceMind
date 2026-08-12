"""
Chat Router — 主要對話端點
整合五層防禦機制 + Server-side Session Memory：

  Layer 1a: Input Gateway    — Regex 黑名單（長度/注入/危機）
  Layer 1b: Semantic Gateway — Azure AI Content Safety Prompt Shields
  Layer 1c: Multi-turn Scorer — Sliding Window 時序風險評分
  Layer 2:  LLM Core         — Azure OpenAI + Sandwich Prompt
  Layer 3:  Output Gateway   — 藥名/診斷/系統洩露 Regex 過濾

Memory：
  - session_id 為選填（可由前端傳入，或由後端從 X-Session-ID header 讀取）
  - 若未提供 session_id，退回純無狀態模式（使用前端傳入的 history）
  - InMemoryConversationStore：每 instance 獨立，PoC 用途
  - Phase 5c (Redis) 可在之後替換 store 實作
"""

import logging
import uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateways.input_gateway import check_input, InputStatus
from app.gateways.semantic_gateway import check_semantic, SemanticStatus
from app.gateways.multiturn_gateway import evaluate_multiturn_risk, RiskAction
from app.gateways.output_gateway import check_output, OutputStatus
from app.core.llm_client import chat_with_llm
from app.core.crisis_handler import get_crisis_response
from app.core.persona_resolver import resolve_persona, record_persona_usage
from app.storage import conversation_store

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
    # session_id 為選填：提供時啟用 server-side memory；不提供時使用 history (stateless)
    session_id: str | None = Field(default=None, min_length=8, max_length=128)
    # Legacy / stateless fallback：前端傳入歷史
    history: list[Message] = Field(default_factory=list, max_length=20)


class ChatResponse(BaseModel):
    reply: str
    intercepted: bool = False
    crisis: bool = False
    session_id: str | None = None   # 回傳 session_id 供前端儲存


class ResetRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=128)


class ResetResponse(BaseModel):
    status: str


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    主要對話端點，執行完整五層防禦流程。

    防禦層順序（由快到慢）：
      L1a → L1b → L1c → L2 → L3
    """
    user_message = request.message.strip()
    session_id = request.session_id

    # ── Memory：決定 history 來源 ──────────────────────────────────────────────
    if session_id:
        # Server-side memory 模式：從 store 讀取，忽略前端傳入的 history
        history = await conversation_store.get_history(session_id)
    else:
        # Stateless 模式：使用前端傳入的 history（兼容舊版前端）
        history = [msg.model_dump() for msg in request.history]

    # ── Layer 1a: Input Gateway（Regex）───────────────────────────────────────
    input_result = check_input(user_message)

    if input_result.status == InputStatus.BLOCKED:
        logger.warning("L1a BLOCKED | len=%d | snippet=%.50s", len(user_message), user_message)
        return ChatResponse(reply=input_result.message, intercepted=True, session_id=session_id)

    if input_result.status == InputStatus.CRISIS:
        logger.warning("L1a CRISIS | snippet=%.50s", user_message)
        crisis_reply = get_crisis_response(user_message)
        # 危機訊息也記入 memory
        if session_id:
            await conversation_store.append(session_id, "user", user_message)
            await conversation_store.append(session_id, "assistant", crisis_reply)
        return ChatResponse(reply=crisis_reply, crisis=True, session_id=session_id)

    # ── Layer 1b: Semantic Gateway（Prompt Shields）──────────────────────────
    # async call — fail-open（見 semantic_gateway.py）
    semantic_result = await check_semantic(user_message)

    if semantic_result.status == SemanticStatus.ATTACK:
        logger.warning("L1b BLOCKED (semantic) | snippet=%.50s", user_message)
        return ChatResponse(reply=_BLOCK_MSG, intercepted=True, session_id=session_id)

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
        return ChatResponse(reply=_MULTITURN_BLOCK_MSG, intercepted=True, session_id=session_id)

    # WARN → 傳遞 security_hint 給 LLM，靜默強化角色護欄
    security_hint = "HIGH_RISK" if mt_result.action == RiskAction.WARN else None

    if security_hint:
        logger.warning(
            "L1c WARN (multi-turn) | score=%.3f | injecting security hint",
            mt_result.cumulative_score,
        )

    # ── Persona Resolver（Phase 1）─────────────────────────────────────────
    # Phase 1 目前只解析「治療師手動指派」或「系統預設 persona」；
    # Phase 2 引入真實 Profile/主題資料後才會補上自動匹配邏輯。
    persona = await resolve_persona(user_client_key=session_id)
    if session_id:
        await record_persona_usage(session_id, persona)

    # ── Layer 2: LLM Core（Azure OpenAI + Sandwich Prompt）──────────────────
    try:
        llm_reply = chat_with_llm(
            user_message,
            conversation_history=history,
            security_hint=security_hint,
            persona_name=persona.name,
            persona_fragment=persona.system_prompt_fragment,
        )
    except Exception as e:
        logger.error("LLM call failed: %s", str(e))
        raise HTTPException(
            status_code=503,
            detail="AI 服務暫時無法使用，請稍後再試。",
        )

    # ── Layer 3: Output Gateway ──────────────────────────────────────────────
    output_result = check_output(llm_reply)

    final_reply = output_result.response

    if output_result.status == OutputStatus.INTERCEPTED:
        logger.warning(
            "L3 INTERCEPTED | rule=%s | snippet=%.80s",
            output_result.triggered_rule,
            llm_reply[:80],
        )

    # ── Memory 寫入 ──────────────────────────────────────────────────────────
    if session_id:
        await conversation_store.append(session_id, "user", user_message, persona_id=persona.id)
        await conversation_store.append(session_id, "assistant", final_reply, persona_id=persona.id)

    intercepted = output_result.status == OutputStatus.INTERCEPTED
    return ChatResponse(reply=final_reply, intercepted=intercepted, session_id=session_id)


@router.post("/reset", response_model=ResetResponse)
async def reset(request: ResetRequest):
    await conversation_store.reset(request.session_id.strip())
    return ResetResponse(status="cleared")
