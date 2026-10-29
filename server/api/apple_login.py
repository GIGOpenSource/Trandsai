"""
Apple Sign in with Apple。

官方要求（Verifying a user / TN3107）：
- 验 JWKS 签名、iss、aud、exp；有 nonce 则校验
- 原生 App：aud / client_id = Bundle ID
- Web / REST：aud / client_id = Services ID
- 建议把 authorization code 一并交给服务端做 token 校验

文档：
https://developer.apple.com/documentation/signinwithapple/verifying-a-user
https://developer.apple.com/documentation/technotes/tn3107-resolving-sign-in-with-apple-response-errors
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
import time
from typing import Optional

import jwt
import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from fastapi import APIRouter, Body, Header, HTTPException, Request

from core.auth import generate_token, verify_token
from core.app_key import (
    HEADER_APP_KEY,
    app_key_from_headers,
    apple_client_ids_for_app_key,
)
from core.client_context import extract_client_device, extract_client_ip
from core.database import get_db
from core.i18n import _, normalize_ui_language, validation_message
from core.response import fail, ok
from services.oauth_users import find_or_create_oauth_user, oauth_user_payload

logger = logging.getLogger(__name__)

APPLE_TEAM_ID = os.getenv("APPLE_TEAM_ID", "")
APPLE_KEY_ID = os.getenv("APPLE_KEY_ID", "")
# 原生 App Bundle ID（identityToken.aud）— trandsai 主包
APPLE_BUNDLE_ID = os.getenv("APPLE_BUNDLE_ID", "") or os.getenv("APPLE_SERVICE_ID", "com.gig.trandsai")
# Web / code 换 token 用的 Services ID（可与 Bundle ID 相同或不同）
APPLE_SERVICE_ID = os.getenv("APPLE_SERVICE_ID", "") or APPLE_BUNDLE_ID
APPLE_PRIVATE_KEY_PATH = os.getenv("APPLE_PRIVATE_KEY_PATH", "AuthKey.p8")

router = APIRouter(tags=["Apple登录"])

_jwks_lock = threading.Lock()
_jwks_cache: dict = {"fetched_at": 0.0, "keys": []}
_JWKS_TTL_SEC = 3600


def _dedupe_ids(*groups: list[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for group in groups:
        for i in group or []:
            s = (i or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _apple_audiences() -> list[str]:
    """全量 aud 白名单（兼容旧逻辑 / 调试）；业务请用 _audiences_for_request。"""
    return _dedupe_ids(
        [APPLE_BUNDLE_ID, APPLE_SERVICE_ID],
        apple_client_ids_for_app_key("kaya"),
    )


def _audiences_for_request(request: Request) -> list[str]:
    """按 X-App-Key 选择本马甲允许的 Apple aud / client_id。

    - 无头或 trandsai → APPLE_BUNDLE_ID / APPLE_SERVICE_ID
    - kaya → APPLE_CLIENT_IDS（如 com.gig.kaya）
    """
    app_key = app_key_from_headers(request.headers)
    ids = apple_client_ids_for_app_key(app_key)
    if not ids:
        logger.error(
            "Apple audience 未配置 app_key=%s（kaya 需设置 APPLE_CLIENT_IDS）",
            app_key,
        )
    return ids


def _base64url_decode(input_str: str) -> bytes:
    rem = len(input_str) % 4
    if rem > 0:
        input_str += "=" * (4 - rem)
    return base64.urlsafe_b64decode(input_str)


def _get_apple_jwks(force: bool = False) -> list:
    now = time.time()
    with _jwks_lock:
        if (
            not force
            and _jwks_cache["keys"]
            and now - _jwks_cache["fetched_at"] < _JWKS_TTL_SEC
        ):
            return _jwks_cache["keys"]
    try:
        resp = requests.get("https://appleid.apple.com/auth/keys", timeout=10)
        resp.raise_for_status()
        keys = resp.json().get("keys", [])
    except Exception as e:
        logger.error("获取 Apple 公钥失败: %s", e)
        with _jwks_lock:
            return list(_jwks_cache["keys"] or [])
    with _jwks_lock:
        _jwks_cache["keys"] = keys
        _jwks_cache["fetched_at"] = time.time()
        return list(keys)


def _public_key_for_kid(kid: str):
    keys = _get_apple_jwks(force=False)
    matched = next((k for k in keys if k.get("kid") == kid), None)
    if not matched:
        keys = _get_apple_jwks(force=True)
        matched = next((k for k in keys if k.get("kid") == kid), None)
    if not matched:
        logger.warning("Apple 公钥中未找到 kid=%s", kid)
        return None
    try:
        n = int.from_bytes(_base64url_decode(matched["n"]), "big")
        e = int.from_bytes(_base64url_decode(matched["e"]), "big")
        return RSAPublicNumbers(e, n).public_key(default_backend())
    except Exception as e:
        logger.error("构建 Apple 公钥失败: %s", e)
        return None


def _generate_apple_client_secret(client_id: str) -> str | None:
    if not all([APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY_PATH]):
        logger.error("Apple 配置不完整（TEAM_ID / KEY_ID / PRIVATE_KEY_PATH）")
        return None

    key_path = APPLE_PRIVATE_KEY_PATH
    if not os.path.isabs(key_path):
        key_path = os.path.join(os.path.dirname(__file__), "..", key_path)

    try:
        with open(key_path, "r", encoding="utf-8") as f:
            private_key = f.read()
    except FileNotFoundError:
        logger.error("Apple 私钥文件不存在: %s", key_path)
        return None

    now = int(time.time())
    payload = {
        "iss": APPLE_TEAM_ID,
        "iat": now,
        "exp": now + 86400 * 180,
        "aud": "https://appleid.apple.com",
        "sub": client_id,
    }
    return jwt.encode(payload, private_key, algorithm="ES256", headers={"kid": APPLE_KEY_ID})


def _exchange_code_for_tokens(code: str, client_id: str) -> dict | None:
    client_secret = _generate_apple_client_secret(client_id)
    if not client_secret:
        return None
    try:
        resp = requests.post(
            "https://appleid.apple.com/auth/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        if resp.status_code >= 400:
            logger.error("Apple code 换 token 失败: %s %s", resp.status_code, resp.text[:300])
            return None
        return resp.json()
    except Exception as e:
        logger.error("Apple code 换 token 异常: %s", e)
        return None


def _normalize_nonce(raw: str) -> str:
    """Apple 有时放原始 nonce，有时放 SHA256(hex)。两端都比对。"""
    return (raw or "").strip()


def _nonce_matches(expected: str, token_nonce: str) -> bool:
    expected = _normalize_nonce(expected)
    token_nonce = _normalize_nonce(token_nonce)
    if not expected or not token_nonce:
        return False
    if expected == token_nonce:
        return True
    digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    return digest == token_nonce


def _verify_apple_id_token(
    id_token_str: str,
    *,
    expected_nonce: str = "",
    audiences: list[str] | None = None,
) -> dict | None:
    if not id_token_str:
        return None
    audiences = audiences or _apple_audiences()
    if not audiences:
        logger.error("Apple audience 未配置")
        return None

    try:
        unverified_header = jwt.get_unverified_header(id_token_str)
        kid = unverified_header.get("kid")
        if not kid:
            logger.warning("Apple id_token header 中没有 kid")
            return None

        public_key = _public_key_for_kid(kid)
        if not public_key:
            return None

        payload = jwt.decode(
            id_token_str,
            public_key,
            algorithms=["RS256"],
            audience=audiences,
            issuer="https://appleid.apple.com",
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            leeway=30,
        )

        if expected_nonce:
            token_nonce = payload.get("nonce") or ""
            if not token_nonce:
                logger.warning("客户端传了 nonce，但 id_token 无 nonce claim")
                return None
            if not _nonce_matches(expected_nonce, token_nonce):
                logger.warning("Apple id_token nonce 不匹配")
                return None

        email_verified = payload.get("email_verified") in (True, "true", "True", 1, "1")
        # 无 email_verified 字段但有 email 时，Apple 签发邮箱可视为已验证
        if payload.get("email") and "email_verified" not in payload:
            email_verified = True

        return {
            "apple_user_id": payload["sub"],
            "email": payload.get("email") or "",
            "email_verified": email_verified,
            "aud": payload.get("aud") or "",
        }
    except jwt.ExpiredSignatureError:
        logger.warning("Apple id_token 已过期")
        return None
    except jwt.InvalidAudienceError:
        logger.warning("Apple id_token audience 不匹配，允许列表=%s", audiences)
        return None
    except jwt.InvalidIssuerError:
        logger.warning("Apple id_token issuer 不匹配")
        return None
    except Exception as e:
        logger.warning("Apple id_token 验证失败: %s", e)
        return None


@router.post(
    "/api/auth/applelogin",
    summary="Apple 第三方登录",
    description="传 identityToken（id_token）；可选 authorization code、nonce、姓名。",
    response_model=dict,
)
async def apple_login(
    request: Request,
    body: dict = Body(...),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    lang = normalize_ui_language(accept_language)
    id_token_str = body.get("id_token") or body.get("identityToken") or ""
    code = body.get("code") or body.get("authorizationCode") or ""
    first_name = body.get("firstName") or body.get("givenName") or ""
    last_name = body.get("lastName") or body.get("familyName") or ""
    expected_nonce = body.get("nonce") or ""
    # 快速登录 token：把 Apple 贴到临时账号
    bind_guest_user_id = verify_token(x_token) if x_token else None

    if not id_token_str and not code:
        return fail(message=validation_message("missing_id_token_or_code", accept_language), code=400)

    app_key = app_key_from_headers(request.headers)
    audiences = _audiences_for_request(request)
    logger.info(
        "[apple_login] app_key=%s %s=%s audiences=%s",
        app_key,
        HEADER_APP_KEY,
        request.headers.get(HEADER_APP_KEY) or request.headers.get("x-app-key") or "",
        audiences,
    )
    if not audiences:
        return fail(message=validation_message("apple_login_failed", accept_language), code=401)

    # code → token：client_id 必须与授权时一致（原生=Bundle ID）
    exchanged = None
    if code:
        for client_id in audiences:
            exchanged = _exchange_code_for_tokens(code, client_id)
            if exchanged and exchanged.get("id_token"):
                break
        if exchanged and exchanged.get("id_token"):
            id_token_str = exchanged["id_token"]
        elif not id_token_str:
            return fail(message=validation_message("apple_code_exchange_failed", accept_language), code=401)
        elif not exchanged:
            # 已有 identityToken：code 换取失败不阻断（code 易过期 / 配置不全）
            logger.warning("Apple code 校验跳过（换 token 失败），仅依赖 id_token 验签")

    info = _verify_apple_id_token(
        str(id_token_str).strip(),
        expected_nonce=str(expected_nonce or ""),
        audiences=audiences,
    )
    if not info:
        return fail(message=validation_message("apple_login_failed", accept_language), code=401)

    nickname = f"{first_name} {last_name}".strip()
    restored = False
    want_restore = bool(body.get("restore") or body.get("cancel_deletion"))

    try:
        with get_db() as db:
            user, is_new = find_or_create_oauth_user(
                db,
                platform="apple",
                provider_user_id=info["apple_user_id"],
                email=info["email"],
                email_verified=bool(info.get("email_verified")),
                nickname=nickname,
                avatar_url="",
                accept_language=accept_language,
                app_key=app_key,
                bind_guest_user_id=bind_guest_user_id,
            )
            from services.credit_service import touch_user_client_context
            from core.auth import apply_account_status_flags, normalize_account_status
            from core.i18n import (
                account_closed_message,
                account_pending_deletion_message,
            )
            from services.account_lifecycle import (
                handle_deleted_user_on_login,
                is_grace_expired,
                restore_account_from_deletion,
            )

            _st = normalize_account_status(getattr(user, "account_status", None))
            if _st == "banned":
                raise HTTPException(status_code=403, detail="account_banned")
            if _st == "closed":
                return fail(
                    message=account_closed_message(accept_language),
                    code=403,
                    data={"detail": "account_closed", "account_status": "closed"},
                )
            if _st == "deleted":
                if want_restore and not is_grace_expired(user):
                    restore_account_from_deletion(db, user)
                    db.commit()
                    apply_account_status_flags(user.id, "active")
                    restored = True
                else:
                    action, payload = handle_deleted_user_on_login(db, user)
                    if action == "closed":
                        db.commit()
                        apply_account_status_flags(user.id, "closed")
                        return fail(
                            message=account_closed_message(accept_language),
                            code=403,
                            data=payload,
                        )
                    return fail(
                        message=account_pending_deletion_message(
                            accept_language,
                            days_left=int(payload.get("days_left") or 7),
                        ),
                        code=401,
                        data=payload,
                    )

            touch_user_client_context(
                db,
                user.id,
                ip=extract_client_ip(request),
                device=extract_client_device(request),
            )
            from services.version_prompt import persist_app_version_from_request

            persist_app_version_from_request(request, user.id, db=db)
            db.commit()
            user_id = user.id
            out_user = oauth_user_payload(db, user, lang)
    except HTTPException as e:
        return fail(message=str(e.detail), code=e.status_code)
    except Exception as e:
        logger.exception("Apple 登录建档失败: %s", e)
        return fail(message=validation_message("login_retry_later", accept_language), code=500)

    token = generate_token(user_id)
    try:
        from services.recommend_ua import after_user_auth
        after_user_auth(user_id, is_new=is_new)
    except Exception:
        pass
    try:
        from services.growth_attribution import touch_attribution_from_request
        touch_attribution_from_request(
            request,
            user_id,
            event_type="register" if is_new else "login",
            login_method="apple",
        )
    except Exception:
        pass
    from core.i18n import account_restored_message

    msg = account_restored_message(accept_language) if restored else _("登录成功")
    return ok(data={"token": token, "user": out_user}, message=msg)
