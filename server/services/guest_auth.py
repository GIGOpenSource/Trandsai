"""设备快速（临时）登录：platform=guest，后续可绑定 Google / Apple。

临时账号可正常使用业务；不可发起 IAP 支付。绑定正式登录后 can_pay=true。
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Optional, Tuple

from fastapi import HTTPException

from core.app_key import DEFAULT_APP_KEY, normalize_app_key
from core.client_context import normalize_device_id
from core.database import UserORM
from core.i18n import validation_message
from services.credit_service import get_or_create_user_credits

logger = logging.getLogger(__name__)

GUEST_PLATFORM = "guest"
_DEVICE_ID_MIN = 8
_DEVICE_ID_MAX = 64
_USERNAME_MAX = 32
_BAD_DEVICE_IDS = frozenset({
    "",
    "anonymous",
    "unknown",
    "null",
    "undefined",
    "none",
    "0",
    "guest",
})


def is_guest_user(user: Optional[UserORM]) -> bool:
    if user is None:
        return False
    return (getattr(user, "platform", None) or "").strip().lower() == GUEST_PLATFORM


def user_can_pay(user: Optional[UserORM]) -> bool:
    """正式登录（google / apple / local 等）可支付；guest 不可。"""
    if user is None:
        return False
    st = (getattr(user, "account_status", None) or "active").strip().lower()
    if st in ("banned", "closed", "deleted"):
        return False
    return not is_guest_user(user)


def normalize_guest_device_id(raw: Optional[str]) -> str:
    return normalize_device_id(raw, max_len=_DEVICE_ID_MAX)


def validate_device_id(raw: Optional[str], *, accept_language: Optional[str] = None) -> str:
    device_id = normalize_guest_device_id(raw)
    low = device_id.lower()
    if not device_id or low in _BAD_DEVICE_IDS or len(device_id) < _DEVICE_ID_MIN:
        raise HTTPException(
            status_code=400,
            detail=validation_message("guest_device_id_required", accept_language),
        )
    # 仅允许常见设备 ID 字符，避免注入怪异串
    if not re.fullmatch(r"[A-Za-z0-9._:\-]+", device_id):
        raise HTTPException(
            status_code=400,
            detail=validation_message("guest_device_id_invalid", accept_language),
        )
    return device_id


def _guest_username(device_id: str, *, app_key: str) -> str:
    key = normalize_app_key(app_key)
    digest = hashlib.sha256(f"{key}:{device_id}".encode("utf-8")).hexdigest()[:20]
    prefix = (key[:3] or "g")
    return f"g_{prefix}_{digest}"[:_USERNAME_MAX]


def find_or_create_guest_user(
    db,
    *,
    device_id: str,
    app_key: Optional[str] = None,
    accept_language: Optional[str] = None,
    nickname: str = "",
) -> Tuple[UserORM, bool]:
    """按 app_key + device_id 查找或创建临时用户。返回 (user, is_new)。"""
    device_id = validate_device_id(device_id, accept_language=accept_language)
    app_key = normalize_app_key(app_key)
    nick = (nickname or "").strip()[:32]

    user = (
        db.query(UserORM)
        .filter(
            UserORM.platform == GUEST_PLATFORM,
            UserORM.provider_user_id == device_id,
            UserORM.app_key == app_key,
        )
        .first()
    )
    if user:
        st = (getattr(user, "account_status", None) or "active").strip().lower()
        if st == "banned":
            raise HTTPException(status_code=403, detail="account_banned")
        if st in ("closed", "已销户"):
            raise HTTPException(status_code=403, detail="account_closed")
        if nick and not (user.nickname or "").strip():
            user.nickname = nick
            db.flush()
        return user, False

    username = _guest_username(device_id, app_key=app_key)
    base = username
    for i in range(0, 8):
        candidate = base if i == 0 else f"{base[: _USERNAME_MAX - 2]}{i}"[:_USERNAME_MAX]
        taken = db.query(UserORM).filter(UserORM.username == candidate).first()
        if not taken:
            username = candidate
            break
    else:
        raise HTTPException(
            status_code=500,
            detail=validation_message("oauth_username_exhausted", accept_language),
        )

    display = nick or f"Guest{(device_id[-4:] if len(device_id) >= 4 else '')}"
    user = UserORM(
        username=username,
        nickname=display[:32],
        password_hash="",
        gender="",
        sexual_orientation="",
        avatar_url="",
        platform=GUEST_PLATFORM,
        provider_user_id=device_id,
        language="en",
        app_key=app_key,
        email="",
    )
    db.add(user)
    db.flush()
    get_or_create_user_credits(db, user.id)
    db.flush()
    logger.info(
        "Guest create: user_id=%s app_key=%s device=%s…",
        user.id,
        app_key,
        device_id[:8],
    )
    return user, True


def assert_user_can_pay(db, user_id: int, *, accept_language: Optional[str] = None) -> UserORM:
    """支付入口校验：guest / 异常状态直接 403。"""
    user = db.query(UserORM).filter(UserORM.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="auth_required")
    if is_guest_user(user):
        raise HTTPException(
            status_code=403,
            detail=validation_message("guest_cannot_pay", accept_language),
        )
    st = (getattr(user, "account_status", None) or "active").strip().lower()
    if st == "banned":
        raise HTTPException(status_code=403, detail="account_banned")
    if st in ("closed", "deleted"):
        raise HTTPException(status_code=403, detail="account_closed")
    return user


def guest_flags(user: Optional[UserORM]) -> dict[str, Any]:
    guest = is_guest_user(user)
    return {
        "platform": (getattr(user, "platform", None) or "") if user else "",
        "is_guest": guest,
        "can_pay": user_can_pay(user),
        "login_required_for_pay": guest,
    }
