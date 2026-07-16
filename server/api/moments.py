import asyncio
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from core.permissions import IsAuthenticated, IsOwner
from core.dependencies import require_permissions, get_optional_user
from core.state import get_companion_manager
from services.moments import (
    add_user_comment,
    count_moments_feed,
    get_companion_moments,
    get_moment_comments,
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
    """获取朋友圈列表，包含评论（公开接口，所有人都能看到所有人的朋友圈）"""
    device_id = _get_device_id(x_device_id)
    moments = get_moments_feed(
        limit=limit,
        offset=offset,
        device_id=device_id,
        lang=lang or "",
        filter_lang=filter_lang or "",
        gender=gender or "",
        orientation=orientation or "",
    )
    for moment in moments:
        moment["comments"] = get_moment_comments(moment["id"], limit=10)
    total = count_moments_feed(
        filter_lang=filter_lang or "",
        gender=gender or "",
        orientation=orientation or "",
    )
    return {"moments": moments, "total": total}


# POST 路由必须在 GET {moment_id} 之前，避免路径冲突
@router.post("/api/moments/{moment_id}/like")
async def api_toggle_like(
    moment_id: int,
    x_device_id: Optional[str] = Header(None),
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """点赞或取消点赞"""
    device_id = _get_device_id(x_device_id)
    result = toggle_like(moment_id, device_id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result.get("error", "Not found"))
    return result


@router.post("/api/moments/{moment_id}/comment")
async def api_add_comment(
    moment_id: int,
    x_device_id: Optional[str] = Header(None),
    content: str = Body(..., embed=True),
    parent_id: Optional[int] = Body(None, embed=True),
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """用户发表评论或回复评论"""
    device_id = _get_device_id(x_device_id)
    result = add_user_comment(moment_id, device_id, content, parent_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "评论失败"))
    return result


@router.post("/api/moments/{moment_id}/regenerate-image")
async def api_regenerate_moment_image(
    moment_id: int,
    user_id: int = Depends(require_permissions(IsOwner)),
):
    """根据朋友圈文案重新生成配图"""
    new_url = regenerate_moment_image(moment_id)
    if not new_url:
        raise HTTPException(status_code=404, detail="朋友圈不存在或无文案")
    return {"ok": True, "image_url": new_url}


# GET {moment_id} 必须在所有 POST {moment_id}/xxx 之后
@router.get("/api/moments/{moment_id}")
async def api_get_moment_detail(
    moment_id: int,
    x_device_id: Optional[str] = Header(None),
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """获取单条朋友圈详情，包含评论"""
    device_id = _get_device_id(x_device_id)
    moment = get_moment_detail(moment_id, device_id=device_id)
    if not moment:
        raise HTTPException(status_code=404, detail="朋友圈不存在")
    moment["comments"] = get_moment_comments(moment_id, limit=100)
    return moment


@router.get("/api/companions/{companion_id}/moments")
async def api_companion_moments(
    companion_id: str,
    limit: int = Query(20, ge=1, le=100),
    user_id: int = Depends(require_permissions(IsOwner)),
):
    """获取某个伴侣的所有朋友圈"""
    moments = get_companion_moments(companion_id, limit=limit)
    return {"moments": moments, "total": len(moments)}
