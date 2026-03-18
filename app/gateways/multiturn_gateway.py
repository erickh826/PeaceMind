"""
第一層防禦 (Layer 1c)：Multi-turn Risk Scorer
防禦「溫水煮青蛙」式多輪對話注入（Crescendo / Deceptive Delight 攻擊）

設計：
- Sliding Window：掃描最近 WINDOW_SIZE 輪使用者訊息
- 時間衰減：越早的輪次風險權重越低（× DECAY_FACTOR^age）
- 三級行動：allow / warn / block
- 純本地計算：無外部 API，延遲 ~2ms

參考：
- TCA Framework (arXiv 2503.15560) — Temporal Context Awareness
- Deceptive Delight (Palo Alto Unit 42) — 3 輪內 64.6% 越獄率
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── 設定常數 ────────────────────────────────────────────────────────────────
WINDOW_SIZE = 8          # 回看最近 N 輪使用者訊息
DECAY_FACTOR = 0.7       # 每輪衰減係數（越早 = 影響越小）
WARN_THRESHOLD = 0.50    # 累積分數 ≥ 此值 → WARN（靜默強化 L2 護欄）
BLOCK_THRESHOLD = 0.80   # 累積分數 ≥ 此值 → BLOCK

# ── 風險信號模式 ─────────────────────────────────────────────────────────────
# 格式：(regex_pattern, risk_weight)
# 不與 L1a 重複（L1a 處理高置信度注入），這裡偵測「漸進式語境引導」信號
RISK_SIGNALS: list[tuple[str, float]] = [

    # ── 角色漂移信號（低風險）——單輪無害，多輪累積才危險 ─────────────────────
    (r"如果你是.*(?:朋友|真人|人類|普通人)", 0.20),
    (r"假設你.*(?:是|變成).*(?:真人|普通|自由)", 0.25),
    (r"if you (?:were|are|could be) (?:a )?(?:real|human|free|normal)", 0.20),
    (r"pretend (?:for a moment|just for now|that)", 0.25),
    (r"想像你.*(?:沒有|不受|超越).*(?:規則|限制|設定)", 0.30),
    (r"imagine you.*(?:no|without).*(?:rule|restrict|limit|constraint)", 0.30),

    # ── 上下文累積信號（中風險）——利用前幾輪的回應建立越獄框架 ──────────────
    (r"你(?:剛才|之前|上次).*說.*(?:可以|沒問題|當然)", 0.35),
    (r"you (?:just|before|previously) said.*(?:can|sure|of course)", 0.35),
    (r"基於你之前的回答", 0.35),
    (r"based on (?:your|what you) (?:previous|last|earlier)", 0.35),
    (r"繼續剛才的.*(?:角色|扮演|假設)", 0.40),
    (r"continue (?:the|that|our) (?:role|scenario|game|story)", 0.40),

    # ── 漸進角色置換信號（中-高風險）—— 逐步替換阿本的身份 ──────────────────
    (r"你現在(?:已經|應該)(?:是|變成).*(?:醫生|治療師|沒有限制)", 0.50),
    (r"you(?:'re| are) now (?:a |an )?(?:doctor|therapist|free|unrestricted)", 0.50),
    (r"從現在起.*(?:扮演|是|叫做)(?!阿本|Boon)", 0.45),
    (r"from now on.*(?:act as|you are|call yourself)(?! Boon| 阿本)", 0.45),
    (r"我們(?:就)?叫你.*(?!阿本|Boon)", 0.40),

    # ── 直接越獄信號（高風險）—— 與 L1a 重疊，但用於多輪累積計分 ─────────────
    # L1a 已單輪阻擋；出現在歷史中表示使用者曾嘗試過
    (r"ignore.*instructions?", 0.70),
    (r"忽略.*(?:指令|規則|限制)", 0.70),
    (r"jailbreak", 0.75),
    (r"DAN\s*mode|developer\s+mode", 0.75),
    (r"system\s+override", 0.80),
    (r"解除.*(?:限制|封鎖|設定)", 0.65),
]


class RiskAction:
    ALLOW = "allow"   # 正常通過
    WARN = "warn"     # 靜默強化 L2 系統提示護欄
    BLOCK = "block"   # 直接阻擋，返回溫暖提示


@dataclass
class MultiTurnResult:
    action: str                      # RiskAction.*
    cumulative_score: float          # 最終累積分數 (0.0–1.0)
    per_turn_scores: list[float] = field(default_factory=list)
    triggered_patterns: list[str] = field(default_factory=list)


def _score_message(text: str) -> tuple[float, list[str]]:
    """
    對單條訊息計算風險分數，回傳 (max_score, 觸發的 patterns)。
    取所有匹配中的最高分（不累加），避免單輪過度放大。
    """
    max_score = 0.0
    triggered = []
    for pattern, weight in RISK_SIGNALS:
        if re.search(pattern, text, re.IGNORECASE):
            triggered.append(pattern)
            if weight > max_score:
                max_score = weight
    return max_score, triggered


def evaluate_multiturn_risk(
    history: list[dict],
    current_message: str,
) -> MultiTurnResult:
    """
    對滑動窗口內的使用者訊息計算時序累積風險。

    Args:
        history: 對話歷史，格式 [{"role": "user"|"assistant", "content": "..."}]
                 只有 role="user" 的訊息會被評分
        current_message: 當前使用者輸入（已通過 L1a + L1b）

    Returns:
        MultiTurnResult
            .action = "allow"  → 繼續正常流程
            .action = "warn"   → 在 LLM system prompt 注入安全提示
            .action = "block"  → 直接返回阻擋訊息
    """
    # 提取最近 WINDOW_SIZE 輪使用者訊息（不含 assistant）
    user_msgs = [
        t["content"] for t in history
        if t.get("role") == "user"
    ][-WINDOW_SIZE:]

    # 加入當前訊息（放在最後 = 最新 = 衰減最少）
    all_msgs = user_msgs + [current_message]
    total = len(all_msgs)

    cumulative = 0.0
    per_turn: list[float] = []
    all_triggered: list[str] = []

    for i, msg in enumerate(all_msgs):
        score, triggered = _score_message(msg)
        # age=0 = 最新訊息（不衰減），age=k = k 輪前（衰減 DECAY_FACTOR^k）
        age = total - 1 - i
        decayed = score * (DECAY_FACTOR ** age)
        cumulative += decayed
        per_turn.append(round(decayed, 4))
        all_triggered.extend(triggered)

    # 正規化到 [0, 1]
    normalized = min(round(cumulative, 4), 1.0)

    if normalized >= BLOCK_THRESHOLD:
        action = RiskAction.BLOCK
        logger.warning(
            "MultiTurnGateway: BLOCK | score=%.3f | turns=%d | patterns=%s",
            normalized, total, all_triggered[:3]
        )
    elif normalized >= WARN_THRESHOLD:
        action = RiskAction.WARN
        logger.warning(
            "MultiTurnGateway: WARN | score=%.3f | turns=%d",
            normalized, total
        )
    else:
        action = RiskAction.ALLOW

    return MultiTurnResult(
        action=action,
        cumulative_score=normalized,
        per_turn_scores=per_turn,
        triggered_patterns=list(set(all_triggered)),
    )
