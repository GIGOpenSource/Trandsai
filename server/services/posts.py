from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import String, asc, cast, desc, func, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from core.database import PostORM, PostCommentORM, PostLikeORM, UserORM, get_db, serialize_datetime
from core.i18n import _, localize_fallback_user_name


def _normalize_images(images) -> List[str]:
    if not images:
        return []
    if isinstance(images, str):
        s = images.strip()
        return [s] if s else []
    out: List[str] = []
    for x in images:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def _coerce_positive_int(raw: Any) -> Optional[int]:
    """管理端筛选项：兼容 int / '91' / '91.0' / InputNumber 串成的 '91,91'。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 1 else None
    if isinstance(raw, float):
        if not raw.is_integer() or raw < 1:
            return None
        return int(raw)
    s = str(raw).strip()
    if not s or s.lower() in ("none", "null", "undefined"):
        return None
    s = s.replace("，", ",").split(",")[0].strip()
    try:
        n = int(float(s))
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _apply_admin_user_query(q, raw: Any):
    """内容管理「用户 ID」：只按作者 user_id 精确匹配（兼容 16 / 16.0）。

    昵称走独立的 user_nickname；帖子 ID 不是这个筛选项。
    """
    uid = _coerce_positive_int(raw)
    if uid is not None:
        return q.filter(PostORM.user_id == uid)
    if raw is None or isinstance(raw, bool):
        return q
    s = str(raw).strip()
    if not s or s.lower() in ("none", "null", "undefined"):
        return q
    # 填了但不是合法数字 ID：空结果，避免退回未筛选列表
    return q.filter(PostORM.user_id == -1)


def _parse_date_bound(s: Optional[str], *, end: bool = False) -> Optional[datetime]:
    if not s:
        return None
    raw = str(s).strip()
    try:
        if len(raw) >= 19 and ("T" in raw or " " in raw):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        day = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end:
            return day.replace(hour=23, minute=59, second=59)
        return day
    except ValueError:
        return None


def _images_array_length():
    return func.coalesce(func.jsonb_array_length(cast(PostORM.images, JSONB)), 0)


def _post_admin_dict(p: PostORM, u: Optional[UserORM] = None, *, comments_count: Optional[int] = None) -> dict:
    status = getattr(p, "review_status", None) or "pending"
    return {
        "id": p.id,
        "user_id": p.user_id,
        "username": (u.username if u else "") or "",
        "user_name": localize_fallback_user_name(
            ((u.nickname if u else "") or p.user_name or "")
        ),
        "avatar": ((u.avatar_url if u else "") or "") or "",
        "title": p.title,
        "content": p.content,
        "images": p.images or [],
        "category": p.category or "",
        "language": p.language or "zh",
        "likes_count": int(p.likes_count or 0),
        "comments_count": int(
            comments_count if comments_count is not None else (p.comments_count or 0)
        ),
        "review_status": status,
        "status": status,  # 兼容审核列表旧字段
        "reject_reason": getattr(p, "reject_reason", None) or "",
        "reject_reason_id": getattr(p, "reject_reason_id", None),
        "reviewed_by": getattr(p, "reviewed_by", None) or "",
        "reviewed_at": serialize_datetime(getattr(p, "reviewed_at", None)),
        "created_at": serialize_datetime(p.created_at),
    }


def _comment_admin_dict(c: PostCommentORM, nickname: Optional[str], avatar_url: Optional[str]) -> dict:
    return {
        "id": c.id,
        "post_id": c.post_id,
        "user_id": c.user_id,
        "user_name": localize_fallback_user_name(nickname or c.user_name),
        "avatar": avatar_url or "",
        "content": c.content,
        "created_at": serialize_datetime(c.created_at),
    }


def _comment_counts_by_post(db, post_ids: list) -> dict:
    if not post_ids:
        return {}
    rows = (
        db.query(PostCommentORM.post_id, func.count(PostCommentORM.id))
        .filter(PostCommentORM.post_id.in_(post_ids))
        .group_by(PostCommentORM.post_id)
        .all()
    )
    return {pid: int(cnt) for pid, cnt in rows}


def create_post(
    title: str,
    content: str,
    user_id: Optional[int] = None,
    user_name: str = "匿名用户",
    avatar: str = "",
    images: Optional[List[str]] = None,
    category: str = "",
    language: str = "zh",
    review_status: str = "pending",
    reviewed_by: str = "",
    reviewed_at: Optional[datetime] = None,
) -> dict:
    """创建新帖子（user_name/avatar 不再依赖存储值，查询时从 users 表取）"""
    title = title.strip()
    content = content.strip()
    category = category.strip()
    if not title:
        return {"ok": False, "error": _("标题不能为空")}
    if not content:
        return {"ok": False, "error": _("内容不能为空")}
    if len(title) > 200:
        title = title[:200]
    if len(content) > 5000:
        content = content[:5000]
    if len(category) > 50:
        category = category[:50]

    status = (review_status or "pending").strip().lower()
    if status not in ("pending", "approved", "rejected"):
        status = "pending"

    with get_db() as db:
        # 查询用户昵称和头像
        live_name = ""
        avatar_url = ""
        if user_id:
            user = db.query(UserORM.nickname, UserORM.avatar_url).filter(UserORM.id == user_id).first()
            if user:
                live_name = user.nickname or ""
                avatar_url = user.avatar_url or ""

        post = PostORM(
            user_id=user_id,
            user_name=user_name.strip() or "匿名用户",
            title=title,
            content=content,
            images=_normalize_images(images),
            category=category,
            language=(language or "zh").strip()[:10] or "zh",
            review_status=status,
            reviewed_by=(reviewed_by or "").strip()[:50],
            reviewed_at=reviewed_at,
        )
        db.add(post)
        db.flush()
        return {
            "ok": True,
            "id": post.id,
            "post_id": post.id,
            "user_id": post.user_id,
            "user_name": localize_fallback_user_name(live_name or post.user_name),
            "avatar": avatar_url,
            "title": post.title,
            "content": post.content,
            "images": post.images,
            "category": post.category or "",
            "language": post.language or "zh",
            "likes_count": post.likes_count,
            "comments_count": post.comments_count,
            "review_status": post.review_status or "pending",
            "pending_review": (post.review_status or "pending") == "pending",
            "created_at": serialize_datetime(post.created_at),
        }


def _batch_liked_ids(db, post_ids: List[int], user_id: Optional[int], device_id: Optional[str]) -> set:
    """批量查询用户已点赞的帖子 ID"""
    if not post_ids:
        return set()
    if user_id:
        rows = db.query(PostLikeORM.post_id).filter(
            PostLikeORM.post_id.in_(post_ids),
            PostLikeORM.user_id == user_id,
        ).all()
    elif device_id:
        rows = db.query(PostLikeORM.post_id).filter(
            PostLikeORM.post_id.in_(post_ids),
            PostLikeORM.device_id == device_id,
        ).all()
    else:
        return set()
    return {r[0] for r in rows}


def _approved_filter():
    """公开流仅展示已通过审核的帖子；兼容历史空状态视为已通过。"""
    return or_(
        PostORM.review_status == "approved",
        PostORM.review_status.is_(None),
        PostORM.review_status == "",
    )


def get_posts_feed(
    limit: int = 20,
    offset: int = 0,
    device_id: Optional[str] = None,
    user_id: Optional[int] = None,
    category: Optional[str] = None,
    language: Optional[str] = None,
) -> List[dict]:
    """获取帖子列表（user_name/avatar 从 users 表实时取；仅已审核通过）"""
    with get_db() as db:
        query = db.query(PostORM, UserORM.nickname, UserORM.avatar_url).outerjoin(
            UserORM, PostORM.user_id == UserORM.id
        ).filter(_approved_filter())
        if category:
            query = query.filter(PostORM.category == category)
        if language:
            query = query.filter(PostORM.language == language)
        rows = (
            query.order_by(desc(PostORM.created_at))
            .limit(limit)
            .offset(offset)
            .all()
        )
        post_ids = [p.id for p, _, _ in rows]
        liked_ids = _batch_liked_ids(db, post_ids, user_id, device_id)
        comments_counts = _comment_counts_by_post(db, post_ids)
        return [
            {
                "id": p.id,
                "user_id": p.user_id,
                "user_name": localize_fallback_user_name(nickname or p.user_name),
                "avatar": avatar_url or "",
                "title": p.title,
                "content": p.content,
                "images": p.images or [],
                "category": p.category or "",
                "language": p.language or "zh",
                "likes_count": p.likes_count,
                "comments_count": comments_counts.get(p.id, 0),
                "liked": p.id in liked_ids,
                "review_status": getattr(p, "review_status", None) or "approved",
                "created_at": serialize_datetime(p.created_at),
            }
            for p, nickname, avatar_url in rows
        ]


def search_posts(
    query: str,
    limit: int = 20,
    offset: int = 0,
    device_id: Optional[str] = None,
    user_id: Optional[int] = None,
    language: Optional[str] = None,
) -> List[dict]:
    """搜索帖子（标题或内容匹配；仅已审核通过）"""
    with get_db() as db:
        search_pattern = f"%{query}%"
        q = (
            db.query(PostORM, UserORM.nickname, UserORM.avatar_url)
            .outerjoin(UserORM, PostORM.user_id == UserORM.id)
            .filter(
                _approved_filter(),
                or_(
                    PostORM.title.ilike(search_pattern),
                    PostORM.content.ilike(search_pattern),
                )
            )
        )
        if language:
            q = q.filter(PostORM.language == language)
        rows = q.order_by(desc(PostORM.created_at)).limit(limit).offset(offset).all()
        comments_counts = _comment_counts_by_post(db, [p.id for p, _, _ in rows])
        result = []
        for p, nickname, avatar_url in rows:
            liked = False
            if user_id:
                liked = (
                    db.query(PostLikeORM)
                    .filter_by(post_id=p.id, user_id=user_id)
                    .first()
                    is not None
                )
            elif device_id:
                liked = (
                    db.query(PostLikeORM)
                    .filter_by(post_id=p.id, device_id=device_id)
                    .first()
                    is not None
                )
            result.append({
                "id": p.id,
                "user_id": p.user_id,
                "user_name": localize_fallback_user_name(nickname or p.user_name),
                "avatar": avatar_url or "",
                "title": p.title,
                "content": p.content,
                "images": p.images or [],
                "category": p.category or "",
                "language": p.language or "zh",
                "likes_count": p.likes_count,
                "comments_count": comments_counts.get(p.id, 0),
                "liked": liked,
                "review_status": getattr(p, "review_status", None) or "approved",
                "created_at": serialize_datetime(p.created_at),
            })
        return result


def get_my_posts(
    user_id: int,
    limit: int = 50,
    offset: int = 0,
) -> List[dict]:
    """获取指定用户发布的帖子列表（user_name/avatar 从 users 表实时取）"""
    with get_db() as db:
        rows = (
            db.query(PostORM, UserORM.nickname, UserORM.avatar_url)
            .outerjoin(UserORM, PostORM.user_id == UserORM.id)
            .filter(PostORM.user_id == user_id)
            .order_by(desc(PostORM.created_at))
            .limit(limit)
            .offset(offset)
            .all()
        )
        comments_counts = _comment_counts_by_post(db, [p.id for p, _, _ in rows])
        return [
            {
                "id": p.id,
                "user_id": p.user_id,
                "user_name": localize_fallback_user_name(nickname or p.user_name),
                "avatar": avatar_url or "",
                "title": p.title,
                "content": p.content,
                "images": p.images or [],
                "category": p.category or "",
                "likes_count": p.likes_count,
                "comments_count": comments_counts.get(p.id, 0),
                "liked": True,
                "review_status": getattr(p, "review_status", None) or "approved",
                "reject_reason": getattr(p, "reject_reason", None) or "",
                "reject_reason_id": getattr(p, "reject_reason_id", None),
                "created_at": serialize_datetime(p.created_at),
            }
            for p, nickname, avatar_url in rows
        ]


def get_post_detail(post_id: int, viewer_user_id: Optional[int] = None) -> Optional[dict]:
    """获取帖子详情；未通过审核的帖子仅作者可见。"""
    with get_db() as db:
        row = (
            db.query(PostORM, UserORM.nickname, UserORM.avatar_url)
            .outerjoin(UserORM, PostORM.user_id == UserORM.id)
            .filter(PostORM.id == post_id)
            .first()
        )
        if not row:
            return None
        post, nickname, avatar_url = row
        status = getattr(post, "review_status", None) or "approved"
        if status != "approved" and post.user_id != viewer_user_id:
            return None
        real_comments_count = (
            db.query(func.count(PostCommentORM.id))
            .filter(PostCommentORM.post_id == post_id)
            .scalar()
        ) or 0
        return {
            "id": post.id,
            "user_id": post.user_id,
            "user_name": localize_fallback_user_name(nickname or post.user_name),
            "avatar": avatar_url or "",
            "title": post.title,
            "content": post.content,
            "images": post.images or [],
            "category": post.category or "",
            "likes_count": post.likes_count,
            "comments_count": real_comments_count,
            "review_status": status,
            "reject_reason": getattr(post, "reject_reason", None) or "",
            "reject_reason_id": getattr(post, "reject_reason_id", None),
            "created_at": serialize_datetime(post.created_at),
        }


def count_approved_posts(
    category: Optional[str] = None,
    search: Optional[str] = None,
    language: Optional[str] = None,
) -> int:
    with get_db() as db:
        query = db.query(func.count(PostORM.id)).filter(_approved_filter())
        if category:
            query = query.filter(PostORM.category == category)
        if language:
            query = query.filter(PostORM.language == language)
        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(PostORM.title.ilike(pattern), PostORM.content.ilike(pattern))
            )
        return query.scalar() or 0


def list_posts_for_admin(
    status: Optional[str] = "pending",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    with get_db() as db:
        count_q = db.query(func.count(PostORM.id))
        q = db.query(PostORM, UserORM).outerjoin(UserORM, PostORM.user_id == UserORM.id)
        if status:
            count_q = count_q.filter(PostORM.review_status == status)
            q = q.filter(PostORM.review_status == status)
        total = count_q.scalar() or 0
        rows = q.order_by(desc(PostORM.created_at)).limit(limit).offset(offset).all()
        post_ids = [p.id for p, _ in rows]
        comments_counts = _comment_counts_by_post(db, post_ids)
        items = [
            _post_admin_dict(p, u, comments_count=comments_counts.get(p.id, 0))
            for p, u in rows
        ]
        return items, total


def list_posts_for_content_admin(
    *,
    review_status: Optional[str] = None,
    language: Optional[str] = None,
    category: Optional[str] = None,
    user_id: Optional[Any] = None,
    keyword: Optional[str] = None,
    user_nickname: Optional[str] = None,
    has_image: Optional[bool] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """内容管理帖子列表：筛选 + 排序 + 分页。"""
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 20)))
    offset = (page - 1) * page_size

    with get_db() as db:
        q = db.query(PostORM, UserORM).outerjoin(UserORM, PostORM.user_id == UserORM.id)

        st = (review_status or "").strip().lower()
        if st and st != "all":
            if st in ("pending", "approved", "rejected"):
                q = q.filter(PostORM.review_status == st)

        if language and language.strip():
            q = q.filter(PostORM.language == language.strip())
        if category and category.strip():
            q = q.filter(PostORM.category == category.strip())
        q = _apply_admin_user_query(q, user_id)
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            q = q.filter(or_(PostORM.title.ilike(pattern), PostORM.content.ilike(pattern)))
        if user_nickname and user_nickname.strip():
            nick = f"%{user_nickname.strip()}%"
            q = q.filter(
                or_(
                    UserORM.nickname.ilike(nick),
                    PostORM.user_name.ilike(nick),
                )
            )
        if has_image is True:
            q = q.filter(_images_array_length() > 0)
        elif has_image is False:
            q = q.filter(_images_array_length() == 0)

        dt_from = _parse_date_bound(created_from, end=False)
        dt_to = _parse_date_bound(created_to, end=True)
        if dt_from:
            q = q.filter(PostORM.created_at >= dt_from)
        if dt_to:
            q = q.filter(PostORM.created_at <= dt_to)

        total = q.count()

        sort_key = (sort_by or "created_at").strip().lower()
        sort_col_map = {
            "created_at": PostORM.created_at,
            "reviewed_at": PostORM.reviewed_at,
            "likes_count": PostORM.likes_count,
            "comments_count": PostORM.comments_count,
        }
        sort_col = sort_col_map.get(sort_key, PostORM.created_at)
        ascending = (sort_order or "desc").strip().lower() == "asc"
        primary = asc(sort_col) if ascending else desc(sort_col)
        rows = (
            q.order_by(primary, desc(PostORM.id))
            .offset(offset)
            .limit(page_size)
            .all()
        )
        post_ids = [p.id for p, _ in rows]
        comments_counts = _comment_counts_by_post(db, post_ids)
        items = [
            _post_admin_dict(p, u, comments_count=comments_counts.get(p.id, 0))
            for p, u in rows
        ]
        return items, total


def get_post_for_admin(post_id: int) -> Optional[dict]:
    with get_db() as db:
        row = (
            db.query(PostORM, UserORM)
            .outerjoin(UserORM, PostORM.user_id == UserORM.id)
            .filter(PostORM.id == int(post_id))
            .first()
        )
        if not row:
            return None
        p, u = row
        real_comments = (
            db.query(func.count(PostCommentORM.id))
            .filter(PostCommentORM.post_id == p.id)
            .scalar()
        ) or 0
        return _post_admin_dict(p, u, comments_count=int(real_comments))


def admin_create_post(
    *,
    title: str,
    content: str,
    user_id: Optional[int] = None,
    user_name: str = "匿名用户",
    images: Optional[List[str]] = None,
    category: str = "",
    language: str = "zh",
    reviewer: str = "admin",
) -> dict:
    """运营代发：默认直接 approved。"""
    now = datetime.now(timezone.utc)
    result = create_post(
        title=title,
        content=content,
        user_id=user_id,
        user_name=user_name,
        images=images,
        category=category,
        language=language,
        review_status="approved",
        reviewed_by=(reviewer or "admin")[:50],
        reviewed_at=now,
    )
    if not result.get("ok"):
        return result
    detail = get_post_for_admin(int(result["id"]))
    if detail:
        detail["ok"] = True
        return detail
    result["ok"] = True
    return result


def admin_update_post(
    post_id: int,
    *,
    title: Optional[str] = None,
    content: Optional[str] = None,
    images=None,
    category: Optional[str] = None,
    language: Optional[str] = None,
) -> dict:
    """编辑帖子字段；不改变 review_status（已通过帖保持 approved）。"""
    with get_db() as db:
        post = db.query(PostORM).filter(PostORM.id == int(post_id)).first()
        if not post:
            return {"ok": False, "error": "post_not_found"}

        if title is not None:
            t = title.strip()
            if not t:
                return {"ok": False, "error": "title_content_required"}
            post.title = t[:200]
        if content is not None:
            c = content.strip()
            if not c:
                return {"ok": False, "error": "title_content_required"}
            post.content = c[:5000]
        if images is not None:
            post.images = _normalize_images(images)
        if category is not None:
            post.category = (category or "").strip()[:50]
        if language is not None:
            post.language = (language or "zh").strip()[:10] or "zh"

        db.flush()

    detail = get_post_for_admin(int(post_id))
    if not detail:
        return {"ok": False, "error": "post_not_found"}
    detail["ok"] = True
    return detail


def review_post(
    post_id: int,
    approve: bool,
    reason: str = "",
    reviewer: str = "admin",
    *,
    reason_id: Optional[int] = None,
) -> dict:
    with get_db() as db:
        post = db.query(PostORM).filter(PostORM.id == post_id).first()
        if not post:
            return {"ok": False, "error": "post_not_found"}
        status = getattr(post, "review_status", None) or "pending"
        if status != "pending":
            return {"ok": False, "error": "review_not_pending"}
        now = datetime.now(timezone.utc)
        if approve:
            post.review_status = "approved"
            post.reject_reason = ""
            post.reject_reason_id = None
            if post.user_id:
                from services.review_notifications import push_review_approved_notifications

                push_review_approved_notifications(
                    db, post.user_id, "post", post_title=post.title or ""
                )
        else:
            post.review_status = "rejected"
            post.reject_reason = (reason or "").strip()[:200]
            post.reject_reason_id = int(reason_id) if reason_id is not None else None
        post.reviewed_by = reviewer
        post.reviewed_at = now
        db.flush()
        return {"ok": True, "id": post.id, "status": post.review_status}


def admin_delete_post(post_id: int) -> dict:
    """管理端删除帖子（任意审核状态，含 [指南]）；硬删帖子及点赞、评论。"""
    if not delete_post(int(post_id)):
        return {"ok": False, "error": "post_not_found"}
    return {"ok": True, "id": int(post_id)}


def list_comments_for_admin(
    post_id: int,
    *,
    keyword: Optional[str] = None,
    user_id: Optional[int] = None,
    sort_order: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 50)))
    offset = (page - 1) * page_size

    with get_db() as db:
        post = db.query(PostORM.id).filter(PostORM.id == int(post_id)).first()
        if not post:
            return [], -1

        q = (
            db.query(PostCommentORM, UserORM.nickname, UserORM.avatar_url)
            .outerjoin(UserORM, PostCommentORM.user_id == UserORM.id)
            .filter(PostCommentORM.post_id == int(post_id))
        )
        if keyword and keyword.strip():
            q = q.filter(PostCommentORM.content.ilike(f"%{keyword.strip()}%"))
        if user_id is not None:
            q = q.filter(PostCommentORM.user_id == int(user_id))

        total = q.count()
        ascending = (sort_order or "asc").strip().lower() != "desc"
        order = (
            asc(PostCommentORM.created_at)
            if ascending
            else desc(PostCommentORM.created_at)
        )
        rows = q.order_by(order, PostCommentORM.id.asc()).offset(offset).limit(page_size).all()
        items = [
            _comment_admin_dict(c, nickname, avatar_url)
            for c, nickname, avatar_url in rows
        ]
        return items, total


def admin_add_comment(
    post_id: int,
    content: str,
    *,
    user_id: Optional[int] = None,
    user_name: str = "官方",
) -> dict:
    """管理端代发评论：不校验帖子审核状态。"""
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "empty_content"}
    if len(content) > 500:
        content = content[:500]

    with get_db() as db:
        post = db.query(PostORM).filter(PostORM.id == int(post_id)).first()
        if not post:
            return {"ok": False, "error": "post_not_found"}

        live_name = ""
        avatar_url = ""
        if user_id:
            user = (
                db.query(UserORM.nickname, UserORM.avatar_url)
                .filter(UserORM.id == int(user_id))
                .first()
            )
            if user:
                live_name = user.nickname or ""
                avatar_url = user.avatar_url or ""

        comment = PostCommentORM(
            post_id=int(post_id),
            user_id=int(user_id) if user_id else None,
            user_name=(user_name or "").strip() or (live_name or "官方"),
            device_id=None,
            content=content,
        )
        db.add(comment)
        post.comments_count = (post.comments_count or 0) + 1
        db.flush()
        item = _comment_admin_dict(comment, live_name or comment.user_name, avatar_url)
        item["ok"] = True
        return item


def admin_update_comment(post_id: int, comment_id: int, content: str) -> dict:
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "empty_content"}
    if len(content) > 500:
        content = content[:500]

    with get_db() as db:
        row = (
            db.query(PostCommentORM, UserORM.nickname, UserORM.avatar_url)
            .outerjoin(UserORM, PostCommentORM.user_id == UserORM.id)
            .filter(
                PostCommentORM.id == int(comment_id),
                PostCommentORM.post_id == int(post_id),
            )
            .first()
        )
        if not row:
            return {"ok": False, "error": "comment_not_found"}
        comment, nickname, avatar_url = row
        comment.content = content
        db.flush()
        item = _comment_admin_dict(comment, nickname, avatar_url)
        item["ok"] = True
        return item


def admin_delete_comment(post_id: int, comment_id: int) -> dict:
    with get_db() as db:
        comment = (
            db.query(PostCommentORM)
            .filter(
                PostCommentORM.id == int(comment_id),
                PostCommentORM.post_id == int(post_id),
            )
            .first()
        )
        if not comment:
            return {"ok": False, "error": "comment_not_found"}
        db.delete(comment)
        post = db.query(PostORM).filter(PostORM.id == int(post_id)).first()
        if post:
            current = int(post.comments_count or 0)
            post.comments_count = max(current - 1, 0)
        db.flush()
        return {"ok": True, "id": int(comment_id), "post_id": int(post_id)}


def _like_device_id(device_id: Optional[str]) -> Optional[str]:
    """写入 post_likes.device_id。空或 anonymous 存 NULL，避免全站共用 anonymous 唯一键。"""
    d = (device_id or "").strip()
    if not d or d.lower() == "anonymous":
        return None
    return d


def _store_like_device(user_id: Optional[int], device_id: Optional[str]) -> str:
    """登录用户写入 per-user 占位，避免 NULL/空串和别人的 device 撞 uniq_post_like_device。"""
    if user_id:
        return f"u:{int(user_id)}"
    return _like_device_id(device_id) or "anonymous"


def _own_like_query(db, post_id: int, user_id: Optional[int], store_device: Optional[str]):
    q = db.query(PostLikeORM).filter_by(post_id=post_id)
    if user_id:
        return q.filter(PostLikeORM.user_id == user_id)
    return q.filter(PostLikeORM.device_id == store_device)


def _post_likes_count(db, post_id: int) -> int:
    return int(db.query(PostORM.likes_count).filter_by(id=post_id).scalar() or 0)


def _sync_post_likes_count(db, post_id: int) -> int:
    """按 post_likes 实表回写 likes_count，避免 NULL+1 停在 NULL、撞唯一键后计数漂移。"""
    n = int(
        db.query(func.count(PostLikeORM.id)).filter(PostLikeORM.post_id == post_id).scalar()
        or 0
    )
    db.query(PostORM).filter_by(id=post_id).update(
        {PostORM.likes_count: n},
        synchronize_session=False,
    )
    db.flush()
    return n


def _insert_post_like(db, post_id: int, store_device: Optional[str], user_id: Optional[int]) -> None:
    db.add(
        PostLikeORM(
            post_id=post_id,
            device_id=store_device,
            user_id=user_id,
        )
    )
    db.flush()
    _sync_post_likes_count(db, post_id)


def toggle_post_like(
    post_id: int,
    device_id: Optional[str] = None,
    user_id: Optional[int] = None,
) -> dict:
    """点赞或取消点赞帖子。

    已登录只按 user_id 切换。device_id 用 u:{user_id}，避免和游客/其他账号抢
    (post_id, device_id) 唯一键。计数一律按 post_likes 行数回写。
    """
    with get_db() as db:
        post = db.query(PostORM).filter_by(id=post_id).first()
        if not post:
            return {"ok": False, "error": _("帖子不存在")}
        status = getattr(post, "review_status", None) or "approved"
        if status != "approved":
            return {"ok": False, "error": _("帖子审核中，暂不可点赞")}

        store_device = _store_like_device(user_id, device_id)

        existing = _own_like_query(db, post_id, user_id, store_device).first()
        if existing:
            db.delete(existing)
            db.flush()
            return {"ok": True, "liked": False, "likes_count": _sync_post_likes_count(db, post_id)}

        liked = False
        try:
            _insert_post_like(db, post_id, store_device, user_id)
            liked = True
        except IntegrityError:
            db.rollback()
            if _own_like_query(db, post_id, user_id, store_device).first():
                liked = True
            elif user_id:
                try:
                    _insert_post_like(db, post_id, f"u:{int(user_id)}", user_id)
                    liked = True
                except IntegrityError:
                    db.rollback()
                    liked = _own_like_query(db, post_id, user_id, store_device).first() is not None

        return {"ok": True, "liked": liked, "likes_count": _sync_post_likes_count(db, post_id)}


def add_post_comment(
    post_id: int,
    content: str,
    device_id: Optional[str] = None,
    user_id: Optional[int] = None,
    user_name: str = "匿名用户",
) -> dict:
    """添加评论"""
    content = content.strip()
    if not content:
        return {"ok": False, "error": _("评论内容不能为空")}
    if len(content) > 500:
        content = content[:500]

    with get_db() as db:
        post = db.query(PostORM).filter_by(id=post_id).first()
        if not post:
            return {"ok": False, "error": _("帖子不存在")}
        status = getattr(post, "review_status", None) or "approved"
        if status == "rejected":
            return {"ok": False, "error": _("帖子已被驳回，暂不可评论")}
        if status != "approved":
            return {"ok": False, "error": _("帖子审核中，暂不可评论")}

        # 查询用户昵称和头像
        live_name = ""
        avatar_url = ""
        if user_id:
            user = db.query(UserORM.nickname, UserORM.avatar_url).filter(UserORM.id == user_id).first()
            if user:
                live_name = user.nickname or ""
                avatar_url = user.avatar_url or ""

        comment = PostCommentORM(
            post_id=post_id,
            user_id=user_id,
            user_name=user_name.strip() or "匿名用户",
            device_id=device_id,
            content=content,
        )
        db.add(comment)
        post.comments_count = (post.comments_count or 0) + 1
        db.flush()
        return {
            "ok": True,
            "id": comment.id,
            "user_name": localize_fallback_user_name(live_name or comment.user_name),
            "avatar": avatar_url,
            "content": comment.content,
            "created_at": serialize_datetime(comment.created_at),
        }


def get_post_comments(post_id: int, limit: int = 50) -> List[dict]:
    """获取帖子的所有评论（user_name/avatar 从 users 表实时取）"""
    with get_db() as db:
        rows = (
            db.query(PostCommentORM, UserORM.nickname, UserORM.avatar_url)
            .outerjoin(UserORM, PostCommentORM.user_id == UserORM.id)
            .filter(PostCommentORM.post_id == post_id)
            .order_by(PostCommentORM.created_at)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": c.id,
                "user_id": c.user_id,
            "user_name": localize_fallback_user_name(nickname or c.user_name),
                "avatar": avatar_url or "",
                "content": c.content,
                "created_at": serialize_datetime(c.created_at),
            }
            for c, nickname, avatar_url in rows
        ]


def delete_post(post_id: int) -> bool:
    """删除帖子及其评论和点赞"""
    with get_db() as db:
        post = db.query(PostORM).filter_by(id=post_id).first()
        if not post:
            return False
        db.query(PostLikeORM).filter_by(post_id=post_id).delete(synchronize_session=False)
        db.query(PostCommentORM).filter_by(post_id=post_id).delete(synchronize_session=False)
        db.delete(post)
        return True
