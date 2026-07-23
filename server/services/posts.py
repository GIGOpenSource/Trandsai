from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import desc, func, or_
from sqlalchemy.exc import IntegrityError

from core.database import PostORM, PostCommentORM, PostLikeORM, UserORM, get_db, serialize_datetime


def create_post(
    title: str,
    content: str,
    user_id: Optional[int] = None,
    user_name: str = "匿名用户",
    avatar: str = "",
    images: Optional[List[str]] = None,
    category: str = "",
) -> dict:
    """创建新帖子（user_name/avatar 不再依赖存储值，查询时从 users 表取）"""
    title = title.strip()
    content = content.strip()
    category = category.strip()
    if not title:
        return {"ok": False, "error": "标题不能为空"}
    if not content:
        return {"ok": False, "error": "内容不能为空"}
    if len(title) > 200:
        title = title[:200]
    if len(content) > 5000:
        content = content[:5000]
    if len(category) > 50:
        category = category[:50]

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
            images=images or [],
            category=category,
        )
        db.add(post)
        db.flush()
        return {
            "ok": True,
            "id": post.id,
            "user_id": post.user_id,
            "user_name": live_name or post.user_name,
            "avatar": avatar_url,
            "title": post.title,
            "content": post.content,
            "images": post.images,
            "category": post.category or "",
            "likes_count": post.likes_count,
            "comments_count": post.comments_count,
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


def get_posts_feed(
    limit: int = 20,
    offset: int = 0,
    device_id: Optional[str] = None,
    user_id: Optional[int] = None,
    category: Optional[str] = None,
) -> List[dict]:
    """获取帖子列表（user_name/avatar 从 users 表实时取）"""
    with get_db() as db:
        query = db.query(PostORM, UserORM.nickname, UserORM.avatar_url).outerjoin(
            UserORM, PostORM.user_id == UserORM.id
        )
        if category:
            query = query.filter(PostORM.category == category)
        rows = (
            query.order_by(desc(PostORM.created_at))
            .limit(limit)
            .offset(offset)
            .all()
        )
        post_ids = [p.id for p, _, _ in rows]
        liked_ids = _batch_liked_ids(db, post_ids, user_id, device_id)
        return [
            {
                "id": p.id,
                "user_id": p.user_id,
                "user_name": nickname or p.user_name,
                "avatar": avatar_url or "",
                "title": p.title,
                "content": p.content,
                "images": p.images or [],
                "category": p.category or "",
                "likes_count": p.likes_count,
                "comments_count": p.comments_count,
                "liked": p.id in liked_ids,
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
) -> List[dict]:
    """搜索帖子（标题或内容匹配，user_name/avatar 从 users 表实时取）"""
    with get_db() as db:
        search_pattern = f"%{query}%"
        rows = (
            db.query(PostORM, UserORM.nickname, UserORM.avatar_url)
            .outerjoin(UserORM, PostORM.user_id == UserORM.id)
            .filter(
                or_(
                    PostORM.title.ilike(search_pattern),
                    PostORM.content.ilike(search_pattern),
                )
            )
            .order_by(desc(PostORM.created_at))
            .limit(limit)
            .offset(offset)
            .all()
        )
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
                "user_name": nickname or p.user_name,
                "avatar": avatar_url or "",
                "title": p.title,
                "content": p.content,
                "images": p.images or [],
                "category": p.category or "",
                "likes_count": p.likes_count,
                "comments_count": p.comments_count,
                "liked": liked,
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
        return [
            {
                "id": p.id,
                "user_id": p.user_id,
                "user_name": nickname or p.user_name,
                "avatar": avatar_url or "",
                "title": p.title,
                "content": p.content,
                "images": p.images or [],
                "category": p.category or "",
                "likes_count": p.likes_count,
                "comments_count": p.comments_count,
                "liked": True,
                "created_at": serialize_datetime(p.created_at),
            }
            for p, nickname, avatar_url in rows
        ]


def get_post_detail(post_id: int) -> Optional[dict]:
    """获取帖子详情（user_name/avatar 从 users 表实时取）"""
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
        return {
            "id": post.id,
            "user_id": post.user_id,
            "user_name": nickname or post.user_name,
            "avatar": avatar_url or "",
            "title": post.title,
            "content": post.content,
            "images": post.images or [],
            "category": post.category or "",
            "likes_count": post.likes_count,
            "comments_count": post.comments_count,
            "created_at": serialize_datetime(post.created_at),
        }


def toggle_post_like(
    post_id: int,
    device_id: Optional[str] = None,
    user_id: Optional[int] = None,
) -> dict:
    """点赞或取消点赞帖子"""
    with get_db() as db:
        post = db.query(PostORM).filter_by(id=post_id).first()
        if not post:
            return {"ok": False, "error": "帖子不存在"}

        query = db.query(PostLikeORM).filter_by(post_id=post_id)
        if user_id:
            query = query.filter_by(user_id=user_id)
        elif device_id:
            query = query.filter_by(device_id=device_id)
        else:
            return {"ok": False, "error": "无法识别用户"}

        existing = query.first()
        if existing:
            db.delete(existing)
            # 修复：不使用 func.max()，改用 Python max()，兼容 SQLite
            current_count = db.query(PostORM.likes_count).filter_by(id=post_id).scalar() or 0
            new_count = max(current_count - 1, 0)
            db.query(PostORM).filter_by(id=post_id).update(
                {PostORM.likes_count: new_count},
                synchronize_session=False,
            )
            db.flush()
            likes_count = new_count
            liked = False
        else:
            try:
                db.add(
                    PostLikeORM(
                        post_id=post_id,
                        device_id=device_id,
                        user_id=user_id,
                    )
                )
                db.flush()
                db.query(PostORM).filter_by(id=post_id).update(
                    {PostORM.likes_count: PostORM.likes_count + 1},
                    synchronize_session=False,
                )
                db.flush()
                liked = True
            except IntegrityError:
                # 并发重复点赞，视为已点赞
                db.rollback()
                liked = True
            likes_count = (
                db.query(PostORM.likes_count).filter_by(id=post_id).scalar() or 0
            )

        return {"ok": True, "liked": liked, "likes_count": likes_count}


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
        return {"ok": False, "error": "评论内容不能为空"}
    if len(content) > 500:
        content = content[:500]

    with get_db() as db:
        post = db.query(PostORM).filter_by(id=post_id).first()
        if not post:
            return {"ok": False, "error": "帖子不存在"}

        # 查询用户昵称
        live_name = ""
        if user_id:
            user = db.query(UserORM.nickname).filter(UserORM.id == user_id).first()
            if user:
                live_name = user.nickname or ""

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
            "user_name": live_name or comment.user_name,
            "content": comment.content,
            "created_at": serialize_datetime(comment.created_at),
        }


def get_post_comments(post_id: int, limit: int = 50) -> List[dict]:
    """获取帖子的所有评论（user_name 从 users 表实时取）"""
    with get_db() as db:
        rows = (
            db.query(PostCommentORM, UserORM.nickname)
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
                "user_name": nickname or c.user_name,
                "content": c.content,
                "created_at": serialize_datetime(c.created_at),
            }
            for c, nickname in rows
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
