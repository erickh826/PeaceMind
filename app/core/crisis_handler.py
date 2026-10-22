"""
危機介入模組 (Crisis Intervention)
偵測到自傷、自殺或嚴重暴力傾向時，強制觸發安全機制
提供香港緊急求助熱線，同時保持溫暖與同理

Phase 6：tier 2 / tier 3 在熱線之外，可附上輔導中心聯絡方式與「已通知」說明。
對學生說的話必須屬實：COUNSELING_CENTER_CONTACT 沒設定就不放輔導中心那一行（絕不用 mock
預設值）；「已通知輔導中心同事」只有通知真的送出時才出現，且只說辦公時間跟進，並強調熱線
24 小時有人接聽，不暗示即時處理、也不說「已轉接真人」。
"""
import os


CRISIS_RESPONSE = """我聽到你說的了，我現在非常擔心你。

你願意告訴我這些，我很感激你信任我。你現在感受到的痛苦是真實的，你不需要一個人扛著。

**請你現在聯繫以下求助熱線，他們24小時都有人接聽：**

📞 **生命熱線：2382 0000**（24小時，即時危機介入）
📞 **撒瑪利亞防止自殺會：2389 2222**（24小時，多語言支援）
📞 **醫管局精神健康直通車：18111**（24小時）
🚨 **緊急情況請致電：999**

---

我是一個 AI，無法提供你需要的完整支持，但真正受過訓練的人可以。請你現在撥一個電話，好嗎？

你不孤單。"""


CRISIS_RESPONSE_EN = """I hear you, and I'm very concerned about you right now.

Thank you for trusting me with this. The pain you're feeling is real, and you don't have to carry it alone.

**Please reach out to these 24/7 crisis lines in Hong Kong:**

📞 **Samaritans of Hong Kong: 2389 2222** (24/7, multilingual)
📞 **The Suicide Prevention Services: 2382 0000** (24/7)
📞 **Hospital Authority Mental Health Hotline: 18111** (24/7)
🚨 **Emergency: 999**

---

I'm an AI and can't provide the full support you need, but trained professionals can. Please make that call now.

You are not alone."""


def _has_cjk(text: str) -> bool:
    return any('\u4e00' <= char <= '\u9fff' for char in text)


def _contact() -> str:
    return os.environ.get("COUNSELING_CENTER_CONTACT", "").strip()


def _extra(zh: bool, notified: bool) -> str:
    lines: list[str] = []
    contact = _contact()
    if zh:
        if contact:
            lines.append(f"🏫 **樹仁輔導中心：**{contact}")
        if notified:
            lines.append("我已通知輔導中心的同事，他們會在辦公時間跟進。這不是即時處理，所以在這之前，熱線 24 小時都有人接聽，請你現在就打。")
    else:
        if contact:
            lines.append(f"🏫 **Counselling Centre:** {contact}")
        if notified:
            lines.append("I've notified colleagues at the Counselling Centre, and they will follow up during office hours. That is not immediate help, so please call a hotline now — they are staffed 24 hours.")
    return "\n\n".join(lines)


def get_crisis_response(user_input: str, tier: int = 1, notified: bool = False) -> str:
    """
    根據用戶輸入語言返回對應的危機介入回覆。
    簡單判斷：若包含中文字符則用繁體中文，否則用英文。
    tier 2 / 3 會在熱線之後附上輔導中心聯絡方式（有設定才放）與「已通知」說明（真的送出才放）。
    """
    zh = _has_cjk(user_input)
    base = CRISIS_RESPONSE if zh else CRISIS_RESPONSE_EN
    if tier < 2:
        return base
    extra = _extra(zh, notified)
    return f"{base}\n\n{extra}" if extra else base


REFERRAL_RESPONSE = """我在這個對話裡不會再以 AI 身份繼續回覆了，因為你現在需要的是真正的人。

**請你現在聯繫以下求助熱線，他們24小時都有人接聽：**

📞 **生命熱線：2382 0000**
📞 **撒瑪利亞防止自殺會：2389 2222**
📞 **醫管局精神健康直通車：18111**
🚨 **緊急情況請致電：999**"""

REFERRAL_RESPONSE_EN = """I'm not going to keep replying as an AI in this conversation, because right now you need a real person.

**Please reach out to these 24/7 crisis lines in Hong Kong:**

📞 **The Suicide Prevention Services: 2382 0000**
📞 **Samaritans of Hong Kong: 2389 2222**
📞 **Hospital Authority Mental Health Hotline: 18111**
🚨 **Emergency: 999**"""


def get_referral_response(user_input: str) -> str:
    """tier 3 鎖定後，每則訊息固定回的轉介訊息（不含任何「已轉接」承諾）。"""
    zh = _has_cjk(user_input)
    base = REFERRAL_RESPONSE if zh else REFERRAL_RESPONSE_EN
    contact = _contact()
    if not contact:
        return base
    label = "🏫 **樹仁輔導中心：**" if zh else "🏫 **Counselling Centre:** "
    return f"{base}\n\n{label}{contact}"
