"""ZeasAds audience_sync：HMAC 校验后回写 users.ua_tags 或 pending。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from core.database import UserORM, get_db
from core.response import fail, ok
from services.recommend_ua import (
    apply_ua_tags_to_user,
    save_ua_pending,
    ua_tags_from_payload,
    verify_audience_hmac,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["推荐归因"])


@router.post("/api/recommend/audience-sync", summary="ZeasAds 人群包回写")
async def audience_sync(
    request: Request,
    x_zeasads_signature: str | None = Header(None, alias="X-ZeasAds-Signature"),
    x_signature: str | None = Header(None, alias="X-Signature"),
):
    raw = await request.body()
    sig = x_zeasads_signature or x_signature
    if not verify_audience_hmac(raw, sig):
        return JSONResponse(status_code=401, content={"ok": False, "message": "invalid signature"})
    try:
        import json
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return fail(message="invalid json", code=400)
    if not isinstance(payload, dict):
        return fail(message="invalid json", code=400)

    tags = ua_tags_from_payload(payload)
    customer_user_id = payload.get("customer_user_id") or payload.get("user_id")
    product_user_id = str(payload.get("product_user_id") or customer_user_id or "").strip()

    uid = None
    try:
        if customer_user_id is not None and str(customer_user_id).strip():
            uid = int(customer_user_id)
    except (TypeError, ValueError):
        uid = None

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == uid).first() if uid else None
        if user:
            apply_ua_tags_to_user(db, user, tags)
            return ok(data={"applied": True, "user_id": user.id, "ua_tags": user.ua_tags})
        if product_user_id:
            save_ua_pending(db, product_user_id, tags)
            return ok(data={"applied": False, "pending": product_user_id, "ua_tags": tags})
    return fail(message="missing user id", code=400)
