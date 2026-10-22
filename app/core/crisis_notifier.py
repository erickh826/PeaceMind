"""
危機通知（Phase 6，tier 2 / tier 3）

通知內容（主旨與內文）一律不含學生原文，只有 tier、時間和事件 id；治療師帶 admin key 到後台看詳情。
目前通道是 SMTP email，介面是 send_crisis_notification()；之後要換成 webhook 只需要換這個函式。

環境變數（SMTP_PASSWORD 只放在 Vercel 與本機 .env，不進 git）：
  CRISIS_NOTIFY_EMAIL_TO  收件人，可用逗號分隔多個
  SMTP_HOST / SMTP_PORT   Vercel 對外不能用 25；587 走 STARTTLS，465 走 SSL
  SMTP_USER / SMTP_PASSWORD / SMTP_FROM（SMTP_FROM 沒設時用 SMTP_USER）

沒設定就只寫 log，回傳 False（= 沒有人會即時收到通知）。回傳 True 只代表 SMTP 伺服器接受了
信件，不代表有人讀到；呼叫端不能因此對學生承諾即時處理。
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 5
SMTP_TOTAL_TIMEOUT_SECONDS = 8


def _recipients() -> list[str]:
    raw = os.environ.get("CRISIS_NOTIFY_EMAIL_TO", "")
    return [a.strip() for a in raw.split(",") if a.strip()]


def is_configured() -> bool:
    return bool(_recipients() and os.environ.get("SMTP_HOST"))


def build_message(event_id: str, tier: int, created_at: datetime) -> EmailMessage:
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "peacemind@localhost"
    msg = EmailMessage()
    msg["Subject"] = f"[PeaceMind] 危機事件通知 tier {tier}"
    msg["From"] = sender
    msg["To"] = ", ".join(_recipients())
    msg.set_content(
        "PeaceMind 偵測到一則危機事件，請登入後台查看詳情。\n\n"
        f"風險層級：tier {tier}\n"
        f"時間：{created_at.isoformat()}\n"
        f"事件 id：{event_id}\n\n"
        "此通知不含學生對話內容。"
    )
    return msg


def _send_blocking(msg: EmailMessage) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")

    if port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECONDS)
    else:
        server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)
    try:
        if port != 465 and os.environ.get("SMTP_STARTTLS", "1") != "0":
            server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


async def send_crisis_notification(event_id: str, tier: int, created_at: datetime) -> bool:
    """送出通知；成功回傳 True。未設定或失敗回傳 False，絕不丟例外。"""
    if not is_configured():
        logger.warning(
            "CRISIS NOTIFY (not configured, log only) | event=%s tier=%d", event_id, tier
        )
        return False
    try:
        msg = build_message(event_id, tier, created_at)
        await asyncio.wait_for(asyncio.to_thread(_send_blocking, msg), timeout=SMTP_TOTAL_TIMEOUT_SECONDS)
        return True
    except Exception as e:  # noqa: BLE001 — 通知失敗不能影響熱線回覆
        logger.error("CRISIS NOTIFY failed | event=%s tier=%d | %s", event_id, tier, e)
        return False
