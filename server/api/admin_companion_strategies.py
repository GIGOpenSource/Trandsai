"""【单体消息策略】后台 CRUD（跨智能体列表 + 增删改）。

三类策略勿混用：
1. 单体消息策略 companion_strategies —— 本模块；某智能体专属埋点招呼文案
2. 埋点消息策略 msg-strategy —— 全局控制/规则/策略库（埋点主动下发）
3. 快速回复 quick-reply —— 用户发聊天消息后关键词/兜底回，跳过 AI

单智能体嵌套路径仍保留：
  /api/admin/content/companions/{companion_id}/strategies
与本模块操作同一张表，本模块可一次查「所有已配置单体策略」的文案。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import Depends, HTTPException, Query
from sqlalchemy import case, func

from api.admin_common import _get_error_msg, admin_auth_required, get_admin_lang, router
from core.database import CompanionORM, CompanionStrategyORM, get_db
from core.response import ok, paginated
from services.companion_content import (
    SUPPORTED_LANGUAGES,
    strategy_to_dict,
    validate_trigger_event,
)
from services.im_admin import (
    assert_companion_in_scope,
    companion_scope_sql_ids,
    resolve_admin_actor,
)
from services.msg_strategy_crud import load_companion_id_name_map
from services.strategy_image import normalize_strategy_image_url


def _norm_cid(companion_id: str, lang: str = "zh") -> str:
    cid = (companion_id or "").strip()[:8]
    if not cid:
        raise HTTPException(status_code=400, detail=_get_error_msg("companion_not_found", lang))
    return cid


def _require_scope(token: str, companion_id: str, lang: str = "zh") -> dict:
    actor = resolve_admin_actor(token)
    assert_companion_in_scope(actor, companion_id, lang)
    return actor


def _apply_scope(q, token: str):
    ids = companion_scope_sql_ids(resolve_admin_actor(token))
    if ids is None:
        return q
    return q.filter(CompanionStrategyORM.companion_id.in_(ids))


def _enrich_rows(db, rows: List[CompanionStrategyORM]) -> list:
    """以策略行（文案）为主；同一智能体多条策略 = 多行。附带智能体名称/头像。"""
    cids = list({str(r.companion_id) for r in rows if r.companion_id})
    name_map = load_companion_id_name_map(db, cids)
    avatar_map: dict = {}
    if cids:
        for cid, avatar in (
            db.query(CompanionORM.id, CompanionORM.avatar_url)
            .filter(CompanionORM.id.in_(cids))
            .all()
        ):
            avatar_map[str(cid)] = (avatar or "") if avatar != "__GENERATING__" else ""
    items = []
    for r in rows:
        cid = str(r.companion_id or "")
        d = strategy_to_dict(r, companion_name=name_map.get(cid, ""))
        d["companion_avatar"] = avatar_map.get(cid, "")
        items.append(d)
    return items


# ---------- 列表：全部单体消息策略 ----------


@router.get(
    "/api/admin/companion-strategies",
    summary="单体消息策略列表（跨智能体）",
)
async def list_companion_strategies(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    companion_id: Optional[str] = Query(None, description="筛某智能体"),
    companion_name: Optional[str] = Query(None, description="智能体昵称模糊"),
    language: Optional[str] = None,
    trigger_event: Optional[str] = Query(None, description="埋点事件名"),
    strategy_type: Optional[str] = Query(None, description="兼容旧参数=trigger_event"),
    is_active: Optional[bool] = None,
    keyword: Optional[str] = Query(None, description="文案内容模糊"),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    event_filter = (trigger_event or strategy_type or "").strip()
    with get_db() as db:
        q = db.query(CompanionStrategyORM)
        q = _apply_scope(q, _token)
        if companion_id and companion_id.strip():
            q = q.filter(CompanionStrategyORM.companion_id == companion_id.strip()[:8])
        if companion_name and companion_name.strip():
            name_like = f"%{companion_name.strip()}%"
            q = q.filter(
                CompanionStrategyORM.companion_id.in_(
                    db.query(CompanionORM.id).filter(CompanionORM.name.ilike(name_like))
                )
            )
        if language:
            q = q.filter(CompanionStrategyORM.language == language.strip())
        if event_filter:
            q = q.filter(
                (CompanionStrategyORM.trigger_event == event_filter)
                | (CompanionStrategyORM.strategy_type == event_filter)
            )
        if is_active is not None:
            q = q.filter(CompanionStrategyORM.is_active == is_active)
        if keyword and keyword.strip():
            q = q.filter(CompanionStrategyORM.content.ilike(f"%{keyword.strip()}%"))
        total = q.count()
        rows = (
            q.order_by(
                CompanionStrategyORM.companion_id.asc(),
                CompanionStrategyORM.sort_order.asc(),
                CompanionStrategyORM.id.asc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return paginated(
            items=_enrich_rows(db, rows),
            total=total,
            page=page,
            page_size=page_size,
        )


@router.get(
    "/api/admin/companion-strategies/companions",
    summary="已配置单体消息策略的智能体汇总",
)
async def list_companions_with_strategies(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    companion_name: Optional[str] = None,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """每个智能体一行：策略条数、生效条数。便于管理页先选智能体再进详情。"""
    with get_db() as db:
        scope_ids = companion_scope_sql_ids(resolve_admin_actor(_token))
        base = (
            db.query(
                CompanionStrategyORM.companion_id,
                func.count(CompanionStrategyORM.id).label("strategy_count"),
                func.sum(
                    case(
                        (CompanionStrategyORM.is_active.is_(True), 1),
                        else_=0,
                    )
                ).label("active_count"),
            )
            .group_by(CompanionStrategyORM.companion_id)
        )
        if scope_ids is not None:
            base = base.filter(CompanionStrategyORM.companion_id.in_(scope_ids))

        sub = base.subquery()
        q = db.query(CompanionORM, sub.c.strategy_count, sub.c.active_count).join(
            sub, CompanionORM.id == sub.c.companion_id
        )
        if companion_name and companion_name.strip():
            q = q.filter(CompanionORM.name.ilike(f"%{companion_name.strip()}%"))
        total = q.count()
        rows = (
            q.order_by(CompanionORM.name.asc(), CompanionORM.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [
            {
                "companion_id": c.id,
                "companion_name": c.name or "",
                "avatar_url": c.avatar_url or "",
                "language": c.language or "zh",
                "strategy_count": int(sc or 0),
                "active_count": int(ac or 0),
            }
            for c, sc, ac in rows
        ]
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/api/admin/companion-strategies/{strategy_id}",
    summary="单体消息策略详情",
)
async def get_companion_strategy(
    strategy_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    with get_db() as db:
        row = (
            db.query(CompanionStrategyORM)
            .filter(CompanionStrategyORM.id == strategy_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="strategy_not_found")
        _require_scope(_token, row.companion_id, lang)
        items = _enrich_rows(db, [row])
        return ok(data=items[0] if items else strategy_to_dict(row))


@router.post(
    "/api/admin/companion-strategies",
    summary="新建单体消息策略",
)
async def create_companion_strategy(
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    companion_id = _norm_cid(data.get("companion_id") or "", lang)
    _require_scope(_token, companion_id, lang)
    trigger_event = (data.get("trigger_event") or data.get("strategy_type") or "").strip()
    content = (data.get("content") or "").strip()
    language = (data.get("language") or "zh").strip()
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="unsupported_language")
    if not content:
        raise HTTPException(status_code=400, detail="content_required")

    with get_db() as db:
        if not db.query(CompanionORM.id).filter(CompanionORM.id == companion_id).first():
            raise HTTPException(
                status_code=404, detail=_get_error_msg("companion_not_found", lang)
            )
        try:
            trigger_event = validate_trigger_event(trigger_event, db)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_trigger_event")
        row = CompanionStrategyORM(
            companion_id=companion_id,
            strategy_type=trigger_event,
            content=content,
            image_url=normalize_strategy_image_url(data.get("image_url")),
            language=language,
            trigger_event=trigger_event,
            source=(data.get("source") or "manual").strip()[:20],
            sort_order=int(data.get("sort_order") or 0),
            is_active=bool(data.get("is_active", True)),
            created_by=(data.get("created_by") or "").strip()[:64],
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        name_map = load_companion_id_name_map(db, [companion_id])
        return ok(
            data=strategy_to_dict(
                row, companion_name=name_map.get(companion_id, "")
            )
        )


@router.put(
    "/api/admin/companion-strategies/{strategy_id}",
    summary="更新单体消息策略",
)
async def update_companion_strategy(
    strategy_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    with get_db() as db:
        row = (
            db.query(CompanionStrategyORM)
            .filter(CompanionStrategyORM.id == strategy_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="strategy_not_found")
        _require_scope(_token, row.companion_id, lang)
        if "companion_id" in data:
            new_cid = _norm_cid(data.get("companion_id") or "", lang)
            if new_cid != row.companion_id:
                raise HTTPException(status_code=400, detail="companion_id_immutable")
        if "trigger_event" in data or "strategy_type" in data:
            raw = (data.get("trigger_event") or data.get("strategy_type") or "").strip()
            try:
                ev = validate_trigger_event(raw, db)
            except ValueError:
                raise HTTPException(status_code=400, detail="invalid_trigger_event")
            row.trigger_event = ev
            row.strategy_type = ev
        if "content" in data:
            content = (data.get("content") or "").strip()
            if not content:
                raise HTTPException(status_code=400, detail="content_required")
            row.content = content
        if "image_url" in data:
            row.image_url = normalize_strategy_image_url(data.get("image_url"))
        if "language" in data:
            lk = (data.get("language") or "").strip()
            if lk not in SUPPORTED_LANGUAGES:
                raise HTTPException(status_code=400, detail="unsupported_language")
            row.language = lk
        if "sort_order" in data:
            row.sort_order = int(data.get("sort_order") or 0)
        if "is_active" in data:
            row.is_active = bool(data.get("is_active"))
        if "source" in data:
            row.source = (data.get("source") or "manual").strip()[:20]
        db.commit()
        db.refresh(row)
        name_map = load_companion_id_name_map(db, [row.companion_id])
        return ok(
            data=strategy_to_dict(
                row, companion_name=name_map.get(row.companion_id or "", "")
            )
        )


@router.patch(
    "/api/admin/companion-strategies/{strategy_id}/active",
    summary="单体消息策略开关",
)
async def patch_companion_strategy_active(
    strategy_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    with get_db() as db:
        row = (
            db.query(CompanionStrategyORM)
            .filter(CompanionStrategyORM.id == strategy_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="strategy_not_found")
        _require_scope(_token, row.companion_id, lang)
        row.is_active = bool(data.get("is_active", True))
        db.commit()
        return ok(data={"id": strategy_id, "is_active": bool(row.is_active)})


@router.delete(
    "/api/admin/companion-strategies/{strategy_id}",
    summary="删除单体消息策略",
)
async def delete_companion_strategy(
    strategy_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    with get_db() as db:
        row = (
            db.query(CompanionStrategyORM)
            .filter(CompanionStrategyORM.id == strategy_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="strategy_not_found")
        _require_scope(_token, row.companion_id, lang)
        db.delete(row)
        db.commit()
        return ok(data={"deleted_id": strategy_id})
