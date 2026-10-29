"""ZeasAds：只回写用户标签，不做策略 CRUD。lookup 失败不挡注册。"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any, Iterable, List, Optional, Sequence

from services.recommend_tags import normalize_tag_list, upsert_ua_tag_def
from services.recommend import bump_recommend_cache

logger = logging.getLogger(__name__)

ZEASADS_BASE_URL = os.getenv("ZEASADS_BASE_URL", "").strip().rstrip("/")
ZEASADS_PRODUCT_ID = os.getenv("ZEASADS_PRODUCT_ID", "").strip()
ZEASADS_HMAC_SECRET = os.getenv("ZEASADS_HMAC_SECRET", "").strip()
ZEASADS_LOOKUP_TIMEOUT = float(os.getenv("ZEASADS_LOOKUP_TIMEOUT", "2.5"))


def _hmac_hex(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_audience_hmac(raw_body: bytes, signature: Optional[str]) -> bool:
    secret = ZEASADS_HMAC_SECRET
    if not secret:
        logger.warning("ZEASADS_HMAC_SECRET 未配置，拒绝 audience_sync")
        return False
    sig = (signature or "").strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1].strip()
    if not sig:
        return False
    expected = _hmac_hex(raw_body, secret)
    return hmac.compare_digest(expected, sig)


def ua_tags_from_payload(payload: dict) -> List[str]:
    tags = normalize_tag_list(payload.get("ua_tags") or payload.get("tags"))
    channel = str(payload.get("channel") or "").strip()
    campaign = str(payload.get("campaign") or "").strip()
    adgroup = str(
        payload.get("adgroup")
        or payload.get("ad_group")
        or payload.get("ad_set_id")
        or payload.get("adgroup_id")
        or ""
    ).strip()
    audience = str(payload.get("audience") or "").strip()

    def _ok(v: str) -> bool:
        return bool(v) and v.lower() not in ("(not set)", "not set", "none", "null", "undefined", "(none)")

    if _ok(channel):
        tags.append(f"ua:channel:{channel}")
    if _ok(campaign):
        tags.append(f"ua:campaign:{campaign}")
    if _ok(adgroup):
        tags.append(f"ua:adgroup:{adgroup}")
    if _ok(audience):
        key = audience if audience.startswith("audience:") else f"audience:{audience}"
        tags.append(key)
    try:
        from services.ad_attribution import enrich_paid_ad_tags

        return enrich_paid_ad_tags(tags)
    except Exception:
        return normalize_tag_list(tags)


def merge_ua_tags(existing: Optional[Iterable[str]], incoming: Sequence[str]) -> List[str]:
    out = normalize_tag_list(existing)
    for t in normalize_tag_list(incoming):
        if t not in out:
            out.append(t)
    return out


def apply_ua_tags_to_user(db, user, incoming: Sequence[str]) -> List[str]:
    from sqlalchemy.orm.attributes import flag_modified

    merged = merge_ua_tags(getattr(user, "ua_tags", None), incoming)
    user.ua_tags = merged
    flag_modified(user, "ua_tags")
    for t in merged:
        upsert_ua_tag_def(db, t)
    bump_recommend_cache()
    return merged


def save_ua_pending(db, product_user_id: str, incoming: Sequence[str]) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from core.database import RecommendUaPendingORM

    pid = str(product_user_id or "").strip()
    if not pid:
        return
    tags = normalize_tag_list(incoming)
    row = db.query(RecommendUaPendingORM).filter(RecommendUaPendingORM.product_user_id == pid).first()
    if row:
        row.ua_tags = merge_ua_tags(row.ua_tags, tags)
        flag_modified(row, "ua_tags")
    else:
        db.add(RecommendUaPendingORM(product_user_id=pid, ua_tags=tags))
    for t in tags:
        upsert_ua_tag_def(db, t)


def merge_pending_for_user(db, user_id: int) -> None:
    from core.database import RecommendUaPendingORM, UserORM

    user = db.query(UserORM).filter(UserORM.id == user_id).first()
    if not user:
        return
    row = (
        db.query(RecommendUaPendingORM)
        .filter(RecommendUaPendingORM.product_user_id == str(user_id))
        .first()
    )
    if not row:
        return
    apply_ua_tags_to_user(db, user, row.ua_tags or [])
    db.delete(row)


def lookup_attribution(user_id: int, event: str = "activation") -> List[str]:
    """GET /v1/attribution/lookup；失败返回 []，不抛。"""
    if not ZEASADS_BASE_URL or not ZEASADS_PRODUCT_ID:
        return []
    try:
        import urllib.parse
        import urllib.request

        qs = urllib.parse.urlencode({
            "product_id": ZEASADS_PRODUCT_ID,
            "customer_user_id": str(user_id),
            "event": event,
        })
        url = f"{ZEASADS_BASE_URL}/v1/attribution/lookup?{qs}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=ZEASADS_LOOKUP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8") or "{}"
        data = json.loads(raw)
        if not isinstance(data, dict):
            return []
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        return ua_tags_from_payload(payload or {})
    except Exception as e:
        logger.warning("ZeasAds lookup failed user_id=%s: %s", user_id, e)
        return []


def after_user_auth(user_id: int, is_new: bool = False) -> None:
    """注册/登录后 lookup + 合并 pending。失败不挡主流程。"""
    try:
        from core.database import UserORM, get_db

        with get_db() as db:
            merge_pending_for_user(db, user_id)
            tags = lookup_attribution(user_id, event="registration" if is_new else "activation")
            user = db.query(UserORM).filter(UserORM.id == user_id).first()
            if user:
                if tags:
                    apply_ua_tags_to_user(db, user, tags)
                try:
                    from services.ad_attribution import merge_attribution_tags_for_user

                    merge_attribution_tags_for_user(db, user_id)
                except Exception:
                    logger.warning("merge attribution tags failed user_id=%s", user_id, exc_info=True)
    except Exception:
        logger.warning("after_user_auth recommend ua failed user_id=%s", user_id, exc_info=True)
