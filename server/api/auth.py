import hashlib
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Request
from pydantic import BaseModel, Field

from core.gender import canonical_preferred_gender, canonical_user_gender
from core.i18n import (
    account_closed_message,
    account_deleted_message,
    account_pending_deletion_message,
    account_restored_message,
    auth_required_message,
    no_update_fields_message,
    normalize_ui_language,
    profile_update_failed_message,
    ticket_issue_failed_message,
    user_not_found_message,
    validation_message,
    vip_plan_display_name,
    _,
)

from core.database import (
    CompanionORM, UserCompanionStateORM, UserORM,
    get_db,
)
from core.auth import (
    generate_token, delete_token, verify_token as redis_verify_token,
    generate_admin_token, verify_admin_token, delete_admin_token,
    clear_all_admin_tokens as _redis_clear_admin_tokens,
    issue_ws_ticket, WS_TICKET_TTL,
)
from core.client_context import extract_client_device, extract_client_ip

logger = logging.getLogger(__name__)

router = APIRouter(tags=["认证"])

_PBKDF2_ROUNDS = 200_000
_PASSWORD_HASH_PREFIX = "pbkdf2_sha256"


def _extract_client_ip(request: Request) -> str:
    return extract_client_ip(request)


def _extract_client_device(request: Request) -> str:
    return extract_client_device(request)


def _normalize_username(raw: str) -> str:
    """邮箱/用户名统一：去首尾空白；含 @ 则按邮箱小写存储与比对。"""
    v = (raw or "").strip()
    if "@" in v:
        return v.lower()
    return v

# --------检查用户名-------
class CheckUsernameRequest(BaseModel):
    username: str = Field(description="用户名(邮箱)")
    password: str = Field(description="密码")
    model_config = {
        "json_schema_extra": {
            "example": {
                "username": "123789@163.com",
                "password": "123456"
            }
        }
    }

# --------注册-------
class RegisterRequest(BaseModel):
    username: str = Field(description="用户名(邮箱)")
    password: str = Field(description="密码")
    nickname: str | None = Field(None, description="昵称")
    gender: str | None = Field(None, description="性别：male/female/other")
    sexual_orientation: str | None = Field(None, description="性取向")
    preferred_gender: str | None = Field(None, description="想要交流的性别：male/female/non-binary/confidential")
    chat_purpose: str | None = Field(None, description="聊天目的：chatting/companionship/romance/communication")
    birth: str | None = Field(None, description="出生日期，格式：2000-01-15")
    model_config = {
        "json_schema_extra": {
            "example": {
                "username": "123789@163.com",
                "password": "123456",
                "nickname": "小明",
                "gender": "female",
                "sexual_orientation": "heterosexual",
                "preferred_gender": "male",
                "chat_purpose": "chatting"
            }
        }
    }
# --------登陆-------
class LoginRequest(BaseModel):
    username: str = Field(description="用户名")
    password: str = Field(description="登录密码")

    model_config = {
        "json_schema_extra": {
            "example": {
                "username": "user001",
                "password": "123456"
            }
        }
    }
# --------更新个人信息-------
class UserUpdateMeRequest(BaseModel):
    """更新当前用户信息 请求体模型"""
    nickname: Optional[str] = None
    gender: Optional[str] = None
    sexual_orientation: Optional[str] = None
    preferred_gender: Optional[str] = None
    chat_purpose: Optional[str] = None
    avatar_url: Optional[str] = None
    profile_backgrounds: Optional[Any] = None
    age: Optional[int] = None
    region: Optional[str] = None
    occupation: Optional[str] = None

    model_config = {
        "extra": "ignore",
        "json_schema_extra": {
            "example": {
                "nickname": "新昵称",
                "gender": "female",
                "sexual_orientation": "heterosexual",
                "preferred_gender": "male",
                "chat_purpose": "chatting",
                "avatar_url": "https://xxx/avatar.jpg",
                "profile_backgrounds": [
                    "https://storage.googleapis.com/trandsbucket/images/bg.jpg"
                ],
                "age": 23,
                "region": "上海市",
                "occupation": "设计师"
            }
        }
    }


class UserLanguageRequest(BaseModel):
    """更新当前用户语言"""
    language: str = Field(..., description="UI 语言短码，如 ar / zh / en / ja")

    model_config = {
        "extra": "ignore",
        "json_schema_extra": {
            "example": {"language": "ar"}
        }
    }



def _get_admin_password() -> str:
    password = (os.getenv("ADMIN_PASSWORD") or "").strip()
    if not password or password == "admin123":
        raise ValueError("ADMIN_PASSWORD is missing or using an insecure default value")
    return password


def _hash_token(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


# 兼容旧版（历史数据已存储为截断 sha256）
def _legacy_password_hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()[:32]


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ROUNDS
    ).hex()
    return f"{_PASSWORD_HASH_PREFIX}${_PBKDF2_ROUNDS}${salt}${digest}"


def _verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False

    parts = stored_hash.split("$")
    if len(parts) == 4 and parts[0] == _PASSWORD_HASH_PREFIX:
        _, rounds_text, salt, expected = parts
        try:
            rounds = int(rounds_text)
        except ValueError:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds
        ).hex()
        return secrets.compare_digest(actual, expected)

    # 兼容旧 hash
    return secrets.compare_digest(_legacy_password_hash(password), stored_hash)


# ===== 管理员 Token（Redis 存储，24小时有效）=====


class AdminLoginError(Exception):
    """管理员登录业务失败（非密码错误时携带错误码）。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def create_token(password: str, username: Optional[str] = None) -> Optional[str]:
    """管理员登录：优先 users 表账号密码；兼容旧 ADMIN_PASSWORD。

    账号存在且密码正确但未生效/已暂停时抛 AdminLoginError('account_inactive')。
    """
    uname = (username or "gigadmin").strip() or "gigadmin"
    try:
        with get_db() as db:
            user = db.query(UserORM).filter(UserORM.username == uname).first()
            if user:
                role_key = (user.role or "user").strip().lower()
                if role_key == "user":
                    return None
                if not _verify_password(password, user.password_hash):
                    return None
                status = (getattr(user, "account_status", None) or "active").strip().lower() or "active"
                if status != "active":
                    raise AdminLoginError("account_inactive")
                if "$" not in (user.password_hash or ""):
                    user.password_hash = _hash_password(password)
                    db.commit()
                return generate_admin_token(
                    _hash_token(password),
                    payload={"user_id": user.id, "username": user.username, "role_key": role_key},
                )
    except AdminLoginError:
        raise
    except Exception:
        logger.exception("admin login by users table failed")

    if password != _get_admin_password():
        return None
    return generate_admin_token(
        _hash_token(password),
        payload={"username": uname, "role_key": "admin"},
    )


def verify_token(token: str) -> bool:
    """校验管理员 Token 是否有效（查 Redis）"""
    return verify_admin_token(token)


# ===== 用户 Token（数据库存储，7天有效，服务器重启不丢失）=====

def create_user_token(user_id: int) -> str:
    """兼容旧名：实际写入 Redis（与登录一致），并可选同步到 users.token 字段。"""
    from core.auth import generate_token

    token = generate_token(user_id)
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    from core.database import get_db, UserORM
    try:
        with get_db() as db:
            user = db.query(UserORM).filter(UserORM.id == user_id).first()
            if user:
                user.token = token
                user.token_expire = expire
                db.commit()
    except Exception:
        logger.warning("create_user_token: failed to mirror token to DB for user=%s", user_id)
    return token


def verify_user_token(token: str) -> Optional[int]:
    """优先 Redis；回退 DB users.token（遗留）。"""
    if not token:
        return None
    uid = redis_verify_token(token)
    if uid:
        return uid
    from core.database import get_db, UserORM
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.token == token).first()
        if not user or not user.token_expire:
            return None
        now = datetime.now(timezone.utc)
        expire = user.token_expire
        if expire.tzinfo is None:
            expire = expire.replace(tzinfo=timezone.utc)
        if now > expire:
            user.token = ""
            user.token_expire = None
            db.commit()
            return None
        return user.id


@router.post("/api/auth/register",
             summary="用户注册",
             description="创建新用户账号，返回用户信息和Token",
             response_model=dict,
             responses={
                 200: {
                     "description": "注册成功",
                     "content": {
                         "application/json": {
                             "example": {
                                 "token": "abc123...",
                                 "user": {
                                     "id": 1,
                                     "username": "user001",
                                     "nickname": "用户昵称",
                                     "gender": "male",
                                     "age": 25
                                 }
                             }
                         }
                     }
                 },
                 400: {"description": "用户名已存在或参数错误"}
             })
async def user_register(
    request: Request,
    data: RegisterRequest,
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    lang = normalize_ui_language(accept_language)
    username = _normalize_username(data.username)
    password = data.password
    nickname = data.nickname.strip() if data.nickname else ""
    if nickname and len(nickname) > 32:
        raise HTTPException(status_code=400, detail=validation_message("nickname_too_long", lang=lang))
    gender = canonical_user_gender(data.gender)
    from services.recommend_tags import canonical_orientation
    sexual_orientation = canonical_orientation(data.sexual_orientation)
    preferred_gender = canonical_preferred_gender(data.preferred_gender)
    chat_purpose = data.chat_purpose or ""
    from core.app_key import app_key_from_headers

    _reg_app_key = app_key_from_headers(request.headers)

    # 解析出生日期
    birth = None
    if data.birth:
        try:
            birth = datetime.strptime(data.birth, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail=validation_message("bad_birth_date", lang=lang))

    if not username or len(username) < 3:
        raise HTTPException(status_code=400, detail=validation_message("username_too_short", lang=lang))
    if len(username) > 128:
        raise HTTPException(status_code=400, detail=validation_message("email_too_long", lang=lang))
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail=validation_message("password_too_short", lang=lang))

    with get_db() as db:
        existing = db.query(UserORM).filter(UserORM.username == username).first()
        if existing:
            raise HTTPException(status_code=400, detail=validation_message("username_exists", lang=lang))

        user = UserORM(
            username=username,
            nickname=(nickname or username)[:32],
            password_hash=_hash_password(password),
            gender=gender,
            sexual_orientation=sexual_orientation,
            preferred_gender=preferred_gender,
            chat_purpose=chat_purpose,
            birth=birth,
            language="en",
            app_key=_reg_app_key,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # 初始化积分记录
        from services.credit_service import get_or_create_user_credits, touch_user_client_context

        get_or_create_user_credits(db, user.id)
        touch_user_client_context(
            db,
            user.id,
            ip=_extract_client_ip(request),
            device=_extract_client_device(request),
        )
        from services.version_prompt import persist_app_version_from_request

        persist_app_version_from_request(request, user.id, db=db)
        db.commit()

        user_id = user.id
        vip_info = _build_vip_info(db, user_id, lang)
        out_user = {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname,
            "gender": user.gender,
            "sexual_orientation": user.sexual_orientation,
            "preferred_gender": user.preferred_gender,
            "chat_purpose": user.chat_purpose,
            "age": user.age,
            "birth": user.birth.strftime("%Y-%m-%d") if user.birth else "",
            "region": user.region or "",
            "occupation": user.occupation or "",
            "language": getattr(user, "language", None) or "",
            **vip_info,
        }

    token = generate_token(user_id)
    try:
        from services.recommend_ua import after_user_auth
        after_user_auth(user_id, is_new=True)
    except Exception:
        pass
    try:
        from services.growth_attribution import touch_attribution_from_request
        touch_attribution_from_request(request, user_id, event_type="register", login_method="email")
    except Exception:
        pass
    return {"token": token, "user": out_user}


@router.post("/api/auth/check-username",
             summary="检查用户名和密码是否匹配",
             description="根据用户名和密码检查该账号状态。用户名不存在返回200表示可注册，密码错误返回401，用户已存在返回409",
             response_model=dict,
             responses={
                 200: {
                     "description": "用户名可用，可以注册",
                     "content": {
                         "application/json": {
                             "example": {
                                 "code": 200,
                                 "message": "该用户名可用",
                                 "results": {
                                     "exists": False
                                 }
                             }
                         }
                     }
                 },
                 400: {
                     "description": "参数错误",
                     "content": {
                         "application/json": {
                             "example": {
                                 "code": 400,
                                 "message": "用户名不能为空",
                                 "results": {}
                             }
                         }
                     }
                 },
                 401: {
                     "description": "密码错误",
                     "content": {
                         "application/json": {
                             "example": {
                                 "code": 401,
                                 "message": "用户名或密码错误",
                                 "results": {}
                             }
                         }
                     }
                 },
                 409: {
                     "description": "用户已存在",
                     "content": {
                         "application/json": {
                             "example": {
                                 "code": 409,
                                 "message": "该用户已存在",
                                 "results": {}
                             }
                         }
                     }
                 }
             })
async def check_username(request: Request, data: CheckUsernameRequest):
    from core.response import ok, fail
    al = request.headers.get("accept-language")
    username = _normalize_username(data.username)
    password = data.password

    if not username:
        return fail(message=validation_message("username_empty", al), code=400)

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.username == username).first()

        # 用户名不存在，可以注册
        if not user:
            return ok(data={"exists": False}, message=validation_message("username_available", al))

        # 用户名存在，检查密码
        if not password:
            return fail(message=validation_message("password_required", al), code=400)

        # 验证密码
        if not _verify_password(password, user.password_hash):
            return fail(message=validation_message("username_or_password_wrong", al), code=401)

        # 用户名和密码都正确
        return fail(message=validation_message("username_taken", al), code=409)


def _get_vip_name(vip_type: str, lang: str = "zh") -> str:
    """根据语言获取 VIP 类型名称。"""
    return vip_plan_display_name(vip_type, lang=lang)


def _build_vip_info(db, user_id: int, lang: str = "zh") -> dict:
    """查询用户 VIP/积分状态，返回登录响应所需的附加字段。
    终身与订阅在 is_vip 上等价（统一 is_vip_active）。
    登录时顺带刷新/补发 VIP 日赠。"""
    from services.credit_service import get_effective_balance, is_vip_active, refresh_daily_gifts

    uc = refresh_daily_gifts(db, user_id)
    if not uc:
        return {"is_vip": False, "vip_type": "", "vip_name": "", "vip_expire_at": "", "credits": 0}

    is_vip = is_vip_active(uc)
    vip_type = (uc.vip_type or "") if is_vip else ""
    expire_at = uc.vip_expire_at
    if expire_at and expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    # 终身不暴露到期日，避免前端误当成「还要续费的订阅」
    vip_expire_at = ""
    if is_vip and vip_type != "lifetime" and expire_at:
        vip_expire_at = expire_at.isoformat()

    return {
        "is_vip": is_vip,
        "vip_type": vip_type,
        "vip_name": _get_vip_name(vip_type, lang),
        "vip_expire_at": vip_expire_at,
        "credits": get_effective_balance(uc),
    }


class QuickLoginRequest(BaseModel):
    device_id: Optional[str] = Field(
        None,
        description="设备唯一标识（推荐）；也可只放在 Header X-Device-Id",
        max_length=64,
    )
    nickname: Optional[str] = Field(None, max_length=32, description="可选展示名")
    restore: Optional[bool] = Field(
        False, description="冷静期内取消注销并登录，同 Google/Apple 的 restore"
    )
    cancel_deletion: Optional[bool] = Field(
        False, description="restore 别名"
    )


@router.post(
    "/api/auth/quick-login",
    summary="设备快速（临时）登录",
    description=(
        "用设备唯一标识创建/登录临时账号。可聊天等正常业务；"
        "不可支付。后续带同一 x-token 走 Google/Apple 登录可绑定到本账号。"
        "注销冷静期内再次登录返回 401 account_pending_deletion；"
        "body.restore=true 则取消注销并登录（与 Google/Apple 相同）。"
    ),
)
async def quick_login(
    request: Request,
    data: Optional[QuickLoginRequest] = Body(None),
    x_device_id: Optional[str] = Header(None, alias="X-Device-Id"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    from core.app_key import app_key_from_headers
    from core.auth import apply_account_status_flags, normalize_account_status
    from core.response import fail, ok
    from services.account_lifecycle import (
        handle_deleted_user_on_login,
        is_grace_expired,
        restore_account_from_deletion,
    )
    from services.guest_auth import find_or_create_guest_user
    from services.oauth_users import oauth_user_payload

    body = data or QuickLoginRequest()
    device_id = (body.device_id or "").strip() or extract_client_device(request, x_device_id)
    app_key = app_key_from_headers(request.headers)
    al = accept_language
    want_restore = bool(body.restore or body.cancel_deletion)
    restored = False

    try:
        with get_db() as db:
            user, is_new = find_or_create_guest_user(
                db,
                device_id=device_id,
                app_key=app_key,
                accept_language=al,
                nickname=(body.nickname or "").strip(),
            )
            _st = normalize_account_status(getattr(user, "account_status", None))
            if _st == "banned":
                return fail(message="account_banned", code=403)
            if _st == "closed":
                return fail(
                    message=account_closed_message(al),
                    code=403,
                    data={"detail": "account_closed", "account_status": "closed"},
                )
            if _st == "deleted":
                if want_restore and not is_grace_expired(user):
                    restore_account_from_deletion(db, user)
                    db.commit()
                    apply_account_status_flags(user.id, "active")
                    restored = True
                    is_new = False
                else:
                    action, payload = handle_deleted_user_on_login(db, user)
                    if action == "closed":
                        db.commit()
                        apply_account_status_flags(user.id, "closed")
                        return fail(
                            message=account_closed_message(al),
                            code=403,
                            data=payload,
                        )
                    return fail(
                        message=account_pending_deletion_message(
                            al, days_left=int(payload.get("days_left") or 7)
                        ),
                        code=401,
                        data=payload,
                    )

            from services.credit_service import touch_user_client_context

            touch_user_client_context(
                db,
                user.id,
                ip=extract_client_ip(request),
                device=extract_client_device(request, x_device_id) or device_id,
            )
            from services.version_prompt import persist_app_version_from_request

            persist_app_version_from_request(request, user.id, db=db)
            db.commit()
            db.refresh(user)
            user_id = user.id
            out_user = oauth_user_payload(db, user)
    except HTTPException as e:
        return fail(message=str(e.detail), code=e.status_code)

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
            login_method="guest",
        )
    except Exception:
        pass

    return ok(
        data={
            "token": token,
            "user": out_user,
            "is_new": is_new,
            "restored": restored,
            "is_guest": bool(out_user.get("is_guest")),
            "can_pay": bool(out_user.get("can_pay")),
            "login_required_for_pay": bool(out_user.get("login_required_for_pay")),
        },
        message=account_restored_message(al) if restored else "登录成功",
    )


@router.post("/api/auth/login",
             summary="用户登录",
             description="使用用户名和密码登录，返回用户信息和Token",
             response_model=dict,
             responses={
                 200: {
                     "description": "登录成功",
                     "content": {
                         "application/json": {
                             "example": {
                                 "token": "abc123...",
                                 "user": {
                                     "id": 1,
                                     "username": "user001",
                                     "nickname": "用户昵称",
                                     "role": "user"
                                 }
                             }
                         }
                     }
                 },
                 401: {"description": "用户名或密码错误"}
             })
async def user_login(
    request: Request,
    data: LoginRequest,
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    lang = normalize_ui_language(accept_language)
    username = _normalize_username(data.username)
    password = data.password

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.username == username).first()
        # 兼容历史：库里邮箱大小写不一致
        if not user and "@" in username:
            from sqlalchemy import func

            user = (
                db.query(UserORM)
                .filter(func.lower(UserORM.username) == username)
                .first()
            )
            if user and user.username != username:
                user.username = username
                db.commit()
        if not user:
            raise HTTPException(status_code=401, detail="account_not_found")
        if not _verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="wrong_password")

        from core.auth import normalize_account_status
        from core.response import fail
        from services.account_lifecycle import handle_deleted_user_on_login

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
            action, payload = handle_deleted_user_on_login(db, user)
            if action == "closed":
                from core.auth import apply_account_status_flags

                db.commit()
                apply_account_status_flags(user.id, "closed")
                return fail(
                    message=account_closed_message(accept_language),
                    code=403,
                    data=payload,
                )
            # 冷静期内：401 提示可取消注销
            return fail(
                message=account_pending_deletion_message(
                    accept_language, days_left=int(payload.get("days_left") or 7)
                ),
                code=401,
                data=payload,
            )
        if "$" not in (user.password_hash or ""):
            # 兼容升级：用户首次使用旧 hash 登录后，平滑迁移到新 hash
            user.password_hash = _hash_password(password)
            db.commit()

        user_id = user.id
        vip_info = _build_vip_info(db, user_id, lang)
        out_user = {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname,
            "role": user.role or "user",
            "gender": user.gender,
            "sexual_orientation": user.sexual_orientation or "",
            "preferred_gender": user.preferred_gender or "",
            "chat_purpose": user.chat_purpose or "",
            "age": user.age,
            "region": user.region or "",
            "occupation": user.occupation or "",
            "language": getattr(user, "language", None) or "",
            **vip_info,
        }

        from services.credit_service import touch_user_client_context
        from services.version_prompt import persist_app_version_from_request

        touch_user_client_context(
            db,
            user_id,
            ip=_extract_client_ip(request),
            device=_extract_client_device(request),
        )
        persist_app_version_from_request(request, user_id, db=db)
        db.commit()

    token = generate_token(user_id)
    try:
        from services.recommend_ua import after_user_auth
        after_user_auth(user_id, is_new=False)
    except Exception:
        pass
    try:
        from services.growth_attribution import touch_attribution_from_request
        touch_attribution_from_request(request, user_id, event_type="login", login_method="email")
    except Exception:
        pass
    logger.info(
        "[auth.login] user_id=%s username=%s ip=%s device=%s",
        user_id,
        username,
        _extract_client_ip(request),
        _extract_client_device(request),
    )
    return {"token": token, "user": out_user}


@router.post(
    "/api/auth/ws-ticket",
    summary="签发 WebSocket 一次性票据",
    description="登录后通过 x-token 换取短时 ticket，供 WS 握手 query 使用（一次性、60 秒）",
)
async def create_ws_ticket(
    request: Request,
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    from core.response import ok, fail

    user_id = redis_verify_token(x_token) if x_token else None
    if not user_id:
        return fail(
            message=auth_required_message(request.headers.get("accept-language")),
            code=401,
        )
    try:
        ticket = issue_ws_ticket(user_id, ttl=WS_TICKET_TTL)
    except Exception:
        logger.exception("签发 ws-ticket 失败")
        return fail(message=ticket_issue_failed_message(request.headers.get("accept-language")), code=500)
    return ok(data={"ticket": ticket, "expires_in": WS_TICKET_TTL})


@router.get(
    "/api/auth/ws-ticket-debug",
    summary="[DEBUG] 测试 ws-ticket 全链路",
    description="签发 ticket → 立即验证 → 返回结果，用于排查 ticket 是否能被 Redis 正确读删",
)
async def ws_ticket_debug(
    request: Request,
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    from core.response import ok, fail
    from core.auth import verify_ws_ticket as _verify_ticket
    import time as _t

    user_id = redis_verify_token(x_token) if x_token else None
    if not user_id:
        return fail(message=_("需要 x-token"), code=401)

    steps = []

    # Step 1: 签发
    t0 = _t.monotonic()
    try:
        ticket = issue_ws_ticket(user_id, ttl=WS_TICKET_TTL)
        t_issue = round((_t.monotonic() - t0) * 1000, 1)
        steps.append({"action": "issue", "ok": True, "ticket_len": len(ticket), "ticket_prefix": ticket[:8] + "...", "ms": t_issue})
    except Exception as e:
        steps.append({"action": "issue", "ok": False, "error": str(e)})
        return fail(message=_("签发失败"), code=500, data={"steps": steps})

    # Step 2: 立即验证（模拟 ws_chat 中的 verify_ws_ticket）
    t1 = _t.monotonic()
    try:
        verified_uid = _verify_ticket(ticket)
        t_verify = round((_t.monotonic() - t1) * 1000, 1)
        steps.append({"action": "verify", "ok": verified_uid is not None, "user_id": verified_uid, "ms": t_verify})
    except Exception as e:
        steps.append({"action": "verify", "ok": False, "error": str(e)})

    # Step 3: 再验证一次（应该失败，因为是一次性的）
    t2 = _t.monotonic()
    try:
        uid2 = _verify_ticket(ticket)
        t_reverify = round((_t.monotonic() - t2) * 1000, 1)
        steps.append({"action": "re_verify", "ok": uid2 is not None, "user_id": uid2, "ms": t_reverify,
                       "note": "应该为null(一次性)" if uid2 is None else "⚠️ 重复消费! 不应该发生"})
    except Exception as e:
        steps.append({"action": "re_verify", "ok": False, "error": str(e)})

    # Step 4: Redis 连接状态
    try:
        from core.auth import get_redis
        r = get_redis()
        info = r.info("memory")
        steps.append({"action": "redis_check", "ok": True, "used_memory": info.get("used_memory_human", "?")})
    except Exception as e:
        steps.append({"action": "redis_check", "ok": False, "error": str(e)})

    all_ok = all(s.get("ok") for s in steps)
    return ok(data={
        "all_ok": all_ok,
        "user_id": user_id,
        "ticket_ttl": WS_TICKET_TTL,
        "steps": steps,
        "hint": "如果 all_ok=true 但 WS 仍然失败，问题在 nginx/网关的 Upgrade 转发，不在 ticket 本身" if all_ok else "ticket 链路有问题，检查 Redis 连接和日志",
    })


@router.post("/api/auth/logout", summary="用户登出")
async def user_logout(x_token: Optional[str] = Header(None)):
    """登出：删除 Redis 中的 Token"""
    if x_token:
        delete_token(x_token)
    return {"ok": True}


@router.post("/api/auth/delete-account",
             summary="注销账号",
             description="进入 7 天注销冷静期（account_status=deleted）；期满后再登录将销户并清空 Google/Apple/本地登录标识",
             response_model=dict)
async def delete_account(
    request: Request,
    x_token: Optional[str] = Header(None),
):
    from core.auth import apply_account_status_flags, normalize_account_status
    from services.account_lifecycle import (
        DELETION_GRACE_DAYS,
        schedule_account_deletion,
    )

    user_id = redis_verify_token(x_token) if x_token else None
    al = request.headers.get("accept-language")
    if not user_id:
        raise HTTPException(status_code=401, detail=auth_required_message(al))

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=user_not_found_message(al))
        cur = normalize_account_status(getattr(user, "account_status", None))
        if cur == "closed":
            if x_token:
                delete_token(x_token)
            return {
                "ok": True,
                "message": account_closed_message(al),
                "account_status": "closed",
            }
        if cur == "deleted":
            if x_token:
                delete_token(x_token)
            apply_account_status_flags(user_id, "deleted")
            return {
                "ok": True,
                "message": account_deleted_message(al),
                "account_status": "deleted",
                "grace_days": DELETION_GRACE_DAYS,
            }

        # 冷静期：保留业务数据与登录标识，仅标记注销 + 吊销登录态
        info = schedule_account_deletion(db, user)
        db.commit()

    apply_account_status_flags(user_id, "deleted")
    if x_token:
        delete_token(x_token)

    return {
        "ok": True,
        "message": account_deleted_message(al),
        "account_status": "deleted",
        "status": "deleted",
        "grace_days": DELETION_GRACE_DAYS,
        "deleted_at": info.get("deleted_at"),
        "purge_at": info.get("purge_at"),
    }


@router.post(
    "/api/auth/restore-account",
    summary="解除注销",
    description="冷静期内用原账号密码取消注销并恢复登录；过期已销户不可恢复",
    response_model=dict,
)
async def restore_account(
    request: Request,
    data: LoginRequest,
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    from core.auth import apply_account_status_flags, normalize_account_status
    from core.response import fail, ok
    from services.account_lifecycle import (
        handle_deleted_user_on_login,
        is_grace_expired,
        restore_account_from_deletion,
    )

    lang = normalize_ui_language(accept_language)
    username = _normalize_username(data.username)
    password = data.password

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.username == username).first()
        if not user and "@" in username:
            from sqlalchemy import func

            user = (
                db.query(UserORM)
                .filter(func.lower(UserORM.username) == username)
                .first()
            )
        if not user:
            return fail(message="account_not_found", code=401)
        if not _verify_password(password, user.password_hash):
            return fail(message="wrong_password", code=401)

        st = normalize_account_status(getattr(user, "account_status", None))
        if st == "banned":
            return fail(message="account_banned", code=403)
        if st == "closed":
            return fail(
                message=account_closed_message(accept_language),
                code=403,
                data={"detail": "account_closed", "account_status": "closed"},
            )
        if st != "deleted":
            # 已是正常账号：直接登录
            pass
        else:
            if is_grace_expired(user):
                action, payload = handle_deleted_user_on_login(db, user)
                db.commit()
                apply_account_status_flags(user.id, "closed")
                return fail(
                    message=account_closed_message(accept_language),
                    code=403,
                    data=payload,
                )
            restore_account_from_deletion(db, user)
            db.commit()
            apply_account_status_flags(user.id, "active")

        user_id = user.id
        vip_info = _build_vip_info(db, user_id, lang)
        out_user = {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname,
            "role": user.role or "user",
            "gender": user.gender,
            "sexual_orientation": user.sexual_orientation or "",
            "preferred_gender": user.preferred_gender or "",
            "chat_purpose": user.chat_purpose or "",
            "age": user.age,
            "region": user.region or "",
            "occupation": user.occupation or "",
            "language": getattr(user, "language", None) or "",
            **vip_info,
        }
        from services.credit_service import touch_user_client_context
        from services.version_prompt import persist_app_version_from_request

        touch_user_client_context(
            db,
            user_id,
            ip=_extract_client_ip(request),
            device=_extract_client_device(request),
        )
        persist_app_version_from_request(request, user_id, db=db)
        db.commit()

    token = generate_token(user_id)
    try:
        from services.recommend_ua import after_user_auth
        after_user_auth(user_id, is_new=False)
    except Exception:
        pass
    try:
        from services.growth_attribution import touch_attribution_from_request
        touch_attribution_from_request(
            request, user_id, event_type="login", login_method="restore"
        )
    except Exception:
        pass
    return ok(
        data={"token": token, "user": out_user, "account_status": "active"},
        message=account_restored_message(accept_language),
    )


@router.get("/api/auth/me",
            summary="获取当前用户信息",
            description="根据Token获取当前登录用户的详细信息",
            response_model=dict,
            responses={
                200: {
                    "description": "成功",
                    "content": {
                         "application/json": {
                             "example": {
                                 "id": 1,
                                 "username": "user001",
                                 "nickname": "用户昵称",
                                 "gender": "male",
                                 "age": 25,
                                 "region": "北京",
                                 "occupation": "工程师",
                                 "avatar_url": "https://example.com/avatar.jpg"
                             }
                         }
                    }
                },
                401: {"description": "未登录或Token已过期"}
            })
async def user_me(x_token: Optional[str] = Header(None), accept_language: Optional[str] = Header(None, alias="Accept-Language")):
    lang = normalize_ui_language(accept_language)
    user_id = redis_verify_token(x_token) if x_token else None
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail=auth_required_message(accept_language),
        )

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(
                status_code=404, detail=user_not_found_message(accept_language)
            )

        from services.profile_review import get_latest_reject_reason, get_pending_review, merge_user_view

        pending = get_pending_review(db, user_id)
        view = merge_user_view(user, pending)
        reject_reason = ""
        if not view.get("profile_review_status"):
            reject_reason = get_latest_reject_reason(db, user_id)
        from services.guest_auth import guest_flags

        vip_info = _build_vip_info(db, user_id, lang)
        return {
            "id": user.id,
            "username": user.username,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            **view,
            "language": getattr(user, "language", None) or "",
            "profile_reject_reason": reject_reason,
            **guest_flags(user),
            **vip_info,
        }


@router.patch(
    "/api/auth/me/language",
    summary="更新当前用户语言",
    description="登录后通过 x-token 设置 users.language。消息策略只给语言匹配的用户发送。",
)
async def user_update_language(
    data: UserLanguageRequest,
    x_token: Optional[str] = Header(None),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    from core.response import fail, ok
    from services.companion_content import SUPPORTED_LANGUAGES, canonical_content_language

    user_id = redis_verify_token(x_token) if x_token else None
    if not user_id:
        return fail(message=auth_required_message(accept_language), code=401)

    lang = canonical_content_language(data.language)
    if not lang:
        return fail(
            message="unsupported_language",
            code=400,
            data={"supported": list(SUPPORTED_LANGUAGES)},
        )

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            return fail(message=user_not_found_message(accept_language), code=404)
        user.language = lang
        db.commit()
        db.refresh(user)
        return ok(data={"id": user.id, "language": user.language or lang})


@router.patch("/api/auth/me", summary="更新当前用户信息（提交审核）")
async def user_update_me(
    request: Request,
    data: UserUpdateMeRequest,
    x_token: Optional[str] = Header(None)
):
    """资料修改：性别/年龄/性取向即时生效；名字/地区/职业/头像/背景图进入人工审核。"""
    user_id = redis_verify_token(x_token) if x_token else None
    al = request.headers.get("accept-language")
    if not user_id:
        raise HTTPException(status_code=401, detail=auth_required_message(al))

    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail=no_update_fields_message(al))

    from services.profile_review import submit_profile_review

    try:
        result = submit_profile_review(user_id, update_data)
    except LookupError:
        raise HTTPException(status_code=404, detail=user_not_found_message(al))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("profile review submit failed")
        raise HTTPException(status_code=500, detail=profile_update_failed_message(al))

    return {
        "id": result["id"],
        "username": result["username"],
        "nickname": result.get("nickname", ""),
        "gender": result.get("gender", ""),
        "sexual_orientation": result.get("sexual_orientation", ""),
        "preferred_gender": result.get("preferred_gender", ""),
        "chat_purpose": result.get("chat_purpose", ""),
        "age": result.get("age"),
        "region": result.get("region", ""),
        "occupation": result.get("occupation", ""),
        "avatar_url": result.get("avatar_url", ""),
        "profile_backgrounds": result.get("profile_backgrounds") or [],
        "profile_backgrounds_pending": result.get("profile_backgrounds_pending"),
        "profile_background_review_status": result.get("profile_background_review_status"),
        "profile_review_status": result.get("profile_review_status"),
        "profile_pending": result.get("profile_pending"),
        "pending_review": result.get("profile_review_status") == "pending",
        "unchanged": bool(result.get("unchanged")),
    }


class UserBackgroundsRequest(BaseModel):
    """用户资料背景图。可传 URL 数组或单字符串；服务端最多保留 3 张。"""
    profile_backgrounds: Any = Field(
        ...,
        description="图片 URL 数组，或单张 URL 字符串；空数组表示清空",
        examples=[["https://storage.googleapis.com/trandsbucket/images/bg.jpg"]],
    )


@router.patch("/api/auth/me/profile-backgrounds", summary="更新资料背景图（提交审核）")
async def user_update_profile_backgrounds(
    request: Request,
    data: UserBackgroundsRequest,
    x_token: Optional[str] = Header(None),
):
    """上传/修改背景图：先入待审，通过后才写入公开字段。清空（[]）立即生效。"""
    user_id = redis_verify_token(x_token) if x_token else None
    al = request.headers.get("accept-language")
    if not user_id:
        raise HTTPException(status_code=401, detail=auth_required_message(al))
    from services.profile_review import submit_profile_review

    try:
        result = submit_profile_review(
            user_id, {"profile_backgrounds": data.profile_backgrounds}
        )
    except LookupError:
        raise HTTPException(status_code=404, detail=user_not_found_message(al))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("profile background submit failed")
        raise HTTPException(status_code=500, detail=profile_update_failed_message(al))
    return {
        "id": result["id"],
        "profile_backgrounds": result.get("profile_backgrounds") or [],
        "profile_backgrounds_pending": result.get("profile_backgrounds_pending"),
        "profile_background_review_status": result.get("profile_background_review_status"),
        "profile_review_status": result.get("profile_review_status"),
        "profile_pending": result.get("profile_pending"),
        "pending_review": result.get("profile_review_status") == "pending",
        "unchanged": bool(result.get("unchanged")),
        "cleared": not bool(result.get("profile_backgrounds") or [])
        and not bool(result.get("profile_backgrounds_pending")),
    }


def clear_all_admin_tokens():
    """清空所有管理员 token（如修改密码后调用）"""
    _redis_clear_admin_tokens()


@router.get("/api/users/stats", summary="获取用户统计数据")
async def user_stats(
    request: Request,
    x_token: Optional[str] = Header(None),
):
    """获取当前用户的统计数据（亲密度>5的伴侣数、总对话轮数、陪伴最久天数）"""
    user_id = redis_verify_token(x_token) if x_token else None
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail=auth_required_message(request.headers.get("accept-language")),
        )

    from datetime import datetime, timezone

    with get_db() as db:
        # 获取当前用户与所有未删除智能体的亲密度关系
        user_states = db.query(UserCompanionStateORM).join(
            CompanionORM,
            CompanionORM.id == UserCompanionStateORM.companion_id
        ).filter(
            UserCompanionStateORM.user_id == user_id,
            CompanionORM.is_deleted == False,
        ).all()

        # 统计亲密度>5的伴侣数、总对话轮数、陪伴最久天数
        intimate_companion_count = 0
        total_turns = 0
        max_days_together = 0
        now = datetime.now(timezone.utc)

        for state in user_states:
            affection = state.affection or 0
            turns = state.turns or 0

            # 亲密度>5的伴侣
            if affection > 5:
                intimate_companion_count += 1
                total_turns += turns

                # 获取智能体的创建时间来计算陪伴天数
                companion = db.query(CompanionORM).filter(
                    CompanionORM.id == state.companion_id,
                    CompanionORM.is_deleted == False,
                ).first()
                if companion and companion.created_at:
                    ct = companion.created_at
                    if ct.tzinfo is None:
                        ct = ct.replace(tzinfo=timezone.utc)
                    days = (now - ct).days
                    if days > max_days_together:
                        max_days_together = days

    return {
        "intimate_companion_count": intimate_companion_count,
        "total_turns": total_turns,
        "max_days_together": max_days_together,
    }
