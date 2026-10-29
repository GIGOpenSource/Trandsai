"""
应用商店收据验证服务

职责：
- Apple App Store 收据验证 / Server Notifications V2
- Google Play 购买验证 / RTDN 通知
"""

import base64
import json
import logging
import os
import time
from typing import Optional

import requests

from urllib.parse import quote

from services.payment_receipt import (
    interpret_apple_verify_result,
    interpret_google_purchase_payload,
    interpret_google_subscription_v2,
)

logger = logging.getLogger(__name__)

# ===== Apple IAP =====

APPLE_IAP_SHARED_SECRET = os.getenv("APPLE_IAP_SHARED_SECRET", "")
APPLE_VERIFY_URL_PROD = "https://buy.itunes.apple.com/verifyReceipt"
APPLE_VERIFY_URL_SANDBOX = "https://sandbox.itunes.apple.com/verifyReceipt"


def verify_apple_receipt(receipt_data: str, shared_secret: str = "") -> dict:
    """
    验证 Apple App Store 收据。
    先用生产 URL 验证，如果返回 21007（沙箱收据）则自动切换到沙箱 URL 重试。

    返回:
        {
            "valid": bool,
            "transaction_id": str,       # 最新交易ID
            "original_transaction_id": str,  # 原始交易ID（续费时不变）
            "product_id": str,           # 产品ID
            "purchase_date_ms": str,     # 购买时间戳
            "expires_date_ms": str,      # 到期时间（订阅类）
            "is_trial_period": bool,     # 是否试用期
            "auto_renew_status": bool,   # 是否自动续费
            "environment": str,          # Production / Sandbox
            "raw": dict,                 # 原始响应
        }
    """
    if not str(receipt_data or "").strip():
        return {"valid": False, "reason": "empty_receipt"}

    secret = shared_secret or APPLE_IAP_SHARED_SECRET
    payload = {
        "receipt-data": receipt_data,
        "exclude-old-transactions": True,
    }
    if secret:
        payload["password"] = secret

    # 先用生产环境
    result = _do_apple_verify(payload, APPLE_VERIFY_URL_PROD)
    if result and result.get("status") == 21007:
        # 沙箱收据，切换到沙箱 URL
        logger.info("[payment_verify] Apple 收据为沙箱环境，切换验证地址")
        result = _do_apple_verify(payload, APPLE_VERIFY_URL_SANDBOX)

    return interpret_apple_verify_result(result)


def _do_apple_verify(payload: dict, url: str) -> Optional[dict]:
    """发送验证请求到 Apple"""
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("[payment_verify] Apple 验证请求失败 (%s): %s", url, e)
        return None


def parse_apple_notification_v2(signed_payload: str, skip_verification: bool = False) -> Optional[dict]:
    """
    解析 Apple App Store Server Notifications V2。
    通知是一个 JWS 签名的 payload，需要：
    1. 解析 JWS header 获取 x5c 证书链
    2. 验证证书链（信任 Apple 根证书）
    3. 验证签名
    4. 解码 payload

    参数:
        signed_payload: JWS 签名的 payload
        skip_verification: 是否跳过签名验证（仅用于测试环境）

    返回:
        {
            "notification_type": str,    # SUBSCRIBED / DID_RENEW / EXPIRED / DID_FAIL_TO_RENEW / REFUND 等
            "subtype": str,              # INITIAL_RESTART / RESURRECT 等
            "transaction_id": str,
            "original_transaction_id": str,
            "product_id": str,
            "expires_date": str,
            "purchase_date": str,
            "raw": dict,
        }
    """
    try:
        import jwt as pyjwt
        from cryptography.x509 import load_der_x509_certificate
        from cryptography.hazmat.backends import default_backend

        # JWS 格式: header.payload.signature
        parts = signed_payload.split(".")
        if len(parts) != 3:
            logger.error("[payment_verify] Apple 通知 JWS 格式错误")
            return None

        # 解析 header
        header_b64 = parts[0]
        header_json = base64.urlsafe_b64decode(header_b64 + "==")
        header = json.loads(header_json)
        x5c_chain = header.get("x5c", [])

        if not x5c_chain:
            logger.error("[payment_verify] Apple 通知缺少 x5c 证书链")
            return None

        # 构建证书链
        certs = []
        for cert_b64 in x5c_chain:
            cert_der = base64.b64decode(cert_b64)
            cert = load_der_x509_certificate(cert_der, default_backend())
            certs.append(cert)

        # 验证签名（除非跳过）
        if not skip_verification:
            try:
                # 用叶子证书（第一个）验证签名
                public_key = certs[0].public_key()
                payload_bytes = f"{parts[0]}.{parts[1]}".encode()
                signature = base64.urlsafe_b64decode(parts[2] + "==")

                from cryptography.hazmat.primitives.asymmetric import ec, utils
                from cryptography.hazmat.primitives.hashes import SHA256

                # Apple 使用 ECDSA with SHA256
                public_key.verify(signature, payload_bytes, ec.ECDSA(SHA256()))
            except Exception as e:
                logger.error("[payment_verify] Apple 通知签名验证失败: %s", e)
                return None
        else:
            logger.info("[payment_verify] 跳过签名验证（测试模式）")

        # 解码 payload
        notification_data = json.loads(
            base64.urlsafe_b64decode(parts[1] + "==")
        )

        print(f"[APPLE_PARSE] 解码后的通知数据 keys: {list(notification_data.keys())}", flush=True)
        print(f"[APPLE_PARSE] notificationType: {notification_data.get('notificationType', '')}", flush=True)
        print(f"[APPLE_PARSE] data 字段存在: {'data' in notification_data}", flush=True)

        # Apple V2 通知结构: data.signedTransactionInfo
        data = notification_data.get("data", {})
        if not data:
            # 兼容直接在顶层的情况
            data = notification_data
            print(f"[APPLE_PARSE] data 字段为空，使用顶层数据", flush=True)
        else:
            print(f"[APPLE_PARSE] data 字段 keys: {list(data.keys()) if isinstance(data, dict) else type(data)}", flush=True)

        signed_tx = data.get("signedTransactionInfo", "")
        signed_renewal = data.get("signedRenewalInfo", "")

        print(f"[APPLE_PARSE] 通知类型: {notification_data.get('notificationType', '')}", flush=True)
        print(f"[APPLE_PARSE] signedTransactionInfo 长度: {len(signed_tx)}", flush=True)
        print(f"[APPLE_PARSE] signedTransactionInfo 前50字符: {signed_tx[:50] if signed_tx else '空'}", flush=True)

        tx_info = {}
        if signed_tx:
            tx_parts = signed_tx.split(".")
            print(f"[APPLE_PARSE] signedTransactionInfo 分段数: {len(tx_parts)}", flush=True)

            if len(tx_parts) == 3:
                try:
                    decoded_bytes = base64.urlsafe_b64decode(tx_parts[1] + "==")
                    tx_info = json.loads(decoded_bytes)
                    print(f"[APPLE_PARSE] ✓ signedTransactionInfo 解析成功", flush=True)
                    print(f"[APPLE_PARSE]   - transactionId: {tx_info.get('transactionId', '')}", flush=True)
                    print(f"[APPLE_PARSE]   - originalTransactionId: {tx_info.get('originalTransactionId', '')}", flush=True)
                    print(f"[APPLE_PARSE]   - productId: {tx_info.get('productId', '')}", flush=True)
                    print(f"[APPLE_PARSE]   - purchaseDate: {tx_info.get('purchaseDate', '')}", flush=True)
                    print(f"[APPLE_PARSE]   - expiresDate: {tx_info.get('expiresDate', '')}", flush=True)
                    print(f"[APPLE_PARSE]   - appAccountToken: {tx_info.get('appAccountToken', '')}", flush=True)
                    print(f"[APPLE_PARSE]   - 所有字段: {list(tx_info.keys())}", flush=True)
                except Exception as e:
                    print(f"[APPLE_PARSE] ✗ signedTransactionInfo 解析失败: {e}", flush=True)
                    print(f"[APPLE_PARSE]   原始数据前100字符: {signed_tx[:100]}", flush=True)
            else:
                print(f"[APPLE_PARSE] ✗ signedTransactionInfo 格式错误（不是3段）", flush=True)
        else:
            print(f"[APPLE_PARSE] ⚠ signedTransactionInfo 为空", flush=True)

        renewal_info = {}
        if signed_renewal:
            r_parts = signed_renewal.split(".")
            if len(r_parts) == 3:
                try:
                    renewal_info = json.loads(
                        base64.urlsafe_b64decode(r_parts[1] + "==")
                    )
                except Exception:
                    renewal_info = {}

        app_account_token = (
            tx_info.get("appAccountToken")
            or renewal_info.get("appAccountToken")
            or tx_info.get("applicationUsername")
            or tx_info.get("username")
            or ""
        )

        return {
            "notification_type": notification_data.get("notificationType", ""),
            "subtype": notification_data.get("subtype", ""),
            "transaction_id": tx_info.get("transactionId", ""),
            "original_transaction_id": tx_info.get("originalTransactionId", ""),
            "product_id": tx_info.get("productId", ""),
            "expires_date": tx_info.get("expiresDate", ""),
            "purchase_date": tx_info.get("purchaseDate", ""),
            "app_account_token": app_account_token or "",
            "raw": notification_data,
            "tx_info": tx_info,
            "renewal_info": renewal_info,
        }

    except Exception as e:
        logger.error("[payment_verify] Apple 通知解析失败: %s", e)
        return None


# ===== Google Play =====

_GOOGLE_PLAY_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "")
_GOOGLE_PLAY_PACKAGE_NAME = os.getenv("GOOGLE_PLAY_PACKAGE_NAME", "")

_google_access_token: Optional[str] = None
_google_token_expires_at: float = 0


def _get_google_access_token() -> Optional[str]:
    """获取 Google Play API 的 OAuth2 access_token（用 Service Account）"""
    global _google_access_token, _google_token_expires_at

    if _google_access_token and time.time() < _google_token_expires_at:
        return _google_access_token

    sa_path = _GOOGLE_PLAY_SERVICE_ACCOUNT_JSON
    if not sa_path or not os.path.exists(sa_path):
        logger.error("[payment_verify] Google Service Account JSON 未配置或不存在: %s", sa_path)
        return None

    try:
        with open(sa_path, "r") as f:
            sa_info = json.load(f)

        client_email = sa_info.get("client_email", "")
        private_key = sa_info.get("private_key", "")

        if not client_email or not private_key:
            logger.error("[payment_verify] Service Account JSON 缺少 client_email 或 private_key")
            return None

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "iss": client_email,
            "scope": "https://www.googleapis.com/auth/androidpublisher",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }

        import jwt as pyjwt
        assertion = pyjwt.encode(payload, private_key, algorithm="RS256", headers=header)

        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=15,
        )
        resp.raise_for_status()
        token_data = resp.json()

        _google_access_token = token_data.get("access_token")
        _google_token_expires_at = now + token_data.get("expires_in", 3600) - 60
        return _google_access_token

    except Exception as e:
        logger.error("[payment_verify] 获取 Google access_token 失败: %s", e)
        return None


def verify_google_purchase(product_id: str, purchase_token: str, is_subscription: bool = False) -> dict:
    """
    验证 Google Play 购买。
    订阅走 purchases.subscriptionsv2.get（只需 token，兼容试用/优惠阶段）。
    一次性商品仍走 purchases.products.get。
    """
    if not str(purchase_token or "").strip():
        return {"valid": False, "reason": "missing_product_or_token"}
    if not is_subscription and not str(product_id or "").strip():
        return {"valid": False, "reason": "missing_product_or_token"}

    token = _get_google_access_token()
    if not token:
        return {"valid": False, "reason": "google_auth_failed"}

    package = _GOOGLE_PLAY_PACKAGE_NAME
    if not package:
        return {"valid": False, "reason": "package_name_not_configured"}

    headers = {"Authorization": f"Bearer {token}"}
    enc_token = quote(purchase_token, safe="")

    try:
        if is_subscription:
            url = (
                f"https://androidpublisher.googleapis.com/androidpublisher/v3/"
                f"applications/{package}/purchases/subscriptionsv2/tokens/{enc_token}"
            )
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 404:
                return {"valid": False, "reason": "purchase_not_found"}
            resp.raise_for_status()
            return interpret_google_subscription_v2(resp.json(), product_id)

        url = (
            f"https://androidpublisher.googleapis.com/androidpublisher/v3/"
            f"applications/{package}/purchases/products/{product_id}/tokens/{enc_token}"
        )
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 404:
            return {"valid": False, "reason": "purchase_not_found"}
        resp.raise_for_status()
        return interpret_google_purchase_payload(resp.json(), product_id)

    except Exception as e:
        logger.error("[payment_verify] Google 购买验证失败: %s", e)
        return {"valid": False, "reason": f"verify_error: {str(e)}"}


def acknowledge_google_subscription(product_id: str, purchase_token: str) -> dict:
    """开通权益后必须 acknowledge，否则约 3 天内 Play 会自动退款。"""
    if not str(product_id or "").strip() or not str(purchase_token or "").strip():
        return {"ok": False, "reason": "missing_product_or_token"}

    token = _get_google_access_token()
    if not token:
        return {"ok": False, "reason": "google_auth_failed"}
    package = _GOOGLE_PLAY_PACKAGE_NAME
    if not package:
        return {"ok": False, "reason": "package_name_not_configured"}

    enc_token = quote(purchase_token, safe="")
    url = (
        f"https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{package}/purchases/subscriptions/{product_id}/tokens/{enc_token}:acknowledge"
    )
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={},
            timeout=15,
        )
        if resp.status_code in (200, 204):
            return {"ok": True}
        logger.warning("[ACK] google subscription ack status=%s body=%s", resp.status_code, resp.text[:300])
        return {"ok": False, "reason": f"http_{resp.status_code}"}
    except Exception as e:
        logger.exception("[ACK] google subscription ack failed")
        return {"ok": False, "reason": str(e)}


def consume_google_purchase(product_id: str, purchase_token: str) -> dict:
    """
    消耗 Google Play 一次性商品购买。
    不消耗的话用户无法再次购买同一商品。

    参数:
        product_id: 产品ID（如 pack_250）
        purchase_token: 购买 token

    返回:
        {"ok": True} 或 {"ok": False, "reason": "..."}
    """
    logger.info("[CONSUME] product_id=%s token_len=%s", product_id, len(purchase_token or ""))
    token = _get_google_access_token()
    if not token:
        logger.warning("[CONSUME] google_auth_failed")
        return {"ok": False, "reason": "google_auth_failed"}

    package = _GOOGLE_PLAY_PACKAGE_NAME
    if not package:
        logger.warning("[CONSUME] package_name_not_configured")
        return {"ok": False, "reason": "package_name_not_configured"}
    logger.info("[CONSUME] package=%s", package)

    url = (
        f"https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{package}/purchases/products/{product_id}/tokens/{purchase_token}:consume"
    )
    headers = {"Authorization": f"Bearer {token}"}
    logger.info("[CONSUME] calling consume API product_id=%s", product_id)

    try:
        resp = requests.post(url, headers=headers, timeout=15)
        logger.info("[CONSUME] status_code=%s", resp.status_code)
        if resp.status_code == 200:
            return {"ok": True}
        elif resp.status_code == 404:
            return {"ok": True}
        else:
            logger.warning("[CONSUME] failed status=%s", resp.status_code)
            return {"ok": False, "reason": f"http_{resp.status_code}"}
    except Exception as e:
        logger.exception("[CONSUME] consume_error")
        return {"ok": False, "reason": f"consume_error: {str(e)}"}


def parse_google_rtdn_message(message_data: str) -> Optional[dict]:
    """
    解析 Google RTDN (Real-time Developer Notifications) 消息。

    Google 推送的是 Base64 编码的 JSON，包含:
    {
        "version": "1.0",
        "packageName": "...",
        "eventTimeMillis": "...",
        "subscriptionNotification": {
            "version": "1.0",
            "notificationType": int,
            "purchaseToken": "..."
        }
    }

    notificationType 现行枚举:
        1 RECOVERED, 2 RENEWED, 3 CANCELED（取消续订、到期前仍有效）,
        4 PURCHASED（含试用）, 5 ON_HOLD, 6 GRACE_PERIOD, 7 RESTARTED,
        12 REVOKED, 13 EXPIRED

    返回:
        {
            "notification_type": int,
            "notification_type_name": str,
            "purchase_token": str,
            "subscription_id": str,
            "package_name": str,
            "event_time_ms": str,
            "raw": dict,
        }
    """
    try:
        decoded = base64.b64decode(message_data).decode("utf-8")
        data = json.loads(decoded)

        sub_notif = data.get("subscriptionNotification") or {}
        one_time_notif = data.get("oneTimeProductNotification") or {}

        notif_type = sub_notif.get("notificationType") or one_time_notif.get("notificationType", 0)

        type_names = {
            1: "RECOVERED",
            2: "RENEWED",
            3: "CANCELED",
            4: "PURCHASED",
            5: "ON_HOLD",
            6: "GRACE_PERIOD",
            7: "RESTARTED",
            8: "PRICE_CHANGE_CONFIRMED",
            9: "DEFERRED",
            10: "PAUSED",
            11: "PAUSE_SCHEDULE_CHANGED",
            12: "REVOKED",
            13: "EXPIRED",
            20: "PENDING_PURCHASE_CANCELED",
            22: "PRICE_STEP_UP_CONSENT_UPDATED",
        }
        one_time_names = {
            1: "ONE_TIME_PURCHASED",
            2: "ONE_TIME_CANCELED",
        }
        if one_time_notif:
            type_name = one_time_names.get(int(notif_type or 0), f"ONE_TIME_{notif_type}")
        else:
            type_name = type_names.get(notif_type, f"UNKNOWN_{notif_type}")

        return {
            "notification_type": notif_type,
            "notification_type_name": type_name,
            "notification_kind": "one_time" if one_time_notif else "subscription",
            "purchase_token": sub_notif.get("purchaseToken") or one_time_notif.get("purchaseToken", ""),
            "subscription_id": sub_notif.get("subscriptionId", ""),
            "product_id": sub_notif.get("subscriptionId") or one_time_notif.get("sku", ""),
            "package_name": data.get("packageName", ""),
            "event_time_ms": data.get("eventTimeMillis", ""),
            "raw": data,
        }

    except Exception as e:
        logger.error("[payment_verify] Google RTDN 消息解析失败: %s", e)
        return None


def verify_google_pubsub_push_auth(
    authorization: Optional[str],
    *,
    audience: Optional[str] = None,
) -> bool:
    """
    校验 Google Pub/Sub Push 的 OIDC Bearer JWT。
    - 生产：必须带 Authorization: Bearer <token>
    - 本地联调：设置 GOOGLE_RTDN_SKIP_AUTH=1 可跳过（切勿用于生产）
    """
    import os

    if os.getenv("GOOGLE_RTDN_SKIP_AUTH", "").strip().lower() in ("1", "true", "yes"):
        logger.warning("[payment_verify] GOOGLE_RTDN_SKIP_AUTH enabled — skipping OIDC check")
        return True

    if not authorization or not str(authorization).startswith("Bearer "):
        return False
    token = str(authorization)[7:].strip()
    if not token:
        return False

    aud = (audience or os.getenv("GOOGLE_RTDN_AUDIENCE", "") or "").strip()
    expected_email = (os.getenv("GOOGLE_RTDN_SERVICE_ACCOUNT", "") or "").strip()

    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests

        req = google_requests.Request()
        # audience 未配置时仍校验签名与 issuer；配置了则严格比对
        claims = id_token.verify_oauth2_token(token, req, audience=aud or None)
        email = str(claims.get("email") or "")
        if not claims.get("email_verified"):
            return False
        if not email.endswith(".gserviceaccount.com"):
            return False
        if expected_email and email != expected_email:
            logger.warning("[payment_verify] RTDN SA mismatch: %s", email)
            return False
        return True
    except Exception as e:
        logger.warning("[payment_verify] Google RTDN OIDC verify failed: %s", e)
        return False


# ===== Google Play 商品价格查询 =====

def get_google_product_price(product_id: str, region_code: str = "US", product_type: str = "subscribe") -> Optional[dict]:
    """
    通过 Google Play Developer API 查询商品的实际价格。

    参数:
        product_id: store_product_id
        region_code: 地区代码（如 US, CN, JP），默认 US
        product_type: "subscribe" → 订阅接口, "point" → 一次性商品接口

    返回:
        {"price_micros": int, "currency": str, "price_display": str} 或 None
    """
    token = _get_google_access_token()
    if not token:
        return None

    package = _GOOGLE_PLAY_PACKAGE_NAME
    if not package:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    region_upper = region_code.upper()

    if product_type == "point":
        # 一次性商品接口（新版 oneTimeProducts，旧版 inappproducts 已 403）
        url = (
            f"https://androidpublisher.googleapis.com/androidpublisher/v3/"
            f"applications/{package}/oneTimeProducts/{product_id}"
        )
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                # 新接口结构: purchaseOptions[0].regionalPricingAndAvailabilityConfigs
                for opt in data.get("purchaseOptions", []):
                    for rc in opt.get("regionalPricingAndAvailabilityConfigs", []):
                        if rc.get("regionCode", "").upper() == region_upper:
                            price = rc.get("price", {})
                            currency = price.get("currencyCode", "USD")
                            units = int(price.get("units", 0))
                            nanos = int(price.get("nanos", 0))
                            price_micros = units * 1_000_000 + nanos // 1000
                            price_display = f"{units}.{nanos // 10_000_000:02d}" if nanos else str(units)
                            return {
                                "price_micros": price_micros,
                                "currency": currency,
                                "price_display": price_display,
                            }
                # 目标地区没找到，取 US 兜底
                for opt in data.get("purchaseOptions", []):
                    for rc in opt.get("regionalPricingAndAvailabilityConfigs", []):
                        if rc.get("regionCode", "").upper() == "US":
                            price = rc.get("price", {})
                            units = int(price.get("units", 0))
                            nanos = int(price.get("nanos", 0))
                            price_micros = units * 1_000_000 + nanos // 1000
                            price_display = f"{units}.{nanos // 10_000_000:02d}" if nanos else str(units)
                            return {
                                "price_micros": price_micros,
                                "currency": "USD",
                                "price_display": price_display,
                            }
                return None
            else:
                logger.warning("[payment_verify] Google oneTimeProducts price: %s %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("[payment_verify] Google oneTimeProducts price error: %s", e)
        return None

    # 订阅接口
    url = (
        f"https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{package}/subscriptions/{product_id}"
    )
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for plan in data.get("basePlans", []):
                for rc in plan.get("regionalConfigs", []):
                    if rc.get("regionCode", "").upper() == region_upper:
                        price = rc.get("price", {})
                        currency = price.get("currencyCode", "USD")
                        units = int(price.get("units", 0))
                        nanos = int(price.get("nanos", 0))
                        price_micros = units * 1_000_000 + nanos // 1000
                        price_display = f"{units}.{nanos // 10_000_000:02d}" if nanos else str(units)
                        return {
                            "price_micros": price_micros,
                            "currency": currency,
                            "price_display": price_display,
                        }
            other = data.get("basePlans", [{}])[0].get("otherRegionsConfig", {})
            usd_price = other.get("usdPrice", {})
            if usd_price:
                units = int(usd_price.get("units", 0))
                nanos = int(usd_price.get("nanos", 0))
                price_micros = units * 1_000_000 + nanos // 1000
                price_display = f"{units}.{nanos // 10_000_000:02d}" if nanos else str(units)
                return {
                    "price_micros": price_micros,
                    "currency": "USD",
                    "price_display": price_display,
                }
        else:
            logger.warning("[payment_verify] Google sub price: %s %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("[payment_verify] Google sub price error: %s", e)
    return None


def get_apple_product_price(product_id: str, region_code: str = "US") -> Optional[dict]:
    """
    通过 Apple App Store Connect API 查询商品的实际价格。

    参数:
        product_id: apple_product_id
        region_code: 地区代码（如 US, CN, JP）

    返回:
        {"price_micros": int, "currency": str, "price_display": str} 或 None
    """
    # TODO: 实现 Apple App Store Connect API 价格查询
    # 需要 APP_STORE_KEY_ID, APP_STORE_ISSUER_ID, APP_STORE_PRIVATE_KEY_PATH
    # API: GET https://api.appstoreconnect.apple.com/v1/inAppPurchases/{id}/pricePoints
    logger.info("[payment_verify] Apple price query not yet implemented for %s", product_id)
    return None
