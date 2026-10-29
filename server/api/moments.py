import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request

from core.permissions import IsAuthenticated, IsOwner
from core.dependencies import require_permissions, get_optional_user
from core.i18n import _, companion_deleted_message, companion_not_found_message, http_ui_lang, validation_message
from core.response import fail, ok, paginated
from core.state import get_companion_manager
from services.moments import (
    add_user_comment,
    attach_is_say_hi_to_moments,
    confirm_moment_im,
    count_moments_feed,
    generate_icebreaker,
    get_companion_moments,
    get_moment_comments,
    get_moment_detail,
    get_moments_feed,
    record_moment_stats,
    regenerate_moment_image,
    save_moment_to_history,
    say_hi,
    toggle_like,
)

router = APIRouter(tags=["朋友圈"])


def _get_device_id(x_device_id: Optional[str] = None) -> str:
    """获取设备标识，用于点赞去重（保留，为多设备区分预留）"""
    from core.client_context import normalize_device_id

    return normalize_device_id(x_device_id) or "anonymous"


def _get_user_id(x_token: Optional[str] = None) -> Optional[int]:
    """从 token 获取用户ID，用于点赞去重"""
    if not x_token:
        return None
    try:
        from core.auth import verify_token
        user_id = verify_token(x_token)
        return user_id
    except Exception:
        return None

logger = logging.getLogger(__name__)


def _fetch_moments_list(
    *,
    limit: int,
    offset: int,
    user_id: Optional[int],
    ui_lang: str,
    filter_lang_short: str,
    gender: str,
    orientation: str,
    recommend_debug: Optional[dict] = None,
    device_id: str = "",
) -> tuple[list, int, dict]:
    """获取朋友圈列表及总数（供 /api/moments 与 /api/bmoments 共用）。"""
    debug_info = recommend_debug if recommend_debug is not None else {}
    moments = get_moments_feed(
        limit=limit,
        offset=offset,
        user_id=user_id,
        lang=ui_lang,
        filter_lang=filter_lang_short,
        gender=gender,
        orientation=orientation,
        recommend_debug=debug_info,
    )
    for moment in moments:
        try:
            moment["comments"] = get_moment_comments(
                moment["id"],
                limit=10,
                current_user_id=user_id,
            )
        except Exception as e:
            logger.error("Error getting comments for moment %s: %s", moment.get("id"), e)
            moment["comments"] = []

    total = count_moments_feed(
        filter_lang=filter_lang_short,
        gender=gender or "",
        orientation=orientation or "",
        user_id=user_id,
        lang=ui_lang,
    )
    record_moment_stats(
        [m.get("id") for m in moments],
        kind="view",
        user_id=user_id,
        device_id=device_id,
    )
    attach_is_say_hi_to_moments(moments, user_id)
    return moments, total, debug_info


@router.get("/api/moments",
            summary="获取朋友圈列表",
            description="获取朋友圈动态列表，支持多条件筛选，无需登录",
            response_model=dict,
            responses={
                200: {
                    "description": "成功",
                    "content": {
                        "application/json": {
                            "example": {
                                "moments": [
                                    {
                                        "id": 1,
                                        "companion_id": "comp_001",
                                        "companion_name": "小美",
                                        "content": "今天天气真好！",
                                        "image_url": "https://example.com/img1.jpg",
                                        "likes_count": 12,
                                        "liked": False,
                                        "created_at": "2024-06-21T10:30:00Z"
                                    }
                                ],
                                "total": 100
                            }
                        }
                    }
                }
            })
async def api_list_moments(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        lang: Optional[str] = Query(None),
        filter_lang: Optional[str] = Query(None, description="按智能体资料语种筛选"),
        gender: Optional[str] = Query(None, description="男/女"),
        orientation: Optional[str] = Query(None, description="性取向"),
        x_token: Optional[str] = Header(None),
        x_device_id: Optional[str] = Header(None, alias="X-Device-Id"),
        accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """获取朋友圈列表，包含评论（公开接口，所有人都能看到所有人的朋友圈）"""
    from core.i18n import normalize_ui_language
    from services.prompts.resolve import resolve_lang

    if lang is not None and lang == "":
        lang = None
    if filter_lang is not None and filter_lang == "":
        filter_lang = None

    ui_lang = resolve_lang(query_lang=lang, accept_language=accept_language)
    filter_lang_short = normalize_ui_language(filter_lang) if filter_lang else ""

    try:
        user_id = _get_user_id(x_token)
        logger.info(f"   User ID: {user_id}")
    except Exception as e:
        logger.error(f"❌ Error getting user ID: {e}")
        user_id = None

    try:
        logger.info("📊 Calling get_moments_feed...")
        moments, total, debug_info = _fetch_moments_list(
            limit=limit,
            offset=offset,
            user_id=user_id,
            ui_lang=ui_lang,
            filter_lang_short=filter_lang_short,
            gender=gender or "",
            orientation=orientation or "",
            device_id=_get_device_id(x_device_id),
        )
        logger.info(f"   ✅ Retrieved {len(moments)} moments, total: {total}")

        response = {"moments": moments, "total": total}
        if debug_info.get("fallback_layer") and user_id:
            from core.database import UserORM, get_db as _gdb
            with _gdb() as db:
                u = db.query(UserORM).filter(UserORM.id == user_id).first()
                if u and (u.role or "") in ("admin", "staff"):
                    response["matched_strategy_id"] = debug_info.get("matched_strategy_id")
                    response["fallback_layer"] = debug_info.get("fallback_layer")
        logger.info(f"✅ Success: returning {len(moments)} items, total: {total}")
        logger.info("=" * 60)
        return response

    except Exception as e:
        # ============ 5. 详细错误处理 ============
        logger.error(f"❌❌❌ ERROR in api_list_moments: {e}")
        logger.error(f"   Error type: {type(e).__name__}")
        logger.error(f"   Error args: {e.args}")
        import traceback
        logger.error(f"   Traceback:\n{traceback.format_exc()}")
        logger.info("=" * 60)

        # 返回详细的错误信息（开发环境）
        raise HTTPException(
            status_code=400,
            detail={
                "error": str(e),
                "type": type(e).__name__,
                "params": {
                    "limit": limit,
                    "offset": offset,
                    "lang": lang,
                    "filter_lang": filter_lang,
                    "gender": gender,
                    "orientation": orientation
                }
            }
        )

@router.get(
    "/api/bmoments",
    summary="获取朋友圈列表（标准化分页）",
    description=(
        "与 GET /api/moments 相同筛选能力；使用 page/page_size，返回 response.py 标准分页格式。无需登录。"
        "命中推荐策略时只出匹配智能体（池内轮转穿插）；未命中策略才同语言/其他语言兜底。"
    ),
)
async def api_list_bmoments(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    filter_lang: Optional[str] = Query(None, description="按智能体资料语种筛选"),
    gender: Optional[str] = Query(None, description="男/女"),
    orientation: Optional[str] = Query(None, description="性取向"),
    debug: bool = Query(False, description="管理员日志输出 matched_strategy_id / fallback_layer"),
    x_token: Optional[str] = Header(None, alias="x-token"),
    x_device_id: Optional[str] = Header(None, alias="X-Device-Id"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """标准化朋友圈列表：UI 语言走 Accept-Language，分页走 page/page_size。"""
    from core.i18n import normalize_ui_language
    from services.prompts.resolve import resolve_lang

    if filter_lang is not None and filter_lang == "":
        filter_lang = None

    ui_lang = resolve_lang(query_lang=None, accept_language=accept_language)
    filter_lang_short = normalize_ui_language(filter_lang) if filter_lang else ""

    user_id = _get_user_id(x_token)
    offset = (page - 1) * page_size

    try:
        debug_info = {}
        moments, total, debug_info = _fetch_moments_list(
            limit=page_size,
            offset=offset,
            user_id=user_id,
            ui_lang=ui_lang,
            filter_lang_short=filter_lang_short,
            gender=gender or "",
            orientation=orientation or "",
            recommend_debug=debug_info,
            device_id=_get_device_id(x_device_id),
        )

        if debug and user_id and debug_info.get("fallback_layer"):
            from core.database import UserORM, get_db

            with get_db() as db:
                u = db.query(UserORM).filter(UserORM.id == user_id).first()
                if u and (u.role or "") in ("admin", "staff"):
                    logger.info(
                        "[bmoments] debug matched_strategy_id=%s fallback_layer=%s",
                        debug_info.get("matched_strategy_id"),
                        debug_info.get("fallback_layer"),
                    )

        return paginated(items=moments, total=total, page=page, page_size=page_size)
    except Exception as e:
        logger.error("Error in api_list_bmoments: %s", e, exc_info=True)
        return fail(message=str(e), code=400)


# POST 路由必须在 GET {moment_id} 之前，避免路径冲突
@router.post("/api/moments/{moment_id}/like", summary="点赞/取消点赞")
async def api_toggle_like(
    moment_id: int,
    user_id: int = Depends(require_permissions(IsAuthenticated)),
    x_timezone: Optional[str] = Header(None, alias="x-timezone"),
):
    """点赞或取消点赞 - 需要登录"""
    try:
        from services.outreach import remember_user_timezone

        remember_user_timezone(user_id, x_timezone)
    except Exception:
        pass
    result = toggle_like(moment_id, user_id=user_id, tz_name=x_timezone)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", _("点赞失败")))
    return result


@router.post("/api/moments/{moment_id}/comment", summary="发表评论")
async def api_add_comment(
    moment_id: int,
    x_device_id: Optional[str] = Header(None),
    content: str = Body(..., embed=True),
    parent_id: Optional[int] = Body(None, embed=True),
    user_id: int = Depends(require_permissions(IsAuthenticated)),
    x_timezone: Optional[str] = Header(None, alias="x-timezone"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
    lang: Optional[str] = Query(None),
):
    """用户发表评论或回复评论；通过后调度发帖智能体 IM 私信。"""
    device_id = _get_device_id(x_device_id)
    try:
        from services.outreach import remember_user_timezone

        remember_user_timezone(user_id, x_timezone)
    except Exception:
        pass
    from services.prompts.resolve import resolve_lang

    ui_lang = resolve_lang(query_lang=lang, accept_language=accept_language)
    result = add_user_comment(
        moment_id,
        device_id,
        content,
        parent_id,
        user_id=user_id,
        tz_name=x_timezone,
        ui_lang=ui_lang,
    )
    if not result.get("ok"):
        err = result.get("error") or _("评论失败", lang=ui_lang)
        error_code = result.get("error_code") or ""
        if error_code == "comment_limit_reached":
            # 业务码 460：达上限，前端跳转私聊（勿当普通 400 评论失败）
            used = int(result.get("used") or 0)
            limit = int(result.get("limit") or 0)
            return fail(
                message=err,
                code=int(result.get("biz_code") or 460),
                data={
                    "error_code": error_code,
                    "action": result.get("action") or "go_im",
                    "moment_id": result.get("moment_id") or moment_id,
                    "companion_id": result.get("companion_id") or "",
                    "limit": limit,
                    "used": used,
                    # 本用户在该动态下已成功评论条数（= used，便于前端直接提示）
                    "commented_count": used,
                },
            )
        code = 404 if error_code == "not_found" else 400
        return fail(message=err, code=code)
    payload = {k: v for k, v in result.items() if k != "ok"}
    return ok(data=payload)


@router.post("/api/moments/{moment_id}/regenerate-image", summary="补全朋友圈配图")
async def api_regenerate_moment_image(
    moment_id: int,
    request: Request,
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """智能体自动发圈可能生图失败。已登录用户可对无配图的动态触发生成；已有配图则直接返回，不再生成。"""
    from core.database import MomentORM, get_db
    from services.image_generation import ImageGenerationError
    from services.moments import _image_url_valid

    al = request.headers.get("accept-language")
    with get_db() as db:
        moment = db.query(MomentORM).filter_by(id=moment_id).first()
        if not moment:
            raise HTTPException(status_code=404, detail=validation_message("moment_not_found", al))
        existing = _image_url_valid(moment.image_url)
        if existing:
            return {"ok": True, "image_url": existing, "generated": False}

    try:
        new_url = regenerate_moment_image(moment_id)
    except ImageGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not new_url:
        raise HTTPException(status_code=404, detail=validation_message("moment_not_found", al))
    valid = _image_url_valid(new_url)
    return {"ok": True, "image_url": valid, "generated": bool(valid)}


# GET {moment_id} 必须在所有 POST {moment_id}/xxx 之后
@router.get("/api/moments/{moment_id}", summary="获取朋友圈详情")
async def api_get_moment_detail(
    moment_id: int,
    request: Request,
    x_token: Optional[str] = Header(None),
    x_device_id: Optional[str] = Header(None, alias="X-Device-Id"),
):
    """获取单条朋友圈详情，包含评论"""
    user_id = _get_user_id(x_token)
    moment = get_moment_detail(moment_id, user_id=user_id)
    if not moment:
        raise HTTPException(
            status_code=404,
            detail=validation_message(
                "moment_not_found", request.headers.get("accept-language")
            ),
        )
    moment["comments"] = get_moment_comments(moment_id, limit=100, current_user_id=user_id)
    record_moment_stats(
        [moment_id],
        kind="detail_click",
        user_id=user_id,
        device_id=_get_device_id(x_device_id),
    )
    return moment



# @router.get("/companions/{companion_id}/moments", summary="获取伴侣的所有朋友圈")
@router.get("/api/companions/{companion_id}/moments", summary="获取伴侣的所有朋友圈")
async def api_companion_moments(
    companion_id: str,
    limit: int = Query(20, ge=1, le=100),
    user_id: int = Depends(require_permissions(IsAuthenticated)),
    x_device_id: Optional[str] = Header(None, alias="X-Device-Id"),
):
    """获取某个伴侣的所有朋友圈"""
    from api.companion_access import _assert_companion_visible_for_user

    _assert_companion_visible_for_user(user_id, companion_id)
    moments = get_companion_moments(companion_id, limit=limit, user_id=user_id)
    record_moment_stats(
        [m.get("id") for m in moments],
        kind="view",
        user_id=user_id,
        device_id=_get_device_id(x_device_id),
    )
    return {"moments": moments, "total": len(moments)}


@router.get(
    "/api/companions/{companion_id}/our-moments",
    summary="Our Moments 私密相册",
)
@router.get(
    "/api/companions/{companion_id}/private-album",
    summary="Our Moments 私密相册（别名）",
)
async def api_companion_our_moments(
    companion_id: str,
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    from api.companion_access import _assert_companion_visible_for_user
    from services.private_album import get_user_album

    _assert_companion_visible_for_user(user_id, companion_id)
    return get_user_album(user_id, companion_id)


@router.post("/api/moments/{moment_id}/icebreaker", summary="生成破冰文案")
async def api_generate_icebreaker(
    moment_id: int,
    request: Request,
    lang: Optional[str] = Query(None),
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """根据朋友圈内容生成破冰文案，用于首次对话时的开场白

    - 首次（该用户×伴侣无任何会话记录，含 moment）：生成一次，并写入 moment 历史
    - 已有聊天记录（含朋友圈详情）：{is_first:false, icebreaker:null}
    - 仅朋友圈 id 不存在时 404
    """
    al = request.headers.get("accept-language")
    result = generate_icebreaker(
        moment_id, user_id=user_id, lang=http_ui_lang(accept_language=al, lang=lang)
    )
    if result is None:
        raise HTTPException(status_code=404, detail=validation_message("moment_not_found", al))
    if result.get("companion_deleted"):
        raise HTTPException(status_code=403, detail=companion_deleted_message(al))

    # 首次：把朋友圈详情写入历史（便于聊天页展示），与文案是否生成成功无关
    if result.get("is_first"):
        save_moment_to_history(moment_id, user_id)

    return result


@router.post("/api/moments/{moment_id}/confirm-im", summary="评论达上限确认私聊")
async def api_confirm_moment_im(
    moment_id: int,
    request: Request,
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """评论次数达上限后，用户确认进入私聊时调用。

    业务主体是朋友圈（非智能体）：
    - 本条动态尚未写入过聊天 → 追加一条 role=moment（文案/图片/点赞）
    - 本条动态已写入过 → 跳过（already=true），不同动态互不影响、可多次触发
    - 不生成破冰文案；未发送的评论文案由前端自行填入输入框
    """
    al = request.headers.get("accept-language")
    result = confirm_moment_im(moment_id, user_id=user_id)
    if result is None:
        raise HTTPException(status_code=404, detail=validation_message("moment_not_found", al))
    if result.get("companion_deleted"):
        raise HTTPException(status_code=403, detail=companion_deleted_message(al))
    if result.get("companion_hidden"):
        raise HTTPException(status_code=404, detail=companion_not_found_message(al))
    return result


@router.post("/api/moments/{moment_id}/say_hi", summary="打招呼并写入聊天")
async def api_say_hi(
    moment_id: int,
    request: Request,
    lang: Optional[str] = Query(None),
    use_chat_free: bool = Query(
        False,
        description="今日 Say Hi 免费用尽后，用户确认「继续 sayhi」再扣聊天免费次数",
    ),
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """按后台文案库随机抽一条打招呼，写入聊天并按正常对话扣费，后台触发智能体回复。

    兼容旧客户端：用朋友圈 id 找到 companion 后再打招呼，**不依赖朋友圈内容**。
    机器人 Tab / 无动态的智能体请改用 `POST /api/companions/{companion_id}/say_hi`。
    """
    from services.say_hi_reply import (
        enrich_say_hi_response,
        say_hi_quota_blocked_response,
        schedule_say_hi_assistant_reply,
    )

    al = request.headers.get("accept-language")
    result = say_hi(
        moment_id,
        user_id=user_id,
        lang=http_ui_lang(accept_language=al, lang=lang),
        use_chat_free=bool(use_chat_free),
    )
    if result is None:
        raise HTTPException(status_code=404, detail=validation_message("moment_not_found", al))
    if result.get("companion_deleted"):
        raise HTTPException(status_code=403, detail=companion_deleted_message(al))
    if result.get("companion_hidden"):
        raise HTTPException(status_code=403, detail=companion_deleted_message(al))
    charge_err = result.pop("charge_error", None)
    if charge_err:
        return say_hi_quota_blocked_response(
            user_id=user_id,
            companion_id=str(result.get("companion_id") or ""),
            charge_detail=charge_err.get("detail"),
        )
    charge_meta = result.pop("charge_meta", None)
    if (
        result.get("is_say_hi")
        and not result.get("already")
        and not result.get("need_confirm_use_chat_free")
        and (result.get("message") or "").strip()
    ):
        schedule_say_hi_assistant_reply(
            user_id=user_id,
            companion_id=str(result.get("companion_id") or ""),
            user_text=str(result.get("message") or ""),
            charge_meta=charge_meta,
        )
    return enrich_say_hi_response(
        result, user_id=user_id, charge_meta=charge_meta
    )
