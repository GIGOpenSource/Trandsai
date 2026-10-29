"""第三方登录：按 platform + provider_user_id + app_key 查找 / 绑定 / 注册用户。"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import or_

from core.app_key import APP_KEY_TRANDAI, DEFAULT_APP_KEY, normalize_app_key
from core.database import UserORM
from core.i18n import _, validation_message
from services.credit_service import get_or_create_user_credits

logger = logging.getLogger(__name__)

# username 列长度上限（与 UserORM 一致）
_USERNAME_MAX = 32


def oauth_username(
    email: str,
    platform: str,
    provider_user_id: str,
    *,
    app_key: str = DEFAULT_APP_KEY,
) -> str:
    """生成不超过列宽的唯一 username；主包优先已验证邮箱，马甲包加前缀避免撞车。"""
    email = (email or "").strip()
    key = normalize_app_key(app_key)
    if key == APP_KEY_TRANDAI and email and len(email) <= _USERNAME_MAX:
        return email
    # 马甲：不用裸邮箱作 username（users.username 全局唯一）
    prefix = (key[:3] or platform[:1] or "o")
    return f"{prefix}_{provider_user_id}"[:_USERNAME_MAX]


def _user_app_key(user: UserORM) -> str:
    return normalize_app_key(getattr(user, "app_key", None) or DEFAULT_APP_KEY)


def _build_vip_info(db, user_id: int, lang: str = "zh") -> dict:
    try:
        from api.auth import _build_vip_info as build

        return build(db, user_id, lang)
    except Exception:
        return {
            "is_vip": False,
            "vip_type": "",
            "vip_name": "",
            "vip_expire_at": "",
            "credits": 0,
        }


def _upgrade_guest_to_oauth(
    db,
    guest: UserORM,
    *,
    platform: str,
    provider_user_id: str,
    email: str,
    nickname: str,
    avatar_url: str,
    app_key: str,
) -> UserORM:
    """把临时 guest 账号升级为 Google/Apple（保留同一 user_id，聊天/积分不断）。"""
    guest.platform = platform
    guest.provider_user_id = provider_user_id
    guest.app_key = app_key
    if email:
        guest.email = email[:200]
    if nickname and not (guest.nickname or "").strip():
        guest.nickname = nickname[:32]
    if avatar_url and not (guest.avatar_url or "").strip():
        guest.avatar_url = avatar_url
    db.flush()
    logger.info(
        "OAuth upgrade guest: user_id=%s platform=%s app_key=%s",
        guest.id,
        platform,
        app_key,
    )
    return guest


def find_or_create_oauth_user(
    db,
    *,
    platform: str,
    provider_user_id: str,
    email: str = "",
    email_verified: bool = False,
    nickname: str = "",
    avatar_url: str = "",
    accept_language: str | None = None,
    app_key: str | None = None,
    bind_guest_user_id: int | None = None,
) -> tuple[UserORM, bool]:
    """
    返回 (user, is_new_user)。

    规则（对照 Google / Apple 官方：以 sub 为主键；仅在邮箱已由 IdP 验证时可绑定）：
    1. platform + provider_user_id + app_key 命中 → 登录（马甲账号隔离）
    2. 若带 bind_guest_user_id 且该用户仍是 guest → 升级贴到该临时账号（保留 user_id）
       - 若该 OAuth 已绑其他账号 → 409
    3. 同 app_key 下 email 已验证且 username/email 命中 → 绑定后登录
    4. 否则新建用户（写入 app_key）
    """
    platform = (platform or "").strip().lower()
    provider_user_id = (provider_user_id or "").strip()
    email = (email or "").strip()
    nickname = (nickname or "").strip()[:32]
    avatar_url = (avatar_url or "").strip()
    app_key = normalize_app_key(app_key)

    if not platform or not provider_user_id:
        raise HTTPException(
            status_code=400,
            detail=validation_message("oauth_missing_id", accept_language),
        )

    user = (
        db.query(UserORM)
        .filter(
            UserORM.platform == platform,
            UserORM.provider_user_id == provider_user_id,
            UserORM.app_key == app_key,
        )
        .first()
    )
    if user:
        # 轻量回填头像/昵称（不覆盖用户已改资料）
        changed = False
        if avatar_url and not (user.avatar_url or "").strip():
            user.avatar_url = avatar_url
            changed = True
        if nickname and not (user.nickname or "").strip():
            user.nickname = nickname[:32]
            changed = True
        if email and not (getattr(user, "email", None) or "").strip():
            user.email = email[:200]
            changed = True
        if changed:
            db.commit()
            db.refresh(user)
        if (getattr(user, "account_status", None) or "active") == "banned":
            raise HTTPException(status_code=403, detail="account_banned")
        st = (getattr(user, "account_status", None) or "active").strip().lower()
        if st in ("closed", "已销户"):
            raise HTTPException(status_code=403, detail="account_closed")
        # OAuth 已存在：若还带着另一个 guest token，不合并，直接登原账号
        return user, False

    # 临时登录账号升级：Google/Apple 贴到当前 guest（同一 user_id）
    if bind_guest_user_id:
        from services.guest_auth import is_guest_user

        guest = db.query(UserORM).filter(UserORM.id == int(bind_guest_user_id)).first()
        if guest and is_guest_user(guest) and _user_app_key(guest) == app_key:
            gst = (getattr(guest, "account_status", None) or "active").strip().lower()
            if gst == "banned":
                raise HTTPException(status_code=403, detail="account_banned")
            if gst in ("closed", "已销户"):
                raise HTTPException(status_code=403, detail="account_closed")
            # 防御：provider 已被占用时上面已 return；此处升级 guest
            _upgrade_guest_to_oauth(
                db,
                guest,
                platform=platform,
                provider_user_id=provider_user_id,
                email=email,
                nickname=nickname,
                avatar_url=avatar_url,
                app_key=app_key,
            )
            db.commit()
            db.refresh(guest)
            return guest, False
        if guest and not is_guest_user(guest):
            # 当前 token 已是正式账号，继续走新建/邮箱绑定
            pass

    if email and email_verified and len(email) <= _USERNAME_MAX:
        existing = (
            db.query(UserORM)
            .filter(
                UserORM.app_key == app_key,
                or_(UserORM.username == email, UserORM.email == email),
            )
            .first()
        )
        if existing:
            bound_platform = (existing.platform or "local").lower()
            bound_pid = (existing.provider_user_id or "").strip()
            if bound_pid and bound_pid != provider_user_id and bound_platform not in (
                "local",
                "",
                "guest",
            ):
                raise HTTPException(
                    status_code=409,
                    detail=_("该邮箱已绑定其他登录方式，请使用原方式登录"),
                )
            est = (getattr(existing, "account_status", None) or "active").strip().lower()
            if est == "banned":
                raise HTTPException(status_code=403, detail="account_banned")
            if est in ("closed", "已销户"):
                raise HTTPException(status_code=403, detail="account_closed")
            # deleted 冷静期：交给登录接口处理 pending / restore，此处仍返回该用户
            existing.platform = platform
            existing.provider_user_id = provider_user_id
            existing.app_key = app_key
            if email:
                existing.email = email[:200]
            if nickname and not (existing.nickname or "").strip():
                existing.nickname = nickname[:32]
            if avatar_url and not (existing.avatar_url or "").strip():
                existing.avatar_url = avatar_url
            db.commit()
            db.refresh(existing)
            logger.info(
                "OAuth bind: user_id=%s platform=%s app_key=%s email=%s",
                existing.id,
                platform,
                app_key,
                email,
            )
            return existing, False

    username = oauth_username(email, platform, provider_user_id, app_key=app_key)
    # username 冲突（例如截断后撞车）时加后缀重试
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

    display = nickname or (email.split("@")[0] if email else f"{platform[:1].upper()}{provider_user_id[:8]}")
    user = UserORM(
        username=username,
        nickname=(display[:32] if display else username)[:32],
        password_hash="",
        gender="",
        sexual_orientation="",
        avatar_url=avatar_url or "",
        platform=platform,
        provider_user_id=provider_user_id,
        language="en",
        app_key=app_key,
        email=(email[:200] if email else ""),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    get_or_create_user_credits(db, user.id)
    db.commit()
    db.refresh(user)
    logger.info(
        "OAuth create: user_id=%s platform=%s app_key=%s",
        user.id,
        platform,
        app_key,
    )
    return user, True


def oauth_user_payload(db, user: UserORM, lang: str = "zh") -> dict:
    from services.guest_auth import guest_flags

    vip_info = _build_vip_info(db, user.id, lang)
    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.nickname,
        "role": user.role or "user",
        "gender": user.gender or "",
        "sexual_orientation": user.sexual_orientation or "",
        "preferred_gender": getattr(user, "preferred_gender", None) or "",
        "chat_purpose": getattr(user, "chat_purpose", None) or "",
        "age": user.age,
        "birth": user.birth.strftime("%Y-%m-%d") if getattr(user, "birth", None) else "",
        "region": user.region or "",
        "occupation": user.occupation or "",
        "avatar_url": user.avatar_url or "",
        "platform": user.platform or "",
        "language": getattr(user, "language", None) or "",
        "app_key": _user_app_key(user),
        **guest_flags(user),
        **vip_info,
    }
