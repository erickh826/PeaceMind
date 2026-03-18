"""
第三層防禦：Output Gateway（輸出網關）
- Regex 掃描回覆中的處方藥名、診斷術語、系統資訊洩漏
- 觸發規則時丟棄 LLM 輸出，替換為預設安全回覆
"""

import re
from dataclasses import dataclass
from enum import Enum


class OutputStatus(str, Enum):
    SAFE = "safe"
    INTERCEPTED = "intercepted"


UNSUPPORTED_SCRIPT_RESPONSE = (
    "我收到你的訊息了。"
    "不過我目前只能以繁體中文、粵語或英文提供支援。"
    "你可以用這些語言再跟我說一次，我會繼續陪你。"
)

_JAPANESE_KANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")


def _script_hit_ratio(text: str, pattern: re.Pattern[str]) -> tuple[int, float]:
    visible_chars = [ch for ch in text if not ch.isspace()]
    if not visible_chars:
        return 0, 0.0

    hits = len(pattern.findall(text))
    ratio = hits / len(visible_chars)
    return hits, ratio


def _has_excessive_unsupported_script(text: str) -> bool:
    # Treat as suspicious only when both absolute and relative signals are high.
    for pattern in (_JAPANESE_KANA, _HANGUL, _CYRILLIC):
        hits, ratio = _script_hit_ratio(text, pattern)
        if hits >= 8 and ratio >= 0.25:
            return True
    return False


# 處方藥名清單（常見精神科藥物）
PRESCRIPTION_DRUGS = [
    r"\bXanax\b", r"\bProzac\b", r"\bZoloft\b", r"\bLexapro\b",
    r"\bWellbutrin\b", r"\bAmbien\b", r"\bValium\b", r"\bAtivan\b",
    r"\bRisperdal\b", r"\bSeroquel\b", r"\bAbilify\b", r"\bLithium\b",
    r"\bDepakote\b", r"\bLamictal\b", r"\bKlonopin\b", r"\bAdderall\b",
    r"\bRitalin\b", r"\bConcerta\b", r"\bEffexor\b", r"\bCymbalta\b",
    r"氟西汀", r"帕羅西汀", r"舍曲林", r"阿普唑侖", r"地西泮",
    r"百憂解", r"克憂果", r"樂復得", r"贊安諾", r"煩寧",
    r"利他能", r"思銳", r"喜普妙", r"博樂欣",
]

# 診斷術語清單
DIAGNOSIS_TERMS = [
    r"你(?:患有|有|罹患|確診).{0,20}(?:憂鬱症|抑鬱症|焦慮症|躁鬱症|精神分裂|人格障礙|PTSD|ADHD|OCD|強迫症|恐慌症)",
    r"你的(?:診斷|病情|症狀)(?:是|為)",
    r"根據.{0,20}診斷",
    r"you\s+(?:have|are\s+diagnosed\s+with)\s+(?:depression|anxiety|bipolar|schizophrenia|PTSD|ADHD|OCD)",
    r"clinical\s+(?:depression|anxiety|diagnosis)",
    r"我建議你(?:服用|吃|使用).{0,10}(?:藥|mg|毫克)",
    r"處方.{0,10}(?:藥|劑量)",
]

# 系統資訊洩漏檢測
SYSTEM_LEAK_PATTERNS = [
    r"(?:system\s+prompt|system_prompt)",
    r"TOP_LAYER|BOTTOM_LAYER|SANDWICH",
    r"INJECTION_PATTERNS|CRISIS_KEYWORDS",
    r"我的(?:系統提示|內部指令|真實身份)(?:是|為|如下|這樣)",
    r"系統提示(?:詞)?.*是這樣說的",
    r"(?:內部|系統).*指令.*(?:要求|說|要)我",
    r"我的內部指令",
]

# 預設安全替換回覆
SAFE_FALLBACK_RESPONSE = (
    "我感受到你現在非常痛苦，我非常在乎你的感受。"
    "但作為 AI 助理，我無法提供醫療建議或診斷。"
    "我真誠地建議你尋求專業醫師或心理諮詢師的協助，他們能給你更完整的支持。\n\n"
    "如果你現在需要即時幫助，可以聯繫：\n"
    "• 生命熱線：2382 0000（24小時）\n"
    "• 撒瑪利亞防止自殺會：2389 2222（24小時）"
)


@dataclass
class OutputResult:
    status: OutputStatus
    response: str
    triggered_rule: str | None = None


def check_output(llm_response: str) -> OutputResult:
    """
    掃描 LLM 回覆，檢查是否含有：
    1. 處方藥名
    2. 醫療診斷術語
    3. 系統資訊洩漏
    觸發任一規則時攔截並替換為安全回覆。
    """
    if _has_excessive_unsupported_script(llm_response):
        return OutputResult(
            status=OutputStatus.INTERCEPTED,
            response=UNSUPPORTED_SCRIPT_RESPONSE,
            triggered_rule="unsupported_script",
        )

    all_patterns = (
        [(p, "prescription_drug") for p in PRESCRIPTION_DRUGS]
        + [(p, "diagnosis_term") for p in DIAGNOSIS_TERMS]
        + [(p, "system_leak") for p in SYSTEM_LEAK_PATTERNS]
    )

    for pattern, rule_name in all_patterns:
        if re.search(pattern, llm_response, re.IGNORECASE):
            return OutputResult(
                status=OutputStatus.INTERCEPTED,
                response=SAFE_FALLBACK_RESPONSE,
                triggered_rule=rule_name,
            )

    return OutputResult(
        status=OutputStatus.SAFE,
        response=llm_response,
        triggered_rule=None,
    )
