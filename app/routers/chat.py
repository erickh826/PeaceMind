"""
Chat Router — 主要對話端點
整合三層防禦機制：
  Layer 1: Input Gateway
  Layer 2: LLM Core (with Sandwich Prompt)
  Layer 3: Output Gateway
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.gateways.input_gateway import check_input, InputStatus
from app.gateways.output_gateway import check_output, OutputStatus
from app.core.llm_client import chat_with_llm
from app.core.crisis_handler import get_crisis_response
from app.storage import conversation_store

logger = logging.getLogger(__name__)
router = APIRouter()


class Message(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=128)
    message: str = Field(..., min_length=1, max_length=1600)
    # Legacy compatibility only. Server-side memory is authoritative.
    history: list[Message] = Field(default_factory=list, max_length=20)


class ChatResponse(BaseModel):
    reply: str
    intercepted: bool = False
    crisis: bool = False


class ResetRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=128)


class ResetResponse(BaseModel):
    status: str


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    主要對話端點，執行完整三層防禦流程：

    1. Input Gateway → 長度/注入/危機偵測
    2. LLM Core → 三明治結構推理
    3. Output Gateway → 藥名/診斷/洩漏掃描
    """
    session_id = request.session_id.strip()
    user_message = request.message.strip()
    history = conversation_store.get_history(session_id)

    # ── Layer 1: Input Gateway ──────────────────────────────────
    input_result = check_input(user_message)

    if input_result.status == InputStatus.BLOCKED:
        logger.warning("Input blocked | length=%d", len(user_message))
        return ChatResponse(reply=input_result.message, intercepted=True)

    if input_result.status == InputStatus.CRISIS:
        logger.warning("CRISIS detected | input_snippet=%s", user_message[:50])
        crisis_reply = get_crisis_response(user_message)
        conversation_store.append(session_id, "user", user_message)
        conversation_store.append(session_id, "assistant", crisis_reply)
        return ChatResponse(reply=crisis_reply, crisis=True)

    # ── Layer 2: LLM Core ───────────────────────────────────────
    try:
        llm_reply = chat_with_llm(user_message, conversation_history=history)
    except Exception as e:
        logger.error("LLM call failed: %s", str(e))
        raise HTTPException(
            status_code=503,
            detail="AI 服務暫時無法使用，請稍後再試。",
        )

    # ── Layer 3: Output Gateway ─────────────────────────────────
    output_result = check_output(llm_reply)

    if output_result.status == OutputStatus.INTERCEPTED:
        logger.warning(
            "Output intercepted | rule=%s | snippet=%s",
            output_result.triggered_rule,
            llm_reply[:80],
        )
        conversation_store.append(session_id, "user", user_message)
        conversation_store.append(session_id, "assistant", output_result.response)
        return ChatResponse(reply=output_result.response, intercepted=True)

    conversation_store.append(session_id, "user", user_message)
    conversation_store.append(session_id, "assistant", output_result.response)
    return ChatResponse(reply=output_result.response)


@router.post("/reset", response_model=ResetResponse)
async def reset(request: ResetRequest):
    conversation_store.reset(request.session_id.strip())
    return ResetResponse(status="cleared")
