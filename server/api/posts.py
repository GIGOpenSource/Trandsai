import os
import uuid
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, File, Header, HTTPException, Query, UploadFile
from sqlalchemy import func

from core.config import BASE_DIR
from core.database import PostORM, get_db
from core.rest_async import run_rest
from services.cos_storage import is_cos_enabled, upload_bytes_to_cos
from services.thumbnails import derive_thumb_url, save_thumbnail_pair
from services.posts import (
    add_post_comment,
    create_post,
    delete_post,
    get_my_posts,
    get_post_comments,
    get_post_detail,
    get_posts_feed,
    search_posts,
    toggle_post_like,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_device_id(x_device_id: Optional[str] = None) -> str:
    return x_device_id or "anonymous"


def _get_user_from_token(x_token: Optional[str] = None) -> Optional[dict]:
    if not x_token:
        return None
    try:
        from api.auth import verify_user_token
        user_id = verify_user_token(x_token)
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


@router.get("/api/posts")
async def api_list_posts(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    category: Optional[str] = Query(None),
    x_device_id: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None),
):
    """获取帖子列表，支持按分类筛选"""
    device_id = _get_device_id(x_device_id)
    user = _get_user_from_token(x_token)

    def _load():
        posts = get_posts_feed(
            limit=limit,
            offset=offset,
            device_id=device_id,
            user_id=user["id"] if user else None,
            category=category,
        )
        with get_db() as db:
            query = db.query(func.count(PostORM.id))
            if category:
                query = query.filter(PostORM.category == category)
            total = query.scalar() or 0
        return {"posts": posts, "total": total}

    return await run_rest(_load)


@router.get("/api/posts/search")
async def api_search_posts(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    x_device_id: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None),
):
    """搜索帖子（标题或内容匹配）"""
    device_id = _get_device_id(x_device_id)
    user = _get_user_from_token(x_token)

    def _load():
        posts = search_posts(
            query=q,
            limit=limit,
            offset=offset,
            device_id=device_id,
            user_id=user["id"] if user else None,
        )
        with get_db() as db:
            search_pattern = f"%{q}%"
            total = (
                db.query(func.count(PostORM.id))
                .filter(
                    PostORM.title.ilike(search_pattern)
                    | PostORM.content.ilike(search_pattern)
                )
                .scalar()
                or 0
            )
        return {"posts": posts, "total": total}

    return await run_rest(_load)


@router.get("/api/posts/my")
async def api_list_my_posts(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    x_token: Optional[str] = Header(None),
):
    """获取当前登录用户发布的帖子列表"""
    user = _get_user_from_token(x_token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")

    def _load():
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

    return await run_rest(_load)


@router.post("/api/posts")
async def api_create_post(
    data: dict = Body(...),
    x_device_id: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None),
):
    """创建新帖子"""
    device_id = _get_device_id(x_device_id)
    user = _get_user_from_token(x_token)

    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    images = data.get("images", []) or []
    category = data.get("category", "").strip()

    user_id = user["id"] if user else None
    user_name = user.get("nickname") or user.get("username") if user else f"用户_{device_id[:6]}"

    result = create_post(
        title=title,
        content=content,
        user_id=user_id,
        user_name=user_name,
        images=images,
        category=category,
    )
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "创建失败"))
    return result


@router.get("/api/posts/{post_id}")
async def api_get_post_detail(
    post_id: int,
    x_device_id: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None),
):
    """获取帖子详情"""
    post = get_post_detail(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="帖子不存在")
    device_id = _get_device_id(x_device_id)
    user = _get_user_from_token(x_token)
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


@router.post("/api/posts/{post_id}/like")
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


@router.post("/api/posts/{post_id}/comment")
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
        raise HTTPException(status_code=400, detail=result.get("error", "评论失败"))
    return result


@router.delete("/api/posts/{post_id}")
async def api_delete_post(
    post_id: int,
    x_token: Optional[str] = Header(None),
):
    """删除帖子（仅帖子作者可删除）"""
    user = _get_user_from_token(x_token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")

    from core.database import PostORM, get_db
    with get_db() as db:
        post = db.query(PostORM).filter_by(id=post_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="帖子不存在")
        if post.user_id != user["id"]:
            raise HTTPException(status_code=403, detail="无权删除此帖子")

    ok = delete_post(post_id)
    if not ok:
        raise HTTPException(status_code=500, detail="删除失败")
    return {"ok": True}


# ===== 图片上传 =====

@router.post("/api/upload/image")
async def api_upload_image(
    file: UploadFile = File(...),
):
    """上传图片，返回可访问的 URL"""
    ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    MAX_SIZE = 5 * 1024 * 1024  # 5MB

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="仅支持 jpeg/png/gif/webp 图片")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="图片大小不能超过 5MB")

    ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(file.content_type, ".jpg")

    filename = f"{uuid.uuid4().hex}{ext}"
    image_dir = Path(BASE_DIR) / "data" / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    file_path = image_dir / filename

    with open(file_path, "wb") as f:
        f.write(content)

    thumb_path, thumb_bytes = save_thumbnail_pair(content, image_dir, filename)

    # 尝试上传到 COS（原图 + 缩略图）
    cos_key = f"images/{filename}"
    cos_url = upload_bytes_to_cos(content, cos_key)
    thumb_url: Optional[str] = None
    if thumb_bytes is not None:
        thumb_name = thumb_path.name if thumb_path else f"{Path(filename).stem}_thumb.webp"
        if cos_url:
            thumb_cos = upload_bytes_to_cos(thumb_bytes, f"images/{thumb_name}")
            thumb_url = thumb_cos or derive_thumb_url(cos_url)
        else:
            thumb_url = f"/data/images/{thumb_name}"

    if cos_url:
        return {"ok": True, "url": cos_url, "thumb_url": thumb_url or derive_thumb_url(cos_url)}

    local_url = f"/data/images/{filename}"
    return {
        "ok": True,
        "url": local_url,
        "thumb_url": thumb_url or derive_thumb_url(local_url),
    }


@router.get("/api/media/thumb")
async def api_media_thumb(
    src: str = Query(..., min_length=1, max_length=500),
    w: int = Query(400, ge=64, le=800),
):
    """
    按需生成/返回缩略图（兼容历史无 thumb 的图片）。
    仅允许本站 /data/images/ 路径，防止 SSRF。
    """
    from fastapi.responses import FileResponse, RedirectResponse
    from services.thumbnails import make_thumbnail_bytes, thumb_stem_name

    # 只接受站内相对路径
    path = src.strip()
    if path.startswith("http"):
        from urllib.parse import urlparse

        parsed = urlparse(path)
        path = parsed.path or ""
    if not path.startswith("/data/images/"):
        raise HTTPException(status_code=400, detail="仅支持 /data/images/ 资源")

    name = Path(path).name
    if ".." in name or "/" in name:
        raise HTTPException(status_code=400, detail="非法路径")

    image_dir = Path(BASE_DIR) / "data" / "images"
    original = image_dir / name
    if not original.is_file():
        raise HTTPException(status_code=404, detail="原图不存在")

    thumb_name = thumb_stem_name(name)
    thumb_file = image_dir / thumb_name
    if thumb_file.is_file():
        return FileResponse(thumb_file, media_type="image/webp")

    try:
        content = original.read_bytes()
        thumb_bytes = make_thumbnail_bytes(content, max_edge=w)
        if not thumb_bytes:
            return RedirectResponse(url=path)
        thumb_file.write_bytes(thumb_bytes)
        return FileResponse(thumb_file, media_type="image/webp")
    except Exception as e:
        logger.warning("按需缩略图失败 %s: %s", name, e)
        return RedirectResponse(url=path)
