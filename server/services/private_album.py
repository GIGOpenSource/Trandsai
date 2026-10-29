"""智能体 Our Moments 私密相册：配置、进度、聊天解锁。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm.attributes import flag_modified

from core.database import (
    CompanionPrivateAlbumPhotoORM,
    CompanionPrivateAlbumProgressORM,
    get_db,
)

logger = logging.getLogger(__name__)

DEFAULT_UNLOCK_X = 5
MAX_PHOTOS = 50
MAX_URL = 500


def _clip_url(raw) -> str:
    return str(raw or "").strip()[:MAX_URL]


def _unlock_x(raw) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = DEFAULT_UNLOCK_X
    return max(1, min(n, 999))


def _photo_ids(raw) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        try:
            pid = int(item)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def photo_to_admin(row: CompanionPrivateAlbumPhotoORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "photo_id": row.id,
        "companion_id": row.companion_id,
        "image_url": row.image_url or "",
        "unlock_x": int(row.unlock_x or DEFAULT_UNLOCK_X),
        "sort_order": int(row.sort_order or 0),
        "is_enabled": bool(row.is_enabled),
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _enabled_photos(db, companion_id: str) -> list[CompanionPrivateAlbumPhotoORM]:
    return (
        db.query(CompanionPrivateAlbumPhotoORM)
        .filter(
            CompanionPrivateAlbumPhotoORM.companion_id == companion_id,
            CompanionPrivateAlbumPhotoORM.is_enabled.is_(True),
        )
        .order_by(
            CompanionPrivateAlbumPhotoORM.sort_order.asc(),
            CompanionPrivateAlbumPhotoORM.id.asc(),
        )
        .all()
    )


def _get_progress(db, user_id: int, companion_id: str) -> CompanionPrivateAlbumProgressORM:
    row = (
        db.query(CompanionPrivateAlbumProgressORM)
        .filter(
            CompanionPrivateAlbumProgressORM.user_id == int(user_id),
            CompanionPrivateAlbumProgressORM.companion_id == companion_id,
        )
        .first()
    )
    if row:
        return row
    row = CompanionPrivateAlbumProgressORM(
        user_id=int(user_id),
        companion_id=companion_id,
        unlocked_photo_ids=[],
        current_photo_id=None,
        current_unlock_x=DEFAULT_UNLOCK_X,
        progress_count=0,
    )
    db.add(row)
    db.flush()
    return row


def _sync_target(
    progress: CompanionPrivateAlbumProgressORM,
    photos: list[CompanionPrivateAlbumPhotoORM],
) -> Optional[CompanionPrivateAlbumPhotoORM]:
    valid = {p.id for p in photos}
    unlocked = [i for i in _photo_ids(progress.unlocked_photo_ids) if i in valid]
    if unlocked != list(progress.unlocked_photo_ids or []):
        progress.unlocked_photo_ids = unlocked
        flag_modified(progress, "unlocked_photo_ids")
    locked = [p for p in photos if p.id not in set(unlocked)]
    if not locked:
        progress.current_photo_id = None
        return None
    cur_id = progress.current_photo_id
    for p in locked:
        if cur_id is not None and p.id == cur_id:
            if not int(progress.current_unlock_x or 0):
                progress.current_unlock_x = _unlock_x(p.unlock_x)
            return p
    nxt = locked[0]
    progress.current_photo_id = nxt.id
    progress.current_unlock_x = _unlock_x(nxt.unlock_x)
    return nxt


def _album_payload(
    companion_id: str,
    photos: list[CompanionPrivateAlbumPhotoORM],
    progress: Optional[CompanionPrivateAlbumProgressORM],
    *,
    just_unlocked: Optional[list[dict]] = None,
) -> dict[str, Any]:
    unlocked_set = set(_photo_ids(progress.unlocked_photo_ids if progress else []))
    items = []
    for p in photos:
        unlocked = p.id in unlocked_set
        items.append(
            {
                "photo_id": p.id,
                "sort_order": int(p.sort_order or 0),
                "unlocked": unlocked,
                "image_url": p.image_url or "",
            }
        )
    remaining = 0
    x = 0
    all_unlocked = bool(photos) and not any(not it["unlocked"] for it in items)
    locked = [p for p in photos if p.id not in unlocked_set]
    target = None
    if locked:
        if progress and progress.current_photo_id:
            target = next((p for p in locked if p.id == progress.current_photo_id), None)
            if target:
                x = int(progress.current_unlock_x or target.unlock_x or DEFAULT_UNLOCK_X)
        if target is None:
            target = locked[0]
            x = _unlock_x(target.unlock_x)
        count = int(progress.progress_count or 0) if progress else 0
        remaining = max(0, x - count)

    return {
        "companion_id": companion_id,
        "photo_count": len(items),
        "unlocked_count": sum(1 for it in items if it["unlocked"]),
        "empty": len(items) == 0,
        "all_unlocked": all_unlocked,
        "unlock_x": x,
        "remaining_messages": remaining,
        "just_unlocked": just_unlocked or [],
        "photos": items,
    }


def get_user_album(user_id: Optional[int], companion_id: str) -> dict[str, Any]:
    cid = (companion_id or "").strip()
    with get_db() as db:
        photos = _enabled_photos(db, cid)
        progress = None
        if user_id:
            progress = (
                db.query(CompanionPrivateAlbumProgressORM)
                .filter(
                    CompanionPrivateAlbumProgressORM.user_id == int(user_id),
                    CompanionPrivateAlbumProgressORM.companion_id == cid,
                )
                .first()
            )
        return _album_payload(cid, photos, progress)


def on_user_chat_message(user_id: Optional[int], companion_id: str) -> Optional[dict[str, Any]]:
    """用户成功发送一条聊天消息后调用。AI 回复不要调用。"""
    if not user_id or not companion_id:
        return None
    cid = str(companion_id).strip()
    try:
        with get_db() as db:
            photos = _enabled_photos(db, cid)
            if not photos:
                return None
            progress = _get_progress(db, int(user_id), cid)
            target = _sync_target(progress, photos)
            if target is None:
                # 进入本轮前已全部解锁：不再推 our_moments
                return None
            progress.progress_count = int(progress.progress_count or 0) + 1
            x = int(progress.current_unlock_x or target.unlock_x or DEFAULT_UNLOCK_X)
            just = []
            toast = "countdown"
            if progress.progress_count >= x:
                unlocked = _photo_ids(progress.unlocked_photo_ids)
                if target.id not in unlocked:
                    unlocked.append(target.id)
                progress.unlocked_photo_ids = unlocked
                flag_modified(progress, "unlocked_photo_ids")
                just = [{"photo_id": target.id, "image_url": target.image_url or ""}]
                toast = "unlocked"
                progress.progress_count = 0
                progress.current_photo_id = None
                _sync_target(progress, photos)
            progress.updated_at = datetime.now(timezone.utc)
            out = _album_payload(cid, photos, progress, just_unlocked=just)
            # 最后一张刚解锁：仍下发 unlocked（all_unlocked=true、remaining=0、just_unlocked）
            # 之后再发消息才走上面 target is None，不再推送
            if toast == "unlocked":
                out["toast"] = "unlocked"
            else:
                out["toast"] = "countdown"
            return out
    except Exception:
        logger.exception(
            "[private_album] increment failed user=%s companion=%s",
            user_id,
            companion_id,
        )
        return None


def list_admin_photos(companion_id: str) -> list[dict[str, Any]]:
    cid = (companion_id or "").strip()
    with get_db() as db:
        rows = (
            db.query(CompanionPrivateAlbumPhotoORM)
            .filter(CompanionPrivateAlbumPhotoORM.companion_id == cid)
            .order_by(
                CompanionPrivateAlbumPhotoORM.sort_order.asc(),
                CompanionPrivateAlbumPhotoORM.id.asc(),
            )
            .all()
        )
        return [photo_to_admin(r) for r in rows]


def create_admin_photo(companion_id: str, data: dict) -> dict[str, Any]:
    cid = (companion_id or "").strip()
    url = _clip_url(data.get("image_url"))
    if not url:
        raise ValueError("image_url_required")
    with get_db() as db:
        count = (
            db.query(CompanionPrivateAlbumPhotoORM.id)
            .filter(CompanionPrivateAlbumPhotoORM.companion_id == cid)
            .count()
        )
        if count >= MAX_PHOTOS:
            raise ValueError("album_full")
        sort_order = data.get("sort_order")
        if sort_order is None:
            sort_order = count
        row = CompanionPrivateAlbumPhotoORM(
            companion_id=cid,
            image_url=url,
            unlock_x=_unlock_x(data.get("unlock_x", DEFAULT_UNLOCK_X)),
            sort_order=int(sort_order or 0),
            is_enabled=bool(data.get("is_enabled", True)),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return photo_to_admin(row)


def update_admin_photo(companion_id: str, photo_id: int, data: dict) -> dict[str, Any]:
    cid = (companion_id or "").strip()
    with get_db() as db:
        row = (
            db.query(CompanionPrivateAlbumPhotoORM)
            .filter(
                CompanionPrivateAlbumPhotoORM.id == int(photo_id),
                CompanionPrivateAlbumPhotoORM.companion_id == cid,
            )
            .first()
        )
        if not row:
            raise KeyError("not_found")
        if "image_url" in data:
            url = _clip_url(data.get("image_url"))
            if not url:
                raise ValueError("image_url_required")
            row.image_url = url
        if "unlock_x" in data:
            row.unlock_x = _unlock_x(data.get("unlock_x"))
        if "sort_order" in data:
            try:
                row.sort_order = int(data.get("sort_order") or 0)
            except (TypeError, ValueError):
                row.sort_order = 0
        if "is_enabled" in data:
            row.is_enabled = bool(data.get("is_enabled"))
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
        return photo_to_admin(row)


def delete_admin_photo(companion_id: str, photo_id: int) -> dict[str, Any]:
    cid = (companion_id or "").strip()
    with get_db() as db:
        row = (
            db.query(CompanionPrivateAlbumPhotoORM)
            .filter(
                CompanionPrivateAlbumPhotoORM.id == int(photo_id),
                CompanionPrivateAlbumPhotoORM.companion_id == cid,
            )
            .first()
        )
        if not row:
            raise KeyError("not_found")
        db.delete(row)
        db.commit()
        return {"deleted_id": int(photo_id), "photo_id": int(photo_id)}


def reorder_admin_photos(companion_id: str, items: list) -> list[dict[str, Any]]:
    cid = (companion_id or "").strip()
    order_map = {}
    for i, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        pid = item.get("id") or item.get("photo_id")
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if "sort_order" in item:
            try:
                order_map[pid] = int(item.get("sort_order"))
            except (TypeError, ValueError):
                order_map[pid] = i
        else:
            order_map[pid] = i
    with get_db() as db:
        rows = (
            db.query(CompanionPrivateAlbumPhotoORM)
            .filter(CompanionPrivateAlbumPhotoORM.companion_id == cid)
            .all()
        )
        for row in rows:
            if row.id in order_map:
                row.sort_order = order_map[row.id]
                row.updated_at = datetime.now(timezone.utc)
        db.commit()
        return list_admin_photos(cid)
