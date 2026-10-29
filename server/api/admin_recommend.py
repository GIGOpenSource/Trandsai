"""管理端推荐策略 / 标签字典 / 试算。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm.attributes import flag_modified

from api.admin_common import admin_auth_required, get_admin_lang, router
from core.i18n import _
from core.response import fail, ok, paginated
from core.database import (
    CompanionORM,
    MomentORM,
    RecommendStrategyORM,
    RecommendTagDefORM,
    UserORM,
    get_db,
    serialize_datetime,
)
from services.recommend import (
    apply_agent_tag_filters,
    apply_waterfall_to_companion_query,
    bump_recommend_cache,
    load_tag_defs,
    reload_companion_memory_cache,
    resolve_for_request,
    validate_strategy_tags,
)
from services.recommend_tags import (
    APPLY_TO as APPLY_TO_VALUES,
    SIDES as SIDE_VALUES,
    TAG_SOURCES,
    list_tag_def_groups,
    list_tag_defs,
    normalize_surface,
    normalize_tag_list,
    strategies_using_tag,
    tag_def_to_dict,
)

SIDES = set(SIDE_VALUES)
APPLY_TO = set(APPLY_TO_VALUES)
SEED_IMMUTABLE = "seed"
LIST_SURFACES = {"companions_all", "companions_recommend", "companions"}


class StrategyIn(BaseModel):
    name: str = ""
    enabled: bool = True
    priority: int = 0
    apply_to: str = "both"
    user_tags: list[str] = Field(default_factory=list)
    agent_tags: list[str] = Field(default_factory=list)
    user_match: str = "any"
    agent_match: str = "any"


class StrategyPatch(BaseModel):
    enabled: bool


class TagDefIn(BaseModel):
    key: str
    side: str
    group: str
    label_zh: str = ""
    label_en: str = ""
    enabled: bool = True


class TagDefPatch(BaseModel):
    group: Optional[str] = None
    label_zh: Optional[str] = None
    label_en: Optional[str] = None
    enabled: Optional[bool] = None


class PreviewIn(BaseModel):
    user_id: int
    surface: str = "companions_all"
    lang: Optional[str] = None


def _strategy_out(row: RecommendStrategyORM) -> dict:
    return {
        "id": row.id,
        "name": row.name or "",
        "enabled": bool(row.enabled),
        "priority": int(row.priority or 0),
        "apply_to": row.apply_to,
        "user_tags": normalize_tag_list(row.user_tags),
        "agent_tags": normalize_tag_list(row.agent_tags),
        "user_match": row.user_match or "any",
        "agent_match": row.agent_match or "any",
        "created_by": row.created_by or "",
        "created_at": serialize_datetime(row.created_at),
        "updated_at": serialize_datetime(row.updated_at),
    }


def _tag_out(row: RecommendTagDefORM) -> dict:
    return tag_def_to_dict(row)


def _after_save() -> None:
    bump_recommend_cache()
    reload_companion_memory_cache()


def _validate_apply_to(apply_to: str) -> str:
    v = (apply_to or "both").strip()
    if v not in APPLY_TO:
        raise HTTPException(status_code=400, detail=_("apply_to 必须是 companions_all / companions_recommend / moments / both"))
    return v


def _check_tags(user_tags, agent_tags) -> None:
    err = validate_strategy_tags(user_tags, agent_tags, tag_defs=load_tag_defs())
    if err == "both_sides_empty":
        raise HTTPException(status_code=400, detail=_("用户侧与智能体侧不能同时为全部"))
    if err:
        raise HTTPException(status_code=400, detail=err)


@router.get("/api/admin/recommend/ad-groups", summary="广告系列/GCLID 列表（供策略绑定）")
async def admin_list_ad_groups(_token: str = Depends(admin_auth_required)):
    from core.response import ok
    from services.ad_attribution import GENERIC_AD_USER_TAG, list_ad_group_options

    payload = {
        "specific": [],
        "generic": {
            "id": "ads",
            "label": "任意广告来源用户",
            "kind": "generic",
            "user_tag": GENERIC_AD_USER_TAG,
            "source": "attribution",
            "count": 0,
            "hint": "匹配 ua:source:ads；精准选项来自 gad_campaignid / gclid 两列",
        },
    }
    try:
        with get_db() as db:
            payload = list_ad_group_options(db)
            # upsert 失败不得拖垮整份选项（完整 gclid 会超 tag key 64 限制）
            try:
                from services.recommend_tags import upsert_ua_tag_def

                for item in payload.get("specific") or []:
                    tag = item.get("user_tag") or ""
                    if tag.startswith("ua:gclid:"):
                        continue
                    upsert_ua_tag_def(db, tag)
                upsert_ua_tag_def(db, (payload.get("generic") or {}).get("user_tag") or "")
            except Exception:
                import logging

                logging.getLogger(__name__).warning(
                    "ad-groups upsert_ua_tag_def skipped", exc_info=True
                )
        return ok(data=payload)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("admin_list_ad_groups failed")
        return ok(data=payload)


@router.get("/api/admin/recommend/strategies", summary="推荐策略列表")
async def admin_list_strategies(_token: str = Depends(admin_auth_required)):
    with get_db() as db:
        rows = (
            db.query(RecommendStrategyORM)
            .order_by(RecommendStrategyORM.priority.desc(), RecommendStrategyORM.id.asc())
            .all()
        )
        return [_strategy_out(r) for r in rows]


@router.get("/api/admin/recommend/strategies/{strategy_id}", summary="推荐策略详情")
async def admin_get_strategy(strategy_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(RecommendStrategyORM).filter(RecommendStrategyORM.id == strategy_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="strategy not found")
        return _strategy_out(row)


@router.post("/api/admin/recommend/strategies", summary="创建推荐策略")
async def admin_create_strategy(body: StrategyIn, _token: str = Depends(admin_auth_required)):
    apply_to = _validate_apply_to(body.apply_to)
    user_tags = normalize_tag_list(body.user_tags)
    agent_tags = normalize_tag_list(body.agent_tags)
    _check_tags(user_tags, agent_tags)
    with get_db() as db:
        from services.recommend_tags import upsert_ua_tag_def

        for tag in user_tags:
            upsert_ua_tag_def(db, tag)
        row = RecommendStrategyORM(
            name=(body.name or "").strip()[:100],
            enabled=bool(body.enabled),
            priority=int(body.priority or 0),
            apply_to=apply_to,
            user_tags=user_tags,
            agent_tags=agent_tags,
            user_match=(body.user_match or "any")[:8],
            agent_match=(body.agent_match or "any")[:8],
            created_by="admin",
        )
        db.add(row)
        db.flush()
        out = _strategy_out(row)
    _after_save()
    return out


@router.put("/api/admin/recommend/strategies/{strategy_id}", summary="更新推荐策略")
async def admin_update_strategy(
    strategy_id: int,
    body: StrategyIn,
    _token: str = Depends(admin_auth_required),
):
    apply_to = _validate_apply_to(body.apply_to)
    user_tags = normalize_tag_list(body.user_tags)
    agent_tags = normalize_tag_list(body.agent_tags)
    _check_tags(user_tags, agent_tags)
    with get_db() as db:
        from services.recommend_tags import upsert_ua_tag_def

        for tag in user_tags:
            upsert_ua_tag_def(db, tag)
        row = db.query(RecommendStrategyORM).filter(RecommendStrategyORM.id == strategy_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="strategy not found")
        row.name = (body.name or "").strip()[:100]
        row.enabled = bool(body.enabled)
        row.priority = int(body.priority or 0)
        row.apply_to = apply_to
        row.user_tags = user_tags
        row.agent_tags = agent_tags
        flag_modified(row, "user_tags")
        flag_modified(row, "agent_tags")
        row.user_match = (body.user_match or "any")[:8]
        row.agent_match = (body.agent_match or "any")[:8]
        row.updated_at = datetime.now(timezone.utc)
        out = _strategy_out(row)
    _after_save()
    return out


@router.patch("/api/admin/recommend/strategies/{strategy_id}", summary="仅开关策略")
async def admin_patch_strategy(
    strategy_id: int,
    body: StrategyPatch,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        row = db.query(RecommendStrategyORM).filter(RecommendStrategyORM.id == strategy_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="strategy not found")
        row.enabled = bool(body.enabled)
        row.updated_at = datetime.now(timezone.utc)
        out = _strategy_out(row)
    _after_save()
    return out


@router.delete("/api/admin/recommend/strategies/{strategy_id}", summary="删除推荐策略")
async def admin_delete_strategy(strategy_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(RecommendStrategyORM).filter(RecommendStrategyORM.id == strategy_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="strategy not found")
        db.delete(row)
    _after_save()
    return {"ok": True}


@router.get("/api/admin/recommend/tag-defs", summary="标签字典（全量，供其它页下拉）")
async def admin_list_tag_defs(
    side: Optional[str] = None,
    _token: str = Depends(admin_auth_required),
):
    """不分页，直接返回数组。标签字典管理页请用 /tag-dictionary。"""
    with get_db() as db:
        q = db.query(RecommendTagDefORM)
        if side in SIDES:
            q = q.filter(RecommendTagDefORM.side == side)
        rows = q.order_by(RecommendTagDefORM.side, RecommendTagDefORM.group, RecommendTagDefORM.key).all()
        return [_tag_out(r) for r in rows]


def _load_tag(db, side: str, key: str):
    side = (side or "").strip()
    key = (key or "").strip()
    if side not in SIDES or not key:
        return None, fail(message=_("key / side 不合法"), code=400)
    row = (
        db.query(RecommendTagDefORM)
        .filter(RecommendTagDefORM.key == key, RecommendTagDefORM.side == side)
        .first()
    )
    if not row:
        return None, fail(message="tag def not found", code=404)
    return row, None


def _apply_tag_patch(row: RecommendTagDefORM, body: TagDefPatch):
    if row.source == SEED_IMMUTABLE and body.group is not None:
        new_group = body.group.strip()[:32] or row.group
        if new_group != row.group:
            return fail(message="seed tag key/group cannot be changed", code=400)
    if body.group is not None:
        row.group = body.group.strip()[:32] or row.group
    if body.label_zh is not None:
        row.label_zh = body.label_zh[:100]
    if body.label_en is not None:
        row.label_en = body.label_en[:100]
    if body.enabled is not None:
        row.enabled = bool(body.enabled)
    return None


def _validate_create(key: str, side: str, group: str):
    if not key or side not in SIDES or not group:
        return fail(message=_("key / side / group 必填"), code=400)
    if key in ("男", "女") or "男" in key or "女" in key:
        return fail(message=_("tags 禁止使用 男/女"), code=400)
    if len(key) > 64:
        return fail(message=_("key 最长 64"), code=400)
    return None


# ---------- 标签字典管理（分页 + 标准信封） ----------


@router.get("/api/admin/recommend/tag-dictionary/meta", summary="标签字典筛选项")
async def admin_tag_dictionary_meta(_token: str = Depends(admin_auth_required)):
    with get_db() as db:
        groups = list_tag_def_groups(db)
    return ok(
        data={
            "sides": [
                {"value": "user", "label_zh": "用户侧", "label_en": "User"},
                {"value": "agent", "label_zh": "智能体侧", "label_en": "Agent"},
            ],
            "sources": [
                {"value": "seed", "label_zh": "种子", "label_en": "Seed"},
                {"value": "ops", "label_zh": "运营", "label_en": "Ops"},
                {"value": "ua_auto", "label_zh": "广告自动", "label_en": "UA auto"},
            ],
            "groups": groups,
        }
    )


@router.get("/api/admin/recommend/tag-dictionary", summary="标签字典管理列表")
async def admin_list_tag_dictionary(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    side: Optional[str] = Query(None, description="user / agent"),
    group: Optional[str] = Query(None, description="分组，精确"),
    key: Optional[str] = Query(None, description="标签键，模糊"),
    label_zh: Optional[str] = Query(None, description="中文名，模糊"),
    label_en: Optional[str] = Query(None, description="英文名，模糊"),
    enabled: Optional[bool] = Query(None, description="是否启用"),
    source: Optional[str] = Query(None, description="seed / ops / ua_auto"),
    _token: str = Depends(admin_auth_required),
):
    side_v = (side or "").strip()
    if side_v and side_v not in SIDES:
        return fail(message=_("side 必须是 user 或 agent"), code=400)
    source_v = (source or "").strip()
    if source_v and source_v not in TAG_SOURCES:
        return fail(message=_("source 必须是 seed / ops / ua_auto"), code=400)
    with get_db() as db:
        items, total = list_tag_defs(
            db,
            page=page,
            page_size=page_size,
            side=side_v or None,
            group=group,
            key=key,
            label_zh=label_zh,
            label_en=label_en,
            enabled=enabled,
            source=source_v or None,
        )
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/recommend/tag-dictionary/{side}/{key}", summary="标签字典详情")
async def admin_get_tag_dictionary(side: str, key: str, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row, err = _load_tag(db, side, key)
        if err:
            return err
        return ok(data=_tag_out(row))


@router.post("/api/admin/recommend/tag-dictionary", summary="新建标签")
async def admin_create_tag_dictionary(body: TagDefIn, _token: str = Depends(admin_auth_required)):
    key = (body.key or "").strip()
    side = (body.side or "").strip()
    group = (body.group or "").strip()
    bad = _validate_create(key, side, group)
    if bad:
        return bad
    with get_db() as db:
        exists = (
            db.query(RecommendTagDefORM)
            .filter(RecommendTagDefORM.key == key, RecommendTagDefORM.side == side)
            .first()
        )
        if exists:
            return fail(message=_("(key, side) 已存在"), code=400)
        row = RecommendTagDefORM(
            key=key,
            side=side,
            group=group[:32],
            label_zh=(body.label_zh or "")[:100],
            label_en=(body.label_en or "")[:100],
            enabled=bool(body.enabled),
            source="ops",
        )
        db.add(row)
        db.flush()
        out = _tag_out(row)
    _after_save()
    return ok(data=out, message=_("创建成功"))


@router.put("/api/admin/recommend/tag-dictionary/{side}/{key}", summary="修改标签")
@router.patch("/api/admin/recommend/tag-dictionary/{side}/{key}", summary="修改标签")
async def admin_patch_tag_dictionary(
    side: str,
    key: str,
    body: TagDefPatch,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        row, err = _load_tag(db, side, key)
        if err:
            return err
        patch_err = _apply_tag_patch(row, body)
        if patch_err:
            return patch_err
        out = _tag_out(row)
    _after_save()
    return ok(data=out)


@router.patch("/api/admin/recommend/tag-dictionary/{side}/{key}/active", summary="开关标签")
async def admin_patch_tag_dictionary_active(
    side: str,
    key: str,
    body: StrategyPatch,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        row, err = _load_tag(db, side, key)
        if err:
            return err
        row.enabled = bool(body.enabled)
        out = _tag_out(row)
    _after_save()
    return ok(data=out)


@router.delete("/api/admin/recommend/tag-dictionary/{side}/{key}", summary="删除标签")
async def admin_delete_tag_dictionary(side: str, key: str, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row, err = _load_tag(db, side, key)
        if err:
            return err
        if row.source == SEED_IMMUTABLE:
            return fail(message=_("种子标签不可删除"), code=400)
        used = strategies_using_tag(
            db.query(RecommendStrategyORM).all(),
            row.side,
            row.key,
        )
        if used:
            names = "、".join(f"#{u['id']} {u['name']}".strip() for u in used[:8])
            return fail(message=_("该标签已被推荐策略使用，不能删除：") + names, code=400)
        db.delete(row)
    _after_save()
    return ok(data={"ok": True}, message=_("删除成功"))


@router.post("/api/admin/recommend/preview", summary="试算推荐池")
async def admin_preview_recommend(
    body: PreviewIn,
    _token: str = Depends(admin_auth_required),
    admin_lang: str = Depends(get_admin_lang),
):
    surface = normalize_surface((body.surface or "companions_all").strip())
    if surface not in ("companions_all", "companions_recommend", "moments"):
        raise HTTPException(
            status_code=400,
            detail=_("surface 必须是 companions_all / companions_recommend / moments"),
        )
    from sqlalchemy import desc

    from services.moments import _excluded_companion_ids, resolve_moments_pool
    from services.recommend import load_tag_def_map, short_lang

    preview_n = 8
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == body.user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="user not found")
        tag_def_map = load_tag_def_map()
        ui_lang = short_lang(body.lang or admin_lang or "")

        if surface in LIST_SURFACES or surface in ("companions_all", "companions_recommend"):
            base = db.query(CompanionORM).filter(CompanionORM.is_deleted.isnot(True))
            excluded = _excluded_companion_ids(db, body.user_id)
            if excluded:
                base = base.filter(~CompanionORM.id.in_(excluded))

            def count_fn(agent_tags, agent_match, lang_eq):
                q = apply_agent_tag_filters(base, CompanionORM.tags, agent_tags, tag_def_map, agent_match)
                if lang_eq:
                    q = q.filter(CompanionORM.language == lang_eq)
                return int(q.count() or 0)

            pool = resolve_for_request(
                db, surface=surface, user_id=body.user_id, ui_lang=ui_lang, count_fn=count_fn
            )
            q = apply_waterfall_to_companion_query(base, pool, tag_def_map, CompanionORM)
            total = int(q.count() or 0)
            rows = q.order_by(CompanionORM.created_at.desc()).limit(preview_n).all()
            items = [{"id": r.id, "name": r.name, "gender": r.gender, "language": r.language, "tags": r.tags or []} for r in rows]
        else:
            q, pool = resolve_moments_pool(db, body.user_id, ui_lang)
            total = int(q.count() or 0)
            rows = q.order_by(desc(MomentORM.created_at)).limit(preview_n).all()
            items = [{"id": m.id, "companion_id": m.companion_id, "caption": (m.caption or "")[:80]} for m in rows]

    return {
        "surface": surface,
        "user_id": body.user_id,
        "lang": ui_lang,
        "matched_strategy_id": pool.matched_strategy_id,
        "fallback_layer": pool.fallback_layer,
        "is_global": pool.is_global,
        "pool_size": total,
        "items": items,
    }
