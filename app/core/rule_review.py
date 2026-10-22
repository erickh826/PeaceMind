"""
規則審核的共用邏輯（Phase 5，T-Q6–T-Q8）

審核模式（RULE_REVIEW_MODE）：
  dual   — 預設。draft → active 與修改 active 規則都要走審核，reviewer ≠ submitter 且
           角色是 senior_therapist / admin；送審前必須測過被提議的內容。
  single — Phase 4 的行為：可直接 PATCH draft → active；審核端點仍可用，但 reviewer 可以
           是 submitter、不限角色、也不強制測過。
任何不認得的值（包含大小寫打錯）一律當成 dual，寧可嚴格也不要因為打錯字放寬。

身份：目前只有共用的 admin key，submitted_by / reviewer_id 由呼叫端在 body 自報，
系統只能驗證「這個治療師存在、角色符合、reviewer ≠ submitter」。這只能擋誤操作，
擋不了冒名——同一把 key 的持有者可以自報任何身份。真正的保證要等 Phase 8 的帳號系統。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

logger = logging.getLogger(__name__)

REVIEWER_ROLES = ("senior_therapist", "admin")

# 被審核與被測試的「內容」欄位；status 不在內，因為它由審核流程決定
CONTENT_FIELDS = ("name", "conditions_json", "action_json", "priority", "scope")


def get_review_mode() -> str:
    raw = os.environ.get("RULE_REVIEW_MODE", "dual")
    if raw == "single":
        return "single"
    if raw != "dual":
        logger.warning("RULE_REVIEW_MODE=%r 不認得，當成 dual 處理", raw)
    return "dual"


def content_of(snapshot: dict) -> dict:
    return {key: snapshot[key] for key in CONTENT_FIELDS}


def content_hash(content: dict) -> str:
    normalized = json.dumps(
        {key: content[key] for key in CONTENT_FIELDS}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
