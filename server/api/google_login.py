import logging

from typing import Optional

from fastapi import APIRouter, Body, Header, HTTPException, Request

from core.app_key import app_key_from_headers
from core.client_context import extract_client_device, extract_client_ip
from core.database import get_db
from core.auth import generate_token, verify_token
from core.response import ok, fail
from core.i18n import validation_message
from services.oauth_users import find_or_create_oauth_user, oauth_user_payload

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Google登录"])


@router.post(
    "/api/auth/googlelogin",
    summary="Google 第三方登录",
    description="支持 openId/unionid 直接登录，前端传递用户信息",
    response_model=dict,
    responses={
        200: {"description": "登录成功"},
        400: {"description": "缺少必要参数"},
        401: {"description": "Google token 无效"},
    },
)
async def google_login(
    request: Request,
    body: dict = Body(...),
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    al = request.headers.get("accept-language")
    client_ip = request.client.host if request.client else "unknown"
    app_key = app_key_from_headers(request.headers)

    # 直接从 body 取值，不校验 Google token
    google_user_id = (
        body.get("openId")
        or body.get("openid")
        or body.get("unionid")
        or body.get("google_user_id")
        or ""
    ).strip()
    email = (body.get("email") or "").strip()
    name = (body.get("name") or body.get("nickname") or "").strip()
    avatar = (body.get("avatar") or body.get("avatar_url") or body.get("picture") or "").strip()

    # 若当前是快速登录 token，则把 Google 贴到该临时账号上
    bind_guest_user_id = verify_token(x_token) if x_token else None

    logger.info(
        "[GoogleLogin] ip=%s app_key=%s openId=%s email=%s name=%s bind_guest=%s",
        client_ip,
        app_key,
        google_user_id[:20],
        email,
        name,
        bind_guest_user_id,
    )

    if not google_user_id:
        logger.warning("[GoogleLogin] FAIL: openId is empty")
        return fail(message=validation_message("missing_id_token", al), code=400)

    want_restore = bool(body.get("restore") or body.get("cancel_deletion"))
    restored = False
    with get_db() as db:
        try:
            user, is_new_user = find_or_create_oauth_user(
                db,
                platform="google",
                provider_user_id=google_user_id,
                email=email,
                email_verified=bool(email),
                nickname=name,
                avatar_url=avatar,
                accept_language=al,
                app_key=app_key,
                bind_guest_user_id=bind_guest_user_id,
            )
        except HTTPException as e:
            return fail(message=str(e.detail), code=e.status_code)

        from core.auth import apply_account_status_flags, normalize_account_status
        from core.i18n import (
            account_closed_message,
            account_pending_deletion_message,
            account_restored_message,
        )
        from services.account_lifecycle import (
            handle_deleted_user_on_login,
            is_grace_expired,
            restore_account_from_deletion,
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
            device=extract_client_device(request),
        )
        from services.version_prompt import persist_app_version_from_request

        persist_app_version_from_request(request, user.id, db=db)
        db.commit()

        user_id = user.id
        out_user = oauth_user_payload(db, user)

    token = generate_token(user_id)
    try:
        from services.recommend_ua import after_user_auth
        after_user_auth(user_id, is_new=is_new_user)
    except Exception:
        pass
    try:
        from services.growth_attribution import touch_attribution_from_request
        touch_attribution_from_request(
            request,
            user_id,
            event_type="register" if is_new_user else "login",
            login_method="google",
        )
    except Exception:
        pass
    logger.info("========== Google Login SUCCESS ==========")
    logger.info(
        "[GoogleLogin] user_id: %s, is_new: %s, app_key: %s",
        user_id,
        is_new_user,
        app_key,
    )
    logger.info("[GoogleLogin] token: %s...", token[:30] if token else "EMPTY")
    logger.info("[GoogleLogin] user: %s", out_user)
    msg = account_restored_message(al) if restored else "登录成功"
    return ok(data={"token": token, "user": out_user}, message=msg)
