"""Apple / Google 验单响应解析（无网络）。便于单测，不碰商店 HTTP。"""

from __future__ import annotations

from typing import Any, Optional


def interpret_apple_verify_result(result: Optional[dict[str, Any]]) -> dict[str, Any]:
    """把 Apple verifyReceipt JSON 收成内部结构。status==0 才算 valid。"""
    if not result:
        return {"valid": False, "reason": "verify_request_failed"}

    status = result.get("status", -1)
    if status != 0:
        return {"valid": False, "reason": f"apple_status_{status}", "raw": result}

    latest_info = result.get("latest_receipt_info", [])
    pending_renewal = result.get("pending_renewal_info", [])
    latest_tx = latest_info[-1] if latest_info else {}

    auto_renew = False
    if pending_renewal:
        auto_renew = pending_renewal[0].get("auto_renew_status") == "1"

    return {
        "valid": True,
        "transaction_id": latest_tx.get("transaction_id", ""),
        "original_transaction_id": latest_tx.get("original_transaction_id", ""),
        "product_id": latest_tx.get("product_id", ""),
        "purchase_date_ms": latest_tx.get("purchase_date_ms", ""),
        "expires_date_ms": latest_tx.get("expires_date_ms", ""),
        "is_trial_period": latest_tx.get("is_trial_period") == "true",
        "auto_renew_status": auto_renew,
        "environment": result.get("environment", "Production"),
        "raw": result,
    }


def extract_google_obfuscated_account_id(data: Optional[dict[str, Any]]) -> str:
    """Play Billing setObfuscatedAccountId → Developer API / 验单 raw。RTDN 正文本身没有此字段。"""
    if not isinstance(data, dict):
        return ""
    candidates: list[Any] = [
        data.get("obfuscated_external_account_id"),
        data.get("obfuscatedExternalAccountId"),
        data.get("obfuscatedAccountId"),
    ]
    ext = data.get("externalAccountIdentifiers")
    if isinstance(ext, dict):
        candidates.append(ext.get("obfuscatedExternalAccountId"))
    raw = data.get("raw")
    if isinstance(raw, dict):
        candidates.extend(
            [
                raw.get("obfuscatedExternalAccountId"),
                raw.get("obfuscatedAccountId"),
            ]
        )
        raw_ext = raw.get("externalAccountIdentifiers")
        if isinstance(raw_ext, dict):
            candidates.append(raw_ext.get("obfuscatedExternalAccountId"))
    for v in candidates:
        s = str(v or "").strip()
        if s:
            return s
    return ""


def normalize_apple_uuid(token: Optional[str]) -> str:
    """trim + 小写；非法 UUID 返回空串。"""
    import uuid as uuid_lib

    s = str(token or "").strip().lower()
    if not s:
        return ""
    try:
        return str(uuid_lib.UUID(s)).lower()
    except (ValueError, TypeError, AttributeError):
        return ""


def parse_apple_account_user_id(token: Optional[str]) -> Optional[int]:
    """StoreKit appAccountToken 必须是 UUID。约定：UUID(int=user_id)，例如 73 → 00000000-0000-0000-0000-000000000049。"""
    s = str(token or "").strip()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        return n if n > 0 else None
    try:
        import uuid

        n = uuid.UUID(s).int
    except (ValueError, TypeError, AttributeError):
        return None
    if 0 < n < 10**12:
        return n
    return None


def apple_app_account_token_for_user_id(user_id: int) -> str:
    import uuid

    return str(uuid.UUID(int=int(user_id)))


def parse_google_account_user_id(account_id: Optional[str]) -> Optional[int]:
    """约定：收银台传入的 obfuscatedAccountId 为十进制 user_id 字符串。"""
    s = str(account_id or "").strip()
    if s.isdigit():
        n = int(s)
        if n > 0:
            return n
    return None


def interpret_google_purchase_payload(data: dict[str, Any], product_id: str) -> dict[str, Any]:
    """一次性商品：purchaseState 0=已购买, 1=已取消。"""
    purchase_state = data.get("purchaseState", -1)
    valid = purchase_state == 0
    return {
        "valid": valid,
        "order_id": data.get("orderId", ""),
        "purchase_state": purchase_state,
        "consumption_state": data.get("consumptionState", 0),
        "product_id": product_id,
        "purchase_time_ms": str(data.get("purchaseTimeMillis", "")),
        "expiry_time_millis": str(data.get("expiryTimeMillis", "")),
        "auto_renewing": data.get("autoRenewing", False),
        "obfuscated_external_account_id": extract_google_obfuscated_account_id(data),
        "raw": data,
    }


_ACTIVE_SUB_STATES = frozenset({
    "SUBSCRIPTION_STATE_ACTIVE",
    "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
    "SUBSCRIPTION_STATE_CANCELED",  # 已取消但未到期，权益仍有效
})


def _rfc3339_to_millis(value: str) -> str:
    if not value:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    try:
        from datetime import datetime, timezone

        text = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return str(int(dt.timestamp() * 1000))
    except Exception:
        return ""


def interpret_google_subscription_v2(data: dict[str, Any], product_id: str = "") -> dict[str, Any]:
    """purchases.subscriptionsv2.get：试用/优惠阶段以 lineItems.offerPhase 为准。"""
    if not data:
        return {"valid": False, "reason": "empty_subscription"}

    state = str(data.get("subscriptionState") or "")
    items = data.get("lineItems") or []
    item = items[0] if isinstance(items, list) and items else {}
    product = (item.get("productId") or product_id or "").strip()
    offer_phase = item.get("offerPhase") or {}
    offer_details = item.get("offerDetails") or {}
    auto_plan = item.get("autoRenewingPlan") or {}
    ack = str(data.get("acknowledgementState") or "")
    expiry_rfc = item.get("expiryTime") or ""
    start_rfc = data.get("startTime") or ""
    is_trial = bool(offer_phase.get("freeTrialPeriod") or offer_phase.get("freeTrial"))
    if not is_trial and isinstance(offer_phase, dict):
        is_trial = any("freeTrial" in str(k) for k in offer_phase.keys())

    valid = state in _ACTIVE_SUB_STATES
    reason = ""
    if state == "SUBSCRIPTION_STATE_PENDING":
        reason = "subscription_pending"
    elif not valid:
        reason = f"subscription_state_{state or 'unknown'}"

    return {
        "valid": valid,
        "reason": reason,
        "order_id": str(data.get("latestOrderId") or item.get("latestSuccessfulOrderId") or ""),
        "product_id": product,
        "purchase_time_ms": _rfc3339_to_millis(start_rfc),
        "expiry_time_millis": _rfc3339_to_millis(expiry_rfc),
        "expiry_time": expiry_rfc,
        "auto_renewing": bool(auto_plan.get("autoRenewEnabled")),
        "subscription_state": state,
        "acknowledgement_state": ack,
        "needs_ack": ack != "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
        "is_trial_period": is_trial,
        "offer_id": str(offer_details.get("offerId") or ""),
        "base_plan_id": str(offer_details.get("basePlanId") or ""),
        "is_test_purchase": data.get("testPurchase") is not None,
        "obfuscated_external_account_id": extract_google_obfuscated_account_id(data),
        "raw": data,
    }
