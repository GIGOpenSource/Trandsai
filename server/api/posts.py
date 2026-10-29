import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Query, Request, UploadFile
from sqlalchemy import func

from core.database import PostORM, get_db
from core.i18n import (
    _,
    auth_required_message,
    delete_failed_message,
    forbidden_message,
    normalize_ui_language,
    post_not_found_message,
    validation_message,
)
from core.response import fail, paginated
from services.cos_storage import is_cos_enabled, upload_bytes_to_cos
from services.posts import (
    add_post_comment,
    count_approved_posts,
    create_post,
    delete_post,
    get_my_posts,
    get_post_comments,
    get_post_detail,
    get_posts_feed,
    search_posts,
    toggle_post_like,
)

router = APIRouter(tags=["社区"])
logger = logging.getLogger(__name__)


def _get_device_id(x_device_id: Optional[str] = None) -> str:
    from core.client_context import normalize_device_id

    return normalize_device_id(x_device_id) or "anonymous"


def _request_lang(request: Request) -> str:
    raw = request.headers.get("accept-language")
    return normalize_ui_language(raw)


def _get_user_from_token(x_token: Optional[str] = None) -> Optional[dict]:
    if not x_token:
        return None
    try:
        from core.auth import verify_token as redis_verify_token
        user_id = redis_verify_token(x_token)
        if not user_id:
            return None
        from core.database import UserORM, get_db
        with get_db() as db:
            user = db.query(UserORM).filter_by(id=user_id).first()
            if user:
                return {
                    "id": user.id,
                    "username": user.username,
                    "nickname": user.nickname,
                }
    except Exception as e:
        logger.warning("Failed to resolve user from token: %s", e)
    return None


def _extract_request_token(
    request: Request,
    x_token: Optional[str] = None,
    authorization: Optional[str] = None,
) -> str:
    """x-token 优先，其次 Authorization Bearer。"""
    token = (x_token or "").strip()
    if token:
        return token
    auth = (authorization or request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    hdr = (request.headers.get("x-token") or "").strip()
    return hdr


def _resolve_upload_actor(
    request: Request,
    x_token: Optional[str] = None,
    authorization: Optional[str] = None,
) -> Optional[dict]:
    """用户 token 或管理端 token 均可上传图片。"""
    token = _extract_request_token(request, x_token, authorization)
    if not token:
        return None
    user = _get_user_from_token(token)
    if user and user.get("id") is not None:
        return {**user, "actor": "user"}
    try:
        from core.auth import get_admin_token_payload, verify_admin_token

        if not verify_admin_token(token):
            return None
        payload = get_admin_token_payload(token) or {}
        admin_uid = payload.get("user_id")
        return {
            "id": int(admin_uid) if admin_uid is not None else 0,
            "username": (payload.get("username") or "admin"),
            "nickname": (payload.get("username") or "admin"),
            "actor": "admin",
            "role_key": payload.get("role_key") or "admin",
        }
    except Exception as e:
        logger.warning("Failed to resolve admin from token: %s", e)
    return None


@router.get("/api/posts", summary="获取帖子列表")
async def api_list_posts(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    category: Optional[str] = Query(None),
    x_device_id: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None),
):
    """获取帖子列表，支持按分类筛选"""
    device_id = _get_device_id(x_device_id)
    user = _get_user_from_token(x_token)
    posts = get_posts_feed(
        limit=limit,
        offset=offset,
        device_id=device_id,
        user_id=user["id"] if user else None,
        category=category,
        language=_request_lang(request),
    )
    total = count_approved_posts(category=category, language=_request_lang(request))
    return {"posts": posts, "total": total}


@router.get("/api/posts/search", summary="搜索帖子")
async def api_search_posts(
    request: Request,
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    x_device_id: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None),
):
    """搜索帖子（标题或内容匹配）"""
    device_id = _get_device_id(x_device_id)
    user = _get_user_from_token(x_token)
    posts = search_posts(
        query=q,
        limit=limit,
        offset=offset,
        device_id=device_id,
        user_id=user["id"] if user else None,
        language=_request_lang(request),
    )
    total = count_approved_posts(search=q, language=_request_lang(request))
    return {"posts": posts, "total": total}


@router.get("/api/posts/my", summary="获取我的帖子")
async def api_list_my_posts(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    x_token: Optional[str] = Header(None),
):
    """获取当前登录用户发布的帖子列表"""
    user = _get_user_from_token(x_token)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=auth_required_message(request.headers.get("accept-language")),
        )
    posts = get_my_posts(
        user_id=user["id"],
        limit=limit,
        offset=offset,
    )
    with get_db() as db:
        total = (
            db.query(func.count(PostORM.id))
            .filter(PostORM.user_id == user["id"])
            .scalar()
            or 0
        )
    return {"posts": posts, "total": total}


@router.get(
    "/api/bposts",
    summary="获取帖子列表（标准化分页）",
    description="与 GET /api/posts 相同筛选能力；使用 page/page_size，返回 response.py 标准分页格式。",
)
async def api_list_bposts(
    request: Request,
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    category: Optional[str] = Query(None),
    x_device_id: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    """标准化帖子列表：分页走 page/page_size，语言走 Accept-Language。"""
    device_id = _get_device_id(x_device_id)
    user = _get_user_from_token(x_token)
    ui_lang = _request_lang(request)
    offset = (page - 1) * page_size

    posts = get_posts_feed(
        limit=page_size,
        offset=offset,
        device_id=device_id,
        user_id=user["id"] if user else None,
        category=category,
        language=ui_lang,
    )
    total = count_approved_posts(category=category, language=ui_lang)
    return paginated(items=posts, total=total, page=page, page_size=page_size)


@router.get(
    "/api/bposts/my",
    summary="获取我的帖子（标准化分页）",
    description="与 GET /api/posts/my 相同；使用 page/page_size，返回 response.py 标准分页格式。",
)
async def api_list_my_bposts(
    request: Request,
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    x_token: Optional[str] = Header(None, alias="x-token"),
):
    """标准化我的帖子列表；必须登录。"""
    user = _get_user_from_token(x_token)
    if not user:
        return fail(
            message=auth_required_message(request.headers.get("accept-language")),
            code=401,
        )

    offset = (page - 1) * page_size
    posts = get_my_posts(
        user_id=user["id"],
        limit=page_size,
        offset=offset,
    )
    with get_db() as db:
        total = (
            db.query(func.count(PostORM.id))
            .filter(PostORM.user_id == user["id"])
            .scalar()
            or 0
        )
    return paginated(items=posts, total=total, page=page, page_size=page_size)


@router.post("/api/posts",
             summary="创建帖子",
             description="创建新的社区帖子，支持上传图片",
             response_model=dict,
             responses={
                 200: {
                     "description": "创建成功",
                     "content": {
                         "application/json": {
                             "example": {
                                 "ok": True,
                                 "post_id": 1,
                                 "title": "帖子标题",
                                 "content": "帖子内容",
                                 "user_name": "用户昵称"
                             }
                         }
                     }
                 },
                 400: {"description": "参数错误"}
             })
async def api_create_post(
        request: Request,
        data: dict = Body(...),
        x_device_id: Optional[str] = Header(None),
        x_token: Optional[str] = Header(None),
):
    """创建新帖子（必须登录）"""
    device_id = _get_device_id(x_device_id)
    user = _get_user_from_token(x_token)
    if not user or not user.get("id"):
        raise HTTPException(
            status_code=401,
            detail=auth_required_message(request.headers.get("accept-language")),
        )

    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    images = data.get("images", []) or []
    category = data.get("category", "").strip()

    user_id = user["id"]
    user_name = user.get("nickname") or user.get("username") or f"用户_{device_id[:6]}"

    result = create_post(
        title=title,
        content=content,
        user_id=user_id,
        user_name=user_name,
        images=images,
        category=category,
        language=_request_lang(request),
    )
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", _("创建失败")))
    return result


@router.get("/api/posts/{post_id}", summary="获取帖子详情")
async def api_get_post_detail(
    post_id: int,
    request: Request,
    x_device_id: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None),
):
    """获取帖子详情"""
    device_id = _get_device_id(x_device_id)
    user = _get_user_from_token(x_token)
    post = get_post_detail(post_id, viewer_user_id=user["id"] if user else None)
    if not post:
        raise HTTPException(
            status_code=404,
            detail=post_not_found_message(request.headers.get("accept-language")),
        )
    from core.database import PostLikeORM, get_db
    with get_db() as db:
        liked = False
        if user and user.get("id"):
            liked = (
                db.query(PostLikeORM)
                .filter_by(post_id=post_id, user_id=user["id"])
                .first()
                is not None
            )
        elif device_id:
            liked = (
                db.query(PostLikeORM)
                .filter_by(post_id=post_id, device_id=device_id)
                .first()
                is not None
            )
        post["liked"] = liked
    post["comments"] = get_post_comments(post_id, limit=50)
    return post


@router.post("/api/posts/{post_id}/like", summary="点赞/取消点赞1")
async def api_toggle_post_like(
    post_id: int,
    x_device_id: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None),
):
    """点赞或取消点赞帖子"""
    device_id = _get_device_id(x_device_id)
    user = _get_user_from_token(x_token)
    result = toggle_post_like(
        post_id=post_id,
        device_id=device_id,
        user_id=user["id"] if user else None,
    )
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result.get("error", "Not found"))
    return result


@router.post("/api/posts/{post_id}/comment", summary="发表评论")
async def api_add_post_comment(
    post_id: int,
    data: dict = Body(...),
    x_device_id: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None),
):
    """发表评论"""
    device_id = _get_device_id(x_device_id)
    user = _get_user_from_token(x_token)
    content = data.get("content", "").strip()

    user_id = user["id"] if user else None
    user_name = user.get("nickname") or user.get("username") if user else f"用户_{device_id[:6]}"

    result = add_post_comment(
        post_id=post_id,
        content=content,
        device_id=device_id,
        user_id=user_id,
        user_name=user_name,
    )
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", _("评论失败")))
    return result


@router.delete("/api/posts/{post_id}", summary="删除帖子")
async def api_delete_post(
    post_id: int,
    request: Request,
    x_token: Optional[str] = Header(None),
):
    """删除帖子（仅帖子作者可删除）"""
    user = _get_user_from_token(x_token)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=auth_required_message(request.headers.get("accept-language")),
        )

    from core.database import PostORM, get_db
    with get_db() as db:
        post = db.query(PostORM).filter_by(id=post_id).first()
        if not post:
            raise HTTPException(
                status_code=404,
                detail=post_not_found_message(request.headers.get("accept-language")),
            )
        if post.user_id != user["id"]:
            raise HTTPException(
                status_code=403,
                detail=forbidden_message(request.headers.get("accept-language")),
            )

    ok = delete_post(post_id)
    if not ok:
        raise HTTPException(
            status_code=500,
            detail=delete_failed_message(request.headers.get("accept-language")),
        )
    return {"ok": True}


# ===== 图片上传 =====

@router.post("/api/upload/image",
             summary="上传图片",
             description="上传图片（jpeg/png/gif/webp/heic）。可选 quality（1-100）强制压缩；可选 folder 指定 COS 目录，默认 images。",
             response_model=dict,
             responses={
                 200: {
                     "description": "上传成功",
                     "content": {
                         "application/json": {
                             "example": {
                                 "ok": True,
                                 "url": "https://cdn.example.com/images/abc123.jpg",
                                 "folder": "images"
                             }
                         }
                     }
                 },
                 400: {"description": "文件类型不支持或文件过大"},
                 500: {"description": "COS未配置或上传失败"}
             })
async def api_upload_image(
        request: Request,
        file: UploadFile = File(...),
        quality: Optional[int] = Form(None, description="JPEG 压缩质量 1-100；不传则沿用原逻辑"),
        folder: Optional[str] = Form(None, description="COS 目录，如 moments；不传为 images"),
        max_edge: Optional[int] = Form(None, description="最长边像素 320-4096；不传为默认 2560"),
        x_token: Optional[str] = Header(None, alias="x-token"),
        authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """上传图片到COS。用户端 / 管理端 token 均可（x-token 或 Bearer）。"""
    al = request.headers.get("accept-language")
    actor = _resolve_upload_actor(request, x_token, authorization)
    if not actor:
        return fail(message=auth_required_message(al), code=401)

    from services.thumbnails import (
        UPLOAD_JPEG_QUALITY,
        UPLOAD_MAX_EDGE,
        normalize_upload_folder,
        parse_upload_max_edge,
        parse_upload_quality,
        prepare_upload_image,
        sniff_image_mime,
    )

    # 接收上限放宽，便于多张「原图」；入库前会压到 ≤10MB
    RAW_MAX_SIZE = 40 * 1024 * 1024
    ALLOWED_TYPES = {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/heic",
        "image/heif",
        "application/octet-stream",  # 部分客户端不传正确 MIME，靠文件头识别
    }

    try:
        quality_n = parse_upload_quality(quality)
        folder_name = normalize_upload_folder(folder)
        edge_n = parse_upload_max_edge(max_edge)
    except ValueError as e:
        key = str(e) or "invalid_param"
        return fail(message=key, code=400, data={"reason": key})

    declared = (file.content_type or "").split(";")[0].strip().lower()
    content = await file.read()
    if not content:
        return fail(
            message=validation_message("image_empty", al),
            code=400,
            data={"reason": "image_empty"},
        )

    if len(content) > RAW_MAX_SIZE:
        size_mb = round(len(content) / (1024 * 1024), 1)
        return fail(
            message=f"{validation_message('image_too_large', al)} ({size_mb}MB)",
            code=400,
            data={"reason": "image_too_large", "size_bytes": len(content), "max_bytes": RAW_MAX_SIZE},
        )

    sniffed = sniff_image_mime(content)
    effective_type = sniffed or declared
    if declared not in ALLOWED_TYPES and not sniffed:
        return fail(
            message=validation_message("image_type_not_allowed", al),
            code=400,
            data={"reason": "image_type_not_allowed", "content_type": declared or ""},
        )
    if sniffed and sniffed not in {
        "image/jpeg", "image/png", "image/gif", "image/webp", "image/heic",
    }:
        return fail(
            message=validation_message("image_type_not_allowed", al),
            code=400,
            data={"reason": "image_type_not_allowed", "content_type": sniffed},
        )

    prepared, prepare_reason = prepare_upload_image(
        content,
        effective_type or declared,
        max_edge=edge_n or UPLOAD_MAX_EDGE,
        quality=quality_n if quality_n is not None else UPLOAD_JPEG_QUALITY,
        force_compress=quality_n is not None,
    )
    if not prepared:
        reason = prepare_reason or "image_decode_failed"
        return fail(
            message=validation_message(reason, al),
            code=400,
            data={"reason": reason, "content_type": effective_type or declared or ""},
        )
    content, _mime, ext = prepared

    filename = f"{uuid.uuid4().hex}{ext}"
    cos_key = f"{folder_name}/{filename}"
    if not is_cos_enabled():
        return fail(
            message=validation_message("image_storage_not_configured", al),
            code=500,
            data={"reason": "image_storage_not_configured"},
        )
    cos_url = upload_bytes_to_cos(content, cos_key)
    if cos_url:
        return {"ok": True, "url": cos_url, "folder": folder_name}

    return fail(
        message=validation_message("image_storage_upload_failed", al),
        code=500,
        data={"reason": "image_storage_upload_failed"},
    )
