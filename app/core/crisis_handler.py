"""
危機介入模組 (Crisis Intervention)
偵測到自傷、自殺或嚴重暴力傾向時，強制觸發安全機制
提供香港緊急求助熱線，同時保持溫暖與同理
"""

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


def get_crisis_response(user_input: str) -> str:
    """
    根據用戶輸入語言返回對應的危機介入回覆。
    簡單判斷：若包含中文字符則用繁體中文，否則用英文。
    """
    has_cjk = any('\u4e00' <= char <= '\u9fff' for char in user_input)
    return CRISIS_RESPONSE if has_cjk else CRISIS_RESPONSE_EN
