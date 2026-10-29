"""Admin moments routes (list / generate / CRUD). Registers on admin_common.router."""
import asyncio
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func

from api.admin_common import (
    _get_error_msg,
    admin_auth_required,
    get_admin_lang,
    router,
)
from core.database import CompanionORM, MomentORM, get_db, serialize_datetime, serialize_datetime_beijing
from core.state import get_companion_manager
from services.culture_data import infer_language_from_city
from services.image_generation import generate_image_with_cache, generate_moment_image_prompt


@router.get("/api/admin/moments", summary="获取朋友圈列表")
async def admin_list_moments(
    limit: int = 50,
    offset: int = 0,
    lang: Optional[str] = None,
    _token: str = Depends(admin_auth_required),
    admin_lang: str = Depends(get_admin_lang),
):
    from services.im_admin import companion_in_scope, companion_scope_sql_ids, resolve_admin_actor
    from services.moments import get_moments_feed

    actor = resolve_admin_actor(_token)
    scope_ids = companion_scope_sql_ids(actor)
    effective_lang = lang or admin_lang
    moments = get_moments_feed(limit=limit, offset=offset, lang=effective_lang, waterfall=False)
    if scope_ids is not None:
        moments = [
            m
            for m in moments
            if companion_in_scope(actor, (m.get("companion_id") or m.get("companionId") or ""))
        ]
    with get_db() as db:
        q = db.query(func.count(MomentORM.id))
        if scope_ids is not None:
            q = q.filter(MomentORM.companion_id.in_(scope_ids))
        total = q.scalar() or 0
    return {"moments": moments, "total": total}


@router.delete("/api/admin/moments", summary="批量删除朋友圈")
async def admin_clear_all_moments(_token: str = Depends(admin_auth_required)):
    from services.moments import clear_all_moments

    count = clear_all_moments()
    return {"ok": True, "deleted": count}


@router.post("/api/admin/moments/regenerate", summary="批量重新生成朋友圈")
async def admin_regenerate_moments(
    data: Optional[dict] = None, _token: str = Depends(admin_auth_required)
):
    from services.moments import regenerate_moments_for_all
    import asyncio

    cm = get_companion_manager()
    moments_per = 3
    if data and isinstance(data, dict):
        moments_per = data.get("moments_per_companion", 3)
    loop = asyncio.get_event_loop()
    created = await loop.run_in_executor(None, regenerate_moments_for_all, cm, moments_per)
    return {"ok": True, "created": created}


@router.post("/api/admin/moments/batch-generate", summary="批量生成朋友圈")
async def admin_batch_generate_moments(data: dict, _token: str = Depends(admin_auth_required)):
    from services.moments import regenerate_moments_for_all, create_moment_for_companion
    import asyncio

    cm = get_companion_manager()
    clear_existing = data.get("clear_existing", False)
    moments_per = data.get("moments_per_companion", 3)
    loop = asyncio.get_event_loop()

    if clear_existing:
        created = await loop.run_in_executor(None, regenerate_moments_for_all, cm, moments_per)
        return {"ok": True, "created": created, "mode": "clear_and_regenerate"}

    created = 0
    companions = cm.list_all()
    for c in companions:
        if c.get("is_deleted"):
            continue
        companion_id = c.get("profile", {}).get("id")
        if not companion_id:
            continue
        companion = cm.get(companion_id)
        if not companion:
            continue
        for _ in range(moments_per):
            result = await loop.run_in_executor(None, create_moment_for_companion, companion, cm)
            if result:
                created += 1
    return {"ok": True, "created": created, "mode": "append"}


@router.post(
    "/api/admin/moments/generate-image",
    summary="生成朋友圈配图（仅图片，返回二进制）",
    responses={
        200: {
            "content": {"image/png": {}, "image/jpeg": {}, "image/webp": {}},
            "description": "生成的配图二进制",
        }
    },
)
async def admin_generate_moment_image(
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """根据智能体人设 + 文案生成配图，不落库；前端可多次调用重新生成。"""
    from services.image_generation import ImageGenerationError
    from services.im_admin import assert_companion_in_scope, resolve_admin_actor
    from services.moment_image import generate_moment_image_bytes

    companion_id = (data.get("companion_id") or "").strip()
    caption = (data.get("caption") or "").strip()
    if not companion_id or not caption:
        raise HTTPException(
            status_code=400, detail=_get_error_msg("companion_id_caption_required", lang)
        )
    assert_companion_in_scope(resolve_admin_actor(_token), companion_id, lang)

    loop = asyncio.get_event_loop()
    try:
        image_bytes, media_type = await loop.run_in_executor(
            None, generate_moment_image_bytes, companion_id, caption
        )
    except ValueError as e:
        code = str(e)
        if code == "companion_not_found":
            raise HTTPException(status_code=404, detail=_get_error_msg("companion_not_found", lang))
        if code == "caption_required":
            raise HTTPException(
                status_code=400, detail=_get_error_msg("companion_id_caption_required", lang)
            )
        raise HTTPException(status_code=400, detail=str(e))
    except ImageGenerationError as e:
        raise HTTPException(
            status_code=502,
            detail=f"{_get_error_msg('image_generation_failed', lang)}: {e}",
        )

    filename = f"moment-{companion_id[:8]}.png"
    if media_type == "image/webp":
        filename = f"moment-{companion_id[:8]}.webp"
    elif media_type == "image/jpeg":
        filename = f"moment-{companion_id[:8]}.jpg"

    return Response(
        content=image_bytes,
        media_type=media_type or "image/png",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.delete("/api/admin/moments/{moment_id}", summary="删除朋友圈")
async def admin_delete_moment(
    moment_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.im_admin import assert_companion_in_scope, resolve_admin_actor
    from services.moments import delete_moment

    with get_db() as db:
        row = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=_get_error_msg("moment_not_found", lang))
        assert_companion_in_scope(resolve_admin_actor(_token), row.companion_id, lang)

    ok = delete_moment(moment_id)
    if not ok:
        raise HTTPException(status_code=404, detail=_get_error_msg("moment_not_found", lang))
    return {"ok": True}


@router.post("/api/admin/moments/{moment_id}/regenerate-image", summary="重新生成朋友圈配图")
async def admin_regenerate_moment_image(
    moment_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.image_generation import ImageGenerationError
    from services.im_admin import assert_companion_in_scope, resolve_admin_actor
    from services.moments import regenerate_moment_image

    with get_db() as db:
        row = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=_get_error_msg("moment_not_found", lang))
        assert_companion_in_scope(resolve_admin_actor(_token), row.companion_id, lang)

    try:
        new_url = regenerate_moment_image(moment_id)
    except ImageGenerationError as e:
        raise HTTPException(
            status_code=502,
            detail=f"{_get_error_msg('image_generation_failed', lang)}: {e}",
        )
    if not new_url:
        raise HTTPException(status_code=404, detail=_get_error_msg("moment_no_caption", lang))
    return {"ok": True, "image_url": new_url}


@router.post("/api/admin/moments", summary="创建朋友圈")
async def admin_create_moment(
    data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)
):
    from services.im_admin import assert_companion_in_scope, resolve_admin_actor

    companion_id = data.get("companion_id", "").strip()
    caption = data.get("caption", "").strip()
    from services.moment_urls import normalize_preview_pos, optional_image_url

    image_url = optional_image_url(data.get("image_url"))
    try:
        preview_pos = normalize_preview_pos(data.get("preview_pos")) if "preview_pos" in data else None
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_preview_pos")
    if not companion_id or not caption:
        raise HTTPException(
            status_code=400, detail=_get_error_msg("companion_id_caption_required", lang)
        )
    assert_companion_in_scope(resolve_admin_actor(_token), companion_id, lang)

    with get_db() as db:
        companion = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
        if not companion:
            raise HTTPException(status_code=404, detail=_get_error_msg("companion_not_found", lang))

        companion_language = (companion.language or "").strip()
        companion_city = (companion.city or "").strip()

        if not image_url:
            profile_dict = {
                "gender": companion.gender or "女",
                "age": companion.age or 22,
                "city": companion_city,
                "personality": companion.personality or "",
                "hobbies": companion.hobbies or "",
                "mbti": companion.mbti or "",
            }
            img_prompt, img_style = generate_moment_image_prompt(caption, profile=profile_dict)
            try:
                from services.image_generation import ImageGenerationError

                image_url = generate_image_with_cache(
                    img_prompt, style=img_style, width=600, height=600
                )
            except ImageGenerationError as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"{_get_error_msg('image_generation_failed', lang)}: {e}",
                )

        caption_lang = companion_language or infer_language_from_city(companion_city)

        moment = MomentORM(
            companion_id=companion_id,
            caption=caption,
            image_url=image_url or None,
            preview_url=optional_image_url(data.get("preview_url")),
            preview_pos=preview_pos if "preview_pos" in data else None,
            caption_lang=caption_lang,
        )
        db.add(moment)
        db.flush()
        moment_id = moment.id
        created_at = moment.created_at

    return {
        "id": moment_id,
        "companion_id": companion_id,
        "caption": caption,
        "image_url": image_url,
        "preview_url": optional_image_url(data.get("preview_url")),
        "preview_pos": preview_pos if "preview_pos" in data else None,
        "likes_count": 0,
        "comments_count": 0,
        "created_at": serialize_datetime(created_at),
    }


@router.put("/api/admin/moments/{moment_id}", summary="更新朋友圈")
async def admin_update_moment(
    moment_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.im_admin import assert_companion_in_scope, resolve_admin_actor

    actor = resolve_admin_actor(_token)
    with get_db() as db:
        moment = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
        if not moment:
            raise HTTPException(status_code=404, detail=_get_error_msg("moment_not_found", lang))
        assert_companion_in_scope(actor, moment.companion_id, lang)
        if "caption" in data:
            moment.caption = data["caption"].strip()
        if "image_url" in data:
            from services.moment_urls import optional_image_url

            moment.image_url = optional_image_url(data.get("image_url"))
        if "preview_url" in data:
            from services.moment_urls import optional_image_url

            moment.preview_url = optional_image_url(data.get("preview_url"))
        if "preview_pos" in data:
            from services.moment_urls import normalize_preview_pos

            try:
                moment.preview_pos = normalize_preview_pos(data.get("preview_pos"))
            except ValueError:
                raise HTTPException(status_code=400, detail="invalid_preview_pos")
        if "companion_id" in data:
            new_cid = data["companion_id"].strip()
            assert_companion_in_scope(actor, new_cid, lang)
            moment.companion_id = new_cid
        if "created_at" in data:
            raise HTTPException(status_code=400, detail="moment_created_at_readonly")
        db.commit()
        return {
            "id": moment.id,
            "companion_id": moment.companion_id,
            "caption": moment.caption,
            "image_url": moment.image_url,
            "preview_url": getattr(moment, "preview_url", None),
            "preview_pos": getattr(moment, "preview_pos", None),
            "likes_count": moment.likes_count,
            "comments_count": moment.comments_count,
            "created_at": serialize_datetime_beijing(moment.created_at),
        }
