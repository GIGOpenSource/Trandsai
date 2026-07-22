import asyncio
import logging
from datetime import datetime
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

router = APIRouter(tags=["朋友圈"])


def _get_device_id(x_device_id: Optional[str] = None) -> str:
    """获取设备标识，用于点赞去重（保留，为多设备区分预留）"""
    return x_device_id or "anonymous"


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

#
# @router.get("/api/moments")
# async def api_list_moments(
#     limit: int = Query(20, ge=1, le=100),
#     offset: int = Query(0, ge=0),
#     lang: Optional[str] = Query(None),
#     filter_lang: Optional[str] = Query(None, description="按智能体资料语种筛选"),
#     gender: Optional[str] = Query(None, description="男/女"),
#     orientation: Optional[str] = Query(None, description="性取向"),
#     x_device_id: Optional[str] = Header(None),
#     x_token: Optional[str] = Header(None),
# ):
#
#     """获取朋友圈列表，包含评论（公开接口，所有人都能看到所有人的朋友圈）"""
#     device_id = _get_device_id(x_device_id)
#     user_id = _get_user_id(x_token)
#     moments = get_moments_feed(
#         limit=limit,
#         offset=offset,
#         device_id=device_id,
#         user_id=user_id,
#         lang=lang or "",
#         filter_lang=filter_lang or "",
#         gender=gender or "",
#         orientation=orientation or "",
#     )
#     for moment in moments:
#         moment["comments"] = get_moment_comments(moment["id"], limit=10, current_user_id=user_id)
#     total = count_moments_feed(
#         filter_lang=filter_lang or "",
#         gender=gender or "",
#         orientation=orientation or "",
#     )
#     return {"moments": moments, "total": total}
logger = logging.getLogger(__name__)


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
):
    """获取朋友圈列表，包含评论（公开接口，所有人都能看到所有人的朋友圈）"""

    # ============ 1. 详细的请求日志 ============
    logger.info("=" * 60)
    logger.info(f"📥 API Request: /api/moments")
    logger.info(f"   Time: {datetime.now().isoformat()}")
    logger.info(f"   Parameters:")
    logger.info(f"     - limit: {limit} (type: {type(limit).__name__})")
    logger.info(f"     - offset: {offset} (type: {type(offset).__name__})")
    logger.info(f"     - lang: {lang} (type: {type(lang).__name__})")
    logger.info(f"     - filter_lang: {filter_lang}")
    logger.info(f"     - gender: {gender}")
    logger.info(f"     - orientation: {orientation}")
    logger.info(f"   Headers:")
    logger.info(f"     - x_token: {'***' if x_token else 'None'}")

    # ============ 2. 参数验证和转换 ============
    # 确保 lang 参数有效
    if lang is not None and lang == "":
        lang = None
        logger.warning("⚠️ lang参数为空字符串，已转为None")

    # 验证 lang 是否在允许的值范围内（如果后端只支持特定语言）
    allowed_langs = ["zh-CN", "en-US", "zh-TW", "ja-JP", "ko-KR"]  # 根据实际情况调整
    allowed_lang_shorts = ["zh", "en", "ja", "ko"]  # 短代码列表
    # 提取主语言代码进行比较（兼容前端发送的短代码如 'zh'）
    lang_short = lang.split("-")[0] if lang else None
    if lang is not None and lang not in allowed_langs and lang_short not in allowed_lang_shorts:
        logger.warning(f"⚠️ lang='{lang}' 不在允许列表中: {allowed_langs}")
        # 可以设置为默认值或保留原值
        # lang = "zh-CN"  # 取消注释以强制使用默认值

    # 验证 filter_lang
    if filter_lang is not None and filter_lang == "":
        filter_lang = None
        logger.warning("⚠️ filter_lang参数为空字符串，已转为None")

    filter_lang_short = filter_lang.split("-")[0] if filter_lang else None
    if filter_lang is not None and filter_lang not in allowed_langs and filter_lang_short not in allowed_lang_shorts:
        logger.warning(f"⚠️ filter_lang='{filter_lang}' 不在允许列表中: {allowed_langs}")

    # ============ 3. 处理 user_id ============
    try:
        user_id = _get_user_id(x_token)
        logger.info(f"   User ID: {user_id}")
    except Exception as e:
        logger.error(f"❌ Error getting user ID: {e}")
        user_id = None

    # ============ 4. 调用业务逻辑（包含异常捕获） ============
    try:
        logger.info("📊 Calling get_moments_feed...")
        moments = get_moments_feed(
            limit=limit,
            offset=offset,
            user_id=user_id,
            lang=lang or "",
            filter_lang=filter_lang or "",
            gender=gender or "",
            orientation=orientation or "",
        )
        logger.info(f"   ✅ Retrieved {len(moments)} moments")

        # 获取评论
        logger.info("📊 Getting comments for moments...")
        for idx, moment in enumerate(moments):
            try:
                moment["comments"] = get_moment_comments(
                    moment["id"],
                    limit=10,
                    current_user_id=user_id
                )
                logger.debug(f"   Moment {idx + 1}: ID={moment['id']}, {len(moment['comments'])} comments")
            except Exception as e:
                logger.error(f"   ❌ Error getting comments for moment {moment.get('id')}: {e}")
                moment["comments"] = []  # 设置默认值

        # 获取总数
        logger.info("📊 Counting total moments...")
        total = count_moments_feed(
            filter_lang=filter_lang or "",
            gender=gender or "",
            orientation=orientation or "",
        )
        logger.info(f"   ✅ Total: {total}")

        response = {"moments": moments, "total": total}
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

# POST 路由必须在 GET {moment_id} 之前，避免路径冲突
@router.post("/api/moments/{moment_id}/like", summary="点赞/取消点赞")
async def api_toggle_like(
    moment_id: int,
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """点赞或取消点赞 - 需要登录"""
    # user_id 已通过 require_permissions 从中间件获取
    result = toggle_like(moment_id, user_id=user_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "点赞失败"))
    return result


@router.post("/api/moments/{moment_id}/comment", summary="发表评论")
async def api_add_comment(
    moment_id: int,
    x_device_id: Optional[str] = Header(None),
    content: str = Body(..., embed=True),
    parent_id: Optional[int] = Body(None, embed=True),
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """用户发表评论或回复评论"""
    device_id = _get_device_id(x_device_id)
    # user_id 已通过 require_permissions 从中间件获取，无需重复解析
    result = add_user_comment(moment_id, device_id, content, parent_id, user_id=user_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "评论失败"))
    return result


@router.post("/api/moments/{moment_id}/regenerate-image", summary="重新生成配图")
async def api_regenerate_moment_image(
    moment_id: int,
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """根据朋友圈文案重新生成配图"""
    new_url = regenerate_moment_image(moment_id)
    if not new_url:
        raise HTTPException(status_code=404, detail="朋友圈不存在或无文案")
    return {"ok": True, "image_url": new_url}


# GET {moment_id} 必须在所有 POST {moment_id}/xxx 之后
@router.get("/api/moments/{moment_id}", summary="获取朋友圈详情")
async def api_get_moment_detail(
    moment_id: int,
    x_token: Optional[str] = Header(None),
):
    """获取单条朋友圈详情，包含评论"""
    user_id = _get_user_id(x_token)
    moment = get_moment_detail(moment_id, user_id=user_id)
    if not moment:
        raise HTTPException(status_code=404, detail="朋友圈不存在")
    moment["comments"] = get_moment_comments(moment_id, limit=100, current_user_id=user_id)
    return moment


@router.get("/api/companions/{companion_id}/moments", summary="获取伴侣的所有朋友圈")
async def api_companion_moments(
    companion_id: str,
    limit: int = Query(20, ge=1, le=100),
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """获取某个伴侣的所有朋友圈"""
    moments = get_companion_moments(companion_id, limit=limit)
    return {"moments": moments, "total": len(moments)}
