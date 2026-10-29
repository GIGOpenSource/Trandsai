"""管理端媒体代理（GCS/COS 同源转发，供裁切读像素）。"""
from __future__ import annotations

from urllib.parse import unquote

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response

from api.admin_common import _get_error_msg, admin_auth_required, get_admin_lang, router


@router.get(
    "/api/admin/media/proxy",
    summary="同源代理 GCS/COS 图片（裁切/canvas 用）",
    responses={
        200: {
            "content": {
                "image/png": {},
                "image/jpeg": {},
                "image/webp": {},
                "image/gif": {},
            },
            "description": "图片二进制",
        }
    },
)
async def admin_media_proxy(
    url: str = Query(..., description="原始公开图片 URL（GCS/COS）"),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """浏览器 <img> 展示仍用原始 URL；仅编辑裁切需读像素时走本接口。"""
    from services.media_proxy import fetch_media

    raw = unquote((url or "").strip())
    try:
        data, content_type = fetch_media(raw)
    except ValueError as e:
        code = str(e)
        if code == "url_not_allowed":
            raise HTTPException(status_code=400, detail="url_not_allowed")
        if code == "not_image":
            raise HTTPException(status_code=400, detail="not_image")
        if code == "too_large":
            raise HTTPException(status_code=400, detail="too_large")
        raise HTTPException(
            status_code=502,
            detail=_get_error_msg("server_error", lang),
        )
    except Exception:
        raise HTTPException(
            status_code=502,
            detail=_get_error_msg("server_error", lang),
        )

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )
