"""【埋点消息策略】后台 API：全局控制 / 规则配置 / 策略库 / 触发预览。

与【单体消息策略】companion_strategies、【快速回复】quick-reply 独立。
埋点上报后引擎主动下发招呼；取文案时若该智能体有单体策略则优先用单体。
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Query

from api.admin_common import admin_auth_required, router
from core.database import (
    MsgStrategyDispatchLogORM,
    MsgStrategyGlobalORM,
    MsgStrategyLibraryORM,
    MsgStrategyRuleORM,
    get_db,
)
from core.response import ok, paginated
from services.msg_strategy_crud import (
    apply_global_fields,
    apply_library_fields,
    apply_rule_fields,
    enrich_library_rows,
    global_to_dict,
    library_to_dict,
    load_companion_id_name_map,
    rule_to_dict,
)
from services.msg_strategy_engine import dispatch_product_event, diagnose_event_config
from services.msg_strategy_meta import (
    known_library_type_keys,
    meta_payload,
    normalize_triggers,
)


# ---------- 元数据 ----------


@router.get("/api/admin/msg-strategy/meta", summary="消息策略-枚举元数据")
async def msg_strategy_meta(_token: str = Depends(admin_auth_required)):
    """triggers / library_types 来自 strategy_types.event_key + name_zh（短缓存）。"""
    with get_db() as db:
        return ok(data=meta_payload(db))


@router.get("/api/admin/msg-strategy/scope-options", summary="用户范围下拉：Ads标记 / 用户属性标记")
async def msg_strategy_scope_options(_token: str = Depends(admin_auth_required)):
    """供全局控制 / 规则配置多选：返回 value（写入 user_scope_value）+ label。"""
    from core.database import RecommendTagDefORM
    from services.ad_attribution import list_ad_group_options

    with get_db() as db:
        ads_opts: list = []
        try:
            payload = list_ad_group_options(db) or {}
            generic = payload.get("generic") or {}
            if generic.get("user_tag"):
                ads_opts.append(
                    {
                        "value": generic["user_tag"],
                        "label": generic.get("label") or generic["user_tag"],
                        "kind": "generic",
                        "count": int(generic.get("count") or 0),
                    }
                )
            for item in payload.get("specific") or []:
                tag = (item.get("user_tag") or "").strip()
                if not tag:
                    continue
                ads_opts.append(
                    {
                        "value": tag,
                        "label": item.get("label") or tag,
                        "kind": item.get("kind") or "campaign",
                        "count": int(item.get("count") or 0),
                    }
                )
        except Exception:
            ads_opts = []

        attr_opts: list = []
        try:
            rows = (
                db.query(RecommendTagDefORM)
                .filter(
                    RecommendTagDefORM.side == "user",
                    RecommendTagDefORM.enabled.is_(True),
                )
                .order_by(RecommendTagDefORM.group.asc(), RecommendTagDefORM.key.asc())
                .all()
            )
            for r in rows:
                key = (r.key or "").strip()
                if not key:
                    continue
                # Ads 类 ua:* 留给 ads 下拉，属性下拉排除
                if key.startswith("ua:"):
                    continue
                attr_opts.append(
                    {
                        "value": key,
                        "label": (r.label_zh or r.label_en or key).strip() or key,
                        "group": r.group or "",
                        "source": r.source or "ops",
                    }
                )
        except Exception:
            attr_opts = []

        return ok(
            data={
                "ads": ads_opts,
                "attr": attr_opts,
                "hint": {
                    "ads": "多选 value 写入 globals/rules.user_scope_value（逗号分隔），匹配 users.ua_tags",
                    "attr": "多选 value 写入 user_scope_value，匹配 users.tags（运营属性）",
                },
            }
        )


# ---------- 1. 全局控制 ----------


@router.get("/api/admin/msg-strategy/globals", summary="全局控制列表")
async def list_globals(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    is_active: Optional[bool] = None,
    keyword: Optional[str] = None,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        q = db.query(MsgStrategyGlobalORM)
        if is_active is not None:
            q = q.filter(MsgStrategyGlobalORM.is_active == is_active)
        if keyword and keyword.strip():
            q = q.filter(MsgStrategyGlobalORM.name.ilike(f"%{keyword.strip()}%"))
        total = q.count()
        rows = (
            q.order_by(MsgStrategyGlobalORM.priority.desc(), MsgStrategyGlobalORM.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return paginated(
            items=[global_to_dict(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )


@router.post("/api/admin/msg-strategy/globals", summary="新建全局控制")
async def create_global(data: dict, _token: str = Depends(admin_auth_required)):
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name_required")
    with get_db() as db:
        row = MsgStrategyGlobalORM(name=name)
        apply_global_fields(row, data)
        if not data.get("name"):
            row.name = name
        db.add(row)
        db.commit()
        db.refresh(row)
        return ok(data=global_to_dict(row))


@router.put("/api/admin/msg-strategy/globals/{item_id}", summary="更新全局控制")
async def update_global(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(MsgStrategyGlobalORM).filter(MsgStrategyGlobalORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        apply_global_fields(row, data)
        db.commit()
        db.refresh(row)
        return ok(data=global_to_dict(row))


@router.patch("/api/admin/msg-strategy/globals/{item_id}/active", summary="全局控制开关")
async def patch_global_active(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(MsgStrategyGlobalORM).filter(MsgStrategyGlobalORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        row.is_active = bool(data.get("is_active", True))
        db.commit()
        return ok(data={"id": item_id, "is_active": bool(row.is_active)})


@router.delete("/api/admin/msg-strategy/globals/{item_id}", summary="删除全局控制")
async def delete_global(item_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(MsgStrategyGlobalORM).filter(MsgStrategyGlobalORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        db.delete(row)
        db.commit()
        return ok(data={"deleted_id": item_id})


# ---------- 2. 规则配置 ----------


@router.get("/api/admin/msg-strategy/rules", summary="消息策略规则列表")
async def list_rules(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    is_active: Optional[bool] = None,
    keyword: Optional[str] = None,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        q = db.query(MsgStrategyRuleORM)
        if is_active is not None:
            q = q.filter(MsgStrategyRuleORM.is_active == is_active)
        if keyword and keyword.strip():
            q = q.filter(MsgStrategyRuleORM.name.ilike(f"%{keyword.strip()}%"))
        total = q.count()
        rows = (
            q.order_by(MsgStrategyRuleORM.priority.desc(), MsgStrategyRuleORM.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return paginated(
            items=[rule_to_dict(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )


@router.post("/api/admin/msg-strategy/rules", summary="新建策略规则")
async def create_rule(data: dict, _token: str = Depends(admin_auth_required)):
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name_required")
    with get_db() as db:
        row = MsgStrategyRuleORM(
            name=name,
            triggers=normalize_triggers(data.get("triggers"), db=db),
            user_scope_type=(data.get("user_scope_type") or "all").strip()[:32],
            user_scope_value=(data.get("user_scope_value") or "").strip(),
            priority=int(data.get("priority") or 0),
            is_active=bool(data.get("is_active", True)),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return ok(data=rule_to_dict(row))


@router.put("/api/admin/msg-strategy/rules/{item_id}", summary="更新策略规则")
async def update_rule(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(MsgStrategyRuleORM).filter(MsgStrategyRuleORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        apply_rule_fields(row, data, db=db)
        db.commit()
        db.refresh(row)
        return ok(data=rule_to_dict(row))


@router.patch("/api/admin/msg-strategy/rules/{item_id}/active", summary="策略规则开关")
async def patch_rule_active(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(MsgStrategyRuleORM).filter(MsgStrategyRuleORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        row.is_active = bool(data.get("is_active", True))
        db.commit()
        return ok(data={"id": item_id, "is_active": bool(row.is_active)})


@router.delete("/api/admin/msg-strategy/rules/{item_id}", summary="删除策略规则")
async def delete_rule(item_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(MsgStrategyRuleORM).filter(MsgStrategyRuleORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        db.delete(row)
        db.commit()
        return ok(data={"deleted_id": item_id})


# ---------- 3. 消息策略库（全局文案：不绑智能体，作用全部） ----------
# 精准（指定智能体）请用 /api/admin/strategy-center/msg-library


@router.get("/api/admin/msg-strategy/library", summary="全局消息策略库列表")
async def list_library(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    language: Optional[str] = None,
    type_key: Optional[str] = None,
    is_active: Optional[bool] = None,
    keyword: Optional[str] = None,
    scope: Optional[str] = Query(
        "global",
        description="global=仅全局（默认）/ precise=仅精准 / all=全部",
    ),
    _token: str = Depends(admin_auth_required),
):
    from sqlalchemy import func, or_

    with get_db() as db:
        q = db.query(MsgStrategyLibraryORM)
        if language:
            from services.companion_content import canonical_content_language

            lang_code = canonical_content_language(language)
            if not lang_code:
                return paginated(items=[], total=0, page=page, page_size=page_size)
            q = q.filter(MsgStrategyLibraryORM.language == lang_code)
        if type_key:
            q = q.filter(MsgStrategyLibraryORM.type_key == type_key.strip())
        if is_active is not None:
            q = q.filter(MsgStrategyLibraryORM.is_active == is_active)
        if keyword and keyword.strip():
            q = q.filter(MsgStrategyLibraryORM.content.ilike(f"%{keyword.strip()}%"))
        sc = (scope or "global").strip().lower()
        cid_len = func.coalesce(func.jsonb_array_length(MsgStrategyLibraryORM.companion_ids), 0)
        if sc == "global":
            q = q.filter(or_(MsgStrategyLibraryORM.companion_ids.is_(None), cid_len == 0))
        elif sc == "precise":
            q = q.filter(cid_len > 0)
        total = q.count()
        rows = (
            q.order_by(MsgStrategyLibraryORM.sort_order.asc(), MsgStrategyLibraryORM.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return paginated(
            items=enrich_library_rows(db, rows),
            total=total,
            page=page,
            page_size=page_size,
        )


@router.get("/api/admin/msg-strategy/library/{item_id}", summary="全局策略库详情")
async def get_library(item_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(MsgStrategyLibraryORM).filter(MsgStrategyLibraryORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        name_map = load_companion_id_name_map(db, row.companion_ids or [])
        return ok(data=library_to_dict(row, name_map=name_map))


@router.post("/api/admin/msg-strategy/library", summary="新建全局策略文案（无需 companion_ids）")
async def create_library(data: dict, _token: str = Depends(admin_auth_required)):
    content = (data.get("content") or "").strip()
    type_key = (data.get("type_key") or "").strip()
    from services.companion_content import canonical_content_language

    language = canonical_content_language(data.get("language") or "zh")
    if not content:
        raise HTTPException(status_code=400, detail="content_required")
    if not language:
        raise HTTPException(status_code=400, detail="unsupported_language")
    with get_db() as db:
        if type_key not in known_library_type_keys(db):
            raise HTTPException(status_code=400, detail="invalid_type_key")
        from sqlalchemy.orm.attributes import flag_modified

        row = MsgStrategyLibraryORM(
            content=content,
            language=language,
            type_key=type_key,
            companion_ids=[],
        )
        apply_library_fields(row, data, force_global=True)
        row.content = content
        row.type_key = type_key
        row.language = language
        row.companion_ids = []
        flag_modified(row, "companion_ids")
        db.add(row)
        db.commit()
        db.refresh(row)
        return ok(data=library_to_dict(row, name_map={}))


@router.put("/api/admin/msg-strategy/library/{item_id}", summary="更新全局策略文案（清除 companion_ids）")
async def update_library(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(MsgStrategyLibraryORM).filter(MsgStrategyLibraryORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        if "type_key" in data:
            tk = (data.get("type_key") or "").strip()
            if tk not in known_library_type_keys(db):
                raise HTTPException(status_code=400, detail="invalid_type_key")
        if "language" in data:
            from services.companion_content import canonical_content_language

            lang_code = canonical_content_language(data.get("language") or "")
            if not lang_code:
                raise HTTPException(status_code=400, detail="unsupported_language")
            data = {**data, "language": lang_code}
        from sqlalchemy.orm.attributes import flag_modified

        apply_library_fields(row, data, force_global=True)
        row.companion_ids = []
        flag_modified(row, "companion_ids")
        db.commit()
        db.refresh(row)
        return ok(data=library_to_dict(row, name_map={}))


@router.patch("/api/admin/msg-strategy/library/{item_id}/active", summary="策略文案开关")
async def patch_library_active(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(MsgStrategyLibraryORM).filter(MsgStrategyLibraryORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        row.is_active = bool(data.get("is_active", True))
        db.commit()
        return ok(data={"id": item_id, "is_active": bool(row.is_active)})


@router.delete("/api/admin/msg-strategy/library/{item_id}", summary="删除策略文案")
async def delete_library(item_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(MsgStrategyLibraryORM).filter(MsgStrategyLibraryORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        db.delete(row)
        db.commit()
        return ok(data={"deleted_id": item_id})


# ---------- 4. 触发引擎：预览 / 日志 ----------


@router.get("/api/admin/msg-strategy/diagnose", summary="诊断用户+事件的三步配置")
async def admin_diagnose(
    user_id: int = Query(..., ge=1),
    event_key: str = Query(..., min_length=1),
    language: str = Query(""),
    companion_id: str = Query(""),
    moment_id: Optional[int] = Query(None),
    _token: str = Depends(admin_auth_required),
):
    """检查全局 / 规则 / 策略库 / companion 是否齐全；返回 issues 与 dry_run 预览。"""
    return ok(
        data=diagnose_event_config(
            int(user_id),
            event_key.strip(),
            language=language.strip(),
            companion_id=companion_id.strip(),
            moment_id=moment_id,
        )
    )


@router.post("/api/admin/msg-strategy/dispatch", summary="手动触发/预览引擎")
async def admin_dispatch(data: dict, _token: str = Depends(admin_auth_required)):
    user_id = data.get("user_id")
    event_key = (data.get("event_key") or data.get("trigger_event") or "").strip()
    if not user_id or not event_key:
        raise HTTPException(status_code=400, detail="user_id_and_event_key_required")
    raw_mid = data.get("moment_id")
    try:
        moment_id = int(raw_mid) if raw_mid not in (None, "") else None
    except (TypeError, ValueError):
        moment_id = None
    result = dispatch_product_event(
        int(user_id),
        event_key,
        language=(data.get("language") or "").strip() or None,
        companion_id=(data.get("companion_id") or "").strip() or None,
        moment_id=moment_id,
        dry_run=bool(data.get("dry_run", False)),
    )
    return ok(data=result)


@router.get("/api/admin/msg-strategy/logs", summary="触发执行日志")
async def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user_id: Optional[int] = None,
    event_key: Optional[str] = None,
    status: Optional[str] = None,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        q = db.query(MsgStrategyDispatchLogORM)
        if user_id is not None:
            q = q.filter(MsgStrategyDispatchLogORM.user_id == user_id)
        if event_key:
            q = q.filter(MsgStrategyDispatchLogORM.event_key == event_key.strip())
        if status:
            q = q.filter(MsgStrategyDispatchLogORM.status == status.strip())
        total = q.count()
        rows = (
            q.order_by(MsgStrategyDispatchLogORM.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [
            {
                "id": r.id,
                "user_id": r.user_id,
                "event_key": r.event_key,
                "trigger_key": r.trigger_key,
                "global_id": r.global_id,
                "rule_id": r.rule_id,
                "library_id": r.library_id,
                "companion_id": r.companion_id,
                "content": r.content,
                "status": r.status,
                "skip_reason": r.skip_reason,
                "job_id": r.job_id,
                "execute_at": r.execute_at.isoformat() if r.execute_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return paginated(items=items, total=total, page=page, page_size=page_size)
