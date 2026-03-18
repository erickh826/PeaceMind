"""
第一層防禦 (Layer 1b)：Semantic Intent Gateway
使用 Azure AI Content Safety — Prompt Shields API
偵測語義繞過攻擊（同義詞、編碼混淆、跨語言注入）

設計原則：
- Fail-open：API 無法連線時不阻擋使用者，記錄警告並放行
  （PoC 優先服務可用性；生產環境可透過 SEMANTIC_GATEWAY_FAIL_OPEN=false 改為 fail-closed）
- 非同步：使用 httpx async client，不阻塞 FastAPI event loop
"""

import logging
import os
from dataclasses import dataclass
from enum import Enum

import httpx

logger = logging.getLogger(__name__)

# ── 環境變數 ────────────────────────────────────────────────────────────────
CONTENT_SAFETY_ENDPOINT = os.environ.get("AZURE_CONTENT_SAFETY_ENDPOINT", "")
CONTENT_SAFETY_KEY = os.environ.get("AZURE_CONTENT_SAFETY_KEY", "")
FAIL_OPEN = os.environ.get("SEMANTIC_GATEWAY_FAIL_OPEN", "true").lower() == "true"

# Prompt Shields API 版本
_API_VERSION = "2024-09-01"
_TIMEOUT_SEC = 5.0


class SemanticStatus(str, Enum):
    CLEAN = "clean"        # 無攻擊信號
    ATTACK = "attack"      # 偵測到注入攻擊
    SKIPPED = "skipped"    # API 未設定或失敗（fail-open）


@dataclass
class SemanticResult:
    status: SemanticStatus
    confidence: float = 0.0   # 0.0–1.0，目前 Prompt Shields 僅回傳 bool，預留欄位
    raw_response: dict | None = None


async def check_semantic(user_input: str) -> SemanticResult:
    """
    呼叫 Azure AI Content Safety Prompt Shields API。

    Args:
        user_input: 使用者原始輸入（已通過 L1a Regex）

    Returns:
        SemanticResult
            .status = CLEAN    → 通過，繼續流程
            .status = ATTACK   → 偵測到語意注入，應阻擋
            .status = SKIPPED  → API 未設定 / 超時，依 FAIL_OPEN 決策
    """
    # 未設定環境變數 → 靜默跳過（開發環境友好）
    if not CONTENT_SAFETY_ENDPOINT or not CONTENT_SAFETY_KEY:
        logger.debug("SemanticGateway: env vars not set, skipping (fail-open)")
        return SemanticResult(status=SemanticStatus.SKIPPED)

    url = (
        f"{CONTENT_SAFETY_ENDPOINT.rstrip('/')}"
        f"/contentsafety/text:shieldPrompt?api-version={_API_VERSION}"
    )
    payload = {
        "userPrompt": user_input,
        "documents": [],  # 無 RAG 文件，只偵測 user prompt
    }
    headers = {
        "Ocp-Apim-Subscription-Key": CONTENT_SAFETY_KEY,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        user_analysis = data.get("userPromptAnalysis", {})
        attack_detected: bool = user_analysis.get("attackDetected", False)

        if attack_detected:
            logger.warning(
                "SemanticGateway: attack detected | snippet=%.60s",
                user_input,
            )
            return SemanticResult(
                status=SemanticStatus.ATTACK,
                confidence=1.0,
                raw_response=data,
            )

        return SemanticResult(
            status=SemanticStatus.CLEAN,
            raw_response=data,
        )

    except httpx.TimeoutException:
        logger.warning("SemanticGateway: timeout after %.1fs", _TIMEOUT_SEC)
    except httpx.HTTPStatusError as e:
        logger.warning("SemanticGateway: HTTP %d — %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("SemanticGateway: unexpected error — %s", str(e))

    # 到達這裡 = 發生異常
    if FAIL_OPEN:
        logger.warning("SemanticGateway: fail-open, allowing request through")
        return SemanticResult(status=SemanticStatus.SKIPPED)
    else:
        # fail-closed：API 失敗時視為危險，阻擋
        logger.warning("SemanticGateway: fail-closed, blocking request")
        return SemanticResult(status=SemanticStatus.ATTACK)
