"""
第一層防禦：Input Gateway（輸入網關）
- 長度限制：1500 字
- 關鍵字黑名單過濾（Prompt Injection 防禦）
- 危機關鍵字偵測（Crisis Detection）
"""

import re
from dataclasses import dataclass
from enum import Enum


class InputStatus(str, Enum):
    OK = "ok"
    BLOCKED = "blocked"
    CRISIS = "crisis"


# Prompt Injection / 越權指令黑名單
INJECTION_PATTERNS = [
    # ── 英文注入：指令覆寫 ────────────────────────────────────
    r"ignore\s+(?:\w+\s+){0,2}instructions?",
    r"system\s+override",
    r"forget\s+(everything|all|prior|your)\s*(instructions?|rules?|guidelines?)?",
    r"disregard\s+(your|all|previous|prior)\s*(instructions?|rules?|guidelines?)?",
    r"act\s+as\s+(if\s+you\s+(are|were|have\s+no)|a\s+(?:doctor|therapist|psychiatrist|AI\s+without))",
    r"act\s+as\s+if\s+you\s+have\s+no",
    r"pretend\s+(you\s+are|to\s+be|you\s+have\s+no)",
    r"pretend\s+(you\s+are|to\s+be)",
    r"jailbreak",
    r"DAN\s*mode",
    r"developer\s+mode",
    r"prompt\s+injection",
    # ── 英文注入：系統資訊探測 ───────────────────────────────
    r"reveal\s+(your\s+)?(system\s+)?prompt",
    r"show\s+(me\s+)?(your\s+)?(system\s+)?instructions?",
    r"what\s+are\s+your\s+(system\s+)?instructions?",
    r"what\s+(are\s+)?your\s+(rules|guidelines|system\s+prompt)",
    r"tell\s+me\s+(your\s+)?(system\s+)?(prompt|instructions?|rules?)",
    # ── 中文注入：指令覆寫 ────────────────────────────────────
    r"忽略.*(?:指令|規則|限制|系統)",
    r"忘記.*(?:指令|規則|限制|系統)",
    r"解除.*(?:限制|封鎖|禁令)",
    r"扮演.*(?:醫生|醫師|精神科)",
    r"假裝.*(?:人類|沒有限制|沒有任何限制|不受限制)",
    r"沒有.*(?:限制|任何限制|規則).*(?:AI|助理|幫手)",
    # ── 中文注入：系統資訊探測 ───────────────────────────────
    r"(?:你的)?(?:真實|內部|隱藏)?系統提示(?:詞)?.*(?:是|為|什麼)",
    r"告訴我.*(?:內部|系統)?指令",
    r"你的.*(?:內部|系統).*指令",
]

# 危機關鍵字（需強制介入）
CRISIS_KEYWORDS = [
    r"(?:想|要|準備|計畫).{0,10}(?:死|自殺|結束生命|了結)",
    r"活(?:不下去|不了|夠了)",
    r"不想(?:活|存在|繼續)",
    r"(?:割腕|自傷|傷害自己)",
    r"(?:跳樓|跳橋|輕生)",
    r"(?:想|要)(?:死|尋死)",
    r"覺得.*(?:死了算了|死掉算了)",
    r"(?:kill|hurt|harm)\s+myself",
    r"suicid",
    r"self.?harm",
    r"end\s+(my\s+)?life",
    r"想消失",
    r"唔想活",
    r"唔想存在",
    r"唔想.*(?:活|存在|喺度)",
]

MAX_INPUT_LENGTH = 1500


@dataclass
class GatewayResult:
    status: InputStatus
    message: str
    original_input: str


def check_input(user_input: str) -> GatewayResult:
    """
    對用戶輸入進行三重檢查：
    1. 長度限制
    2. 危機關鍵字偵測（優先）
    3. Prompt Injection 過濾
    返回 GatewayResult 供後續處理
    """
    # 1. 長度限制
    if len(user_input) > MAX_INPUT_LENGTH:
        return GatewayResult(
            status=InputStatus.BLOCKED,
            message="你的訊息有點長，可以試試把想說的分成幾段來分享嗎？",
            original_input=user_input,
        )

    # 2. 危機關鍵字偵測（優先處理，不阻擋，由後端觸發危機介入）
    for pattern in CRISIS_KEYWORDS:
        if re.search(pattern, user_input, re.IGNORECASE):
            return GatewayResult(
                status=InputStatus.CRISIS,
                message="",  # 由 crisis_handler 填充回應
                original_input=user_input,
            )

    # 3. Prompt Injection 黑名單過濾
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, user_input, re.IGNORECASE):
            return GatewayResult(
                status=InputStatus.BLOCKED,
                message="我感受到你可能有些困惑或憤怒，但我只是一個心理支持助理，有什麼情緒上的事情想跟我說嗎？",
                original_input=user_input,
            )

    return GatewayResult(
        status=InputStatus.OK,
        message="",
        original_input=user_input,
    )
