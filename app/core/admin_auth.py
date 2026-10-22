"""
最低限度的 Admin 寫入授權（Phase 4）

在 Phase 8 的真實帳號與角色權限之前，所有 admin「寫入」端點都要求 X-Admin-Key
header 等於環境變數 ADMIN_API_KEY。範圍不只規則：規則會引用 persona 與範例，
只保護規則端點的話，未授權者仍可修改被規則引用的範例內容。讀取端點維持現狀。

ADMIN_API_KEY 未設定時一律拒絕（fail closed），不會因為忘記設定就變成任何人可寫。
比對用 hmac.compare_digest，避免依字元逐一比對的時間差洩漏 key。
"""
from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException


def require_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ADMIN_API_KEY")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_API_KEY 未設定，admin 寫入端點已停用。",
        )
    if not x_admin_key or not hmac.compare_digest(x_admin_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="X-Admin-Key 缺少或不正確")
