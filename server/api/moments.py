import asyncio
from typing import Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from core.rest_async import run_rest
from core.state import get_companion_manager
from services.moments import (
    add_user_comment,
    count_moments_feed,
    get_companion_moments,
    get_moment_comments,
    get_moment_comments_batch,
    get_moment_detail,
    get_moments_feed,
    regenerate_moment_image,
    toggle_like,
)

router = APIRouter()


def _get_device_id(x_device_id: Optional[str] = None) -> str:
    """获取设备标识，用于点赞去重"""
    return x_device_id or "anonymous"


@router.get("/api/moments")
async def api_list_moments(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    lang: Optional[str] = Query(None),
    filter_lang: Optional[str] = Query(None, description="按智能体资料语种筛选"),
    gender: Optional[str] = Query(None, description="男/女"),
    orientation: Optional[str] = Query(None, description="性取向"),
    x_device_id: Optional[str] = Header(None),
):
    """获取朋友圈列表，包含评论"""
    device_id = _get_device_id(x_device_id)

    def _load():
        moments = get_moments_feed(
            limit=limit,
            offset=offset,
            device_id=device_id,
            lang=lang or "",
            filter_lang=filter_lang or "",
            gender=gender or "",
            orientation=orientation or "",
        )
        moment_ids = [m["id"] for m in moments]
        comments_map = get_moment_comments_batch(moment_ids, limit=10)
        for moment in moments:
            moment["comments"] = comments_map.get(moment["id"], [])
        total = count_moments_feed(
            filter_lang=filter_lang or "",
            gender=gender or "",
            orientation=orientation or "",
        )
        return {"moments": moments, "total": total}

    return await run_rest(_load)


@router.get("/api/moments/{moment_id}")
async def api_get_moment_detail(
    moment_id: int,
    x_device_id: Optional[str] = Header(None),
):
    """获取单条朋友圈详情，包含评论"""
    device_id = _get_device_id(x_device_id)

    def _load():
        moment = get_moment_detail(moment_id, device_id=device_id)
        if not moment:
            return None
        moment["comments"] = get_moment_comments(moment_id, limit=100)
        return moment

    moment = await run_rest(_load)
    if not moment:
        raise HTTPException(status_code=404, detail="朋友圈不存在")
    return moment


@router.post("/api/moments/{moment_id}/like")
async def api_toggle_like(
    moment_id: int,
    x_device_id: Optional[str] = Header(None),
):
    """点赞或取消点赞"""
    device_id = _get_device_id(x_device_id)
    result = toggle_like(moment_id, device_id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result.get("error", "Not found"))
    return result


@router.get("/api/companions/{companion_id}/moments")
async def api_companion_moments(
    companion_id: str,
    limit: int = Query(20, ge=1, le=100),
):
    """获取某个伴侣的所有朋友圈"""
    moments = get_companion_moments(companion_id, limit=limit)
    return {"moments": moments, "total": len(moments)}


@router.post("/api/moments/{moment_id}/comment")
async def api_add_comment(
    moment_id: int,
    x_device_id: Optional[str] = Header(None),
    content: str = Body(..., embed=True),
    parent_id: Optional[int] = Body(None, embed=True),
):
    """用户发表评论或回复评论"""
    device_id = _get_device_id(x_device_id)
    result = add_user_comment(moment_id, device_id, content, parent_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "评论失败"))
    return result


@router.post("/api/moments/{moment_id}/regenerate-image")
async def api_regenerate_moment_image(moment_id: int):
    """根据朋友圈文案重新生成配图"""
    new_url = regenerate_moment_image(moment_id)
    if not new_url:
        raise HTTPException(status_code=404, detail="朋友圈不存在或无文案")
    return {"ok": True, "image_url": new_url}


