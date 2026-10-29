"""管理后台 - 归因事件列表 / 概览（支持自然日时间筛选）"""
from __future__ import annotations

from datetime import date
from math import ceil
from typing import Any, Dict, Optional, Tuple

from fastapi import Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import desc, func

from api.admin_common import admin_auth_required, router
from core.database import UserAttributionEventORM, get_db, serialize_datetime
from core.response import ok
from services.admin_data_stats import MAX_RANGE_DAYS, parse_date, resolve_date_range
from services.attribution_timing import apply_created_at_range

# 归因事件时间筛选：自然日维度，最大跨度（天）
ATTR_DATE_DIMENSION = "natural_day"  # 按 created_at 的 UTC 日历日
ATTR_DATE_TIMEZONE = "UTC"
ATTR_DATE_FORMAT = "YYYY-MM-DD"
ATTR_DATE_MAX_DAYS = MAX_RANGE_DAYS  # 当前 = 60


def _date_filter_meta(
    start_d: Optional[date],
    end_d: Optional[date],
) -> Dict[str, Any]:
    """统一回传筛选约定，供前端 DatePicker / 展示。"""
    return {
        "dimension": ATTR_DATE_DIMENSION,
        "timezone": ATTR_DATE_TIMEZONE,
        "format": ATTR_DATE_FORMAT,
        "max_days": ATTR_DATE_MAX_DAYS,
        "param_date": "date",
        "param_start": "start_date",
        "param_end": "end_date",
        "start_date": start_d.strftime("%Y-%m-%d") if start_d else None,
        "end_date": end_d.strftime("%Y-%m-%d") if end_d else None,
        "filtered": start_d is not None and end_d is not None,
    }


def _resolve_filter_dates(
    *,
    date_point: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Tuple[Optional[date], Optional[date]]:
    """解析筛选日期（自然日）。

    - 不传任何日期 → 不筛选（全量）
    - 只传 date → 该自然日（起止同一天）
    - 传 start_date + end_date → 自然日闭区间；超过 max_days 时截断
    - 只传其一 → 视为时间点（起止同一天）
    - date 非法时回退 start/end（避免把范围筛选整段作废）
    """
    point = (date_point or "").strip()
    if point:
        d = parse_date(point)
        if d is not None:
            return d, d
        # date 有值但非法 → 继续尝试 start/end，而不是清空筛选

    s = (start_date or "").strip()
    e = (end_date or "").strip()
    if not s and not e:
        return None, None
    if s and not e:
        d = parse_date(s)
        return (d, d) if d else (None, None)
    if e and not s:
        d = parse_date(e)
        return (d, d) if d else (None, None)
    return resolve_date_range(s, e, max_days=ATTR_DATE_MAX_DAYS)


@router.get("/api/admin/attribution/filter-options", summary="归因事件筛选约定")
async def admin_attribution_filter_options(_token: str = Depends(admin_auth_required)):
    """前端初始化 DatePicker 用：自然日维度 + 最大天数等。"""
    return ok(data=_date_filter_meta(None, None))


@router.get("/api/admin/attribution/events", summary="归因事件列表")
async def admin_list_attribution_events(
    _token: str = Depends(admin_auth_required),
    event_type: Optional[str] = Query(None, description="install/register/login/purchase/product"),
    product_event: Optional[str] = Query(None, description="产品事件名，如 app_launch"),
    device_id: Optional[str] = Query(None),
    ad_id: Optional[str] = Query(None),
    install_source: Optional[str] = Query(None),
    os: Optional[str] = Query(None),
    on_date: Optional[str] = Query(
        None,
        alias="date",
        description="时间点（自然日）YYYY-MM-DD；与 start/end 同时传时优先 date",
    ),
    start_date: Optional[str] = Query(
        None,
        description=f"时间范围起点（自然日）YYYY-MM-DD；与 end_date 组成闭区间，最大 {ATTR_DATE_MAX_DAYS} 天",
    ),
    end_date: Optional[str] = Query(
        None,
        description=f"时间范围终点（自然日）YYYY-MM-DD；与 start_date 组成闭区间，最大 {ATTR_DATE_MAX_DAYS} 天",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
):
    start_d, end_d = _resolve_filter_dates(
        date_point=on_date, start_date=start_date, end_date=end_date
    )
    with get_db() as db:
        q = db.query(UserAttributionEventORM)
        q = apply_created_at_range(q, start_d, end_d)
        if event_type:
            q = q.filter(UserAttributionEventORM.event_type == event_type)
        if product_event:
            q = q.filter(UserAttributionEventORM.product_event == product_event)
        if device_id:
            q = q.filter(UserAttributionEventORM.device_id.ilike(f"%{device_id}%"))
        if ad_id:
            q = q.filter(
                (UserAttributionEventORM.ad_id.ilike(f"%{ad_id}%"))
                | (UserAttributionEventORM.idfv.ilike(f"%{ad_id}%"))
                | (UserAttributionEventORM.gclid.ilike(f"%{ad_id}%"))
                | (UserAttributionEventORM.gad_campaignid.ilike(f"%{ad_id}%"))
            )
        if install_source:
            q = q.filter(UserAttributionEventORM.install_source == install_source)
        if os:
            q = q.filter(UserAttributionEventORM.os == os)

        total = q.count()
        rows = (
            q.order_by(desc(UserAttributionEventORM.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [_event_out(r) for r in rows]

    total_pages = ceil(total / page_size) if page_size > 0 else 0
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "message": "success",
            "results": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
            "date_filter": _date_filter_meta(start_d, end_d),
        },
    )


@router.get("/api/admin/attribution/summary", summary="归因事件概览统计")
async def admin_attribution_summary(
    _token: str = Depends(admin_auth_required),
    on_date: Optional[str] = Query(
        None,
        alias="date",
        description="时间点（自然日）YYYY-MM-DD；与 start/end 同时传时优先 date",
    ),
    start_date: Optional[str] = Query(
        None,
        description=f"时间范围起点（自然日）YYYY-MM-DD；最大跨度 {ATTR_DATE_MAX_DAYS} 天",
    ),
    end_date: Optional[str] = Query(
        None,
        description=f"时间范围终点（自然日）YYYY-MM-DD；最大跨度 {ATTR_DATE_MAX_DAYS} 天",
    ),
):
    start_d, end_d = _resolve_filter_dates(
        date_point=on_date, start_date=start_date, end_date=end_date
    )
    with get_db() as db:
        def base():
            return apply_created_at_range(db.query(UserAttributionEventORM), start_d, end_d)

        total = (
            apply_created_at_range(
                db.query(func.count(UserAttributionEventORM.id)), start_d, end_d
            ).scalar()
            or 0
        )
        by_event = (
            base()
            .with_entities(
                UserAttributionEventORM.event_type,
                func.count(UserAttributionEventORM.id),
            )
            .group_by(UserAttributionEventORM.event_type)
            .all()
        )
        by_source = (
            base()
            .with_entities(
                UserAttributionEventORM.install_source,
                func.count(UserAttributionEventORM.id),
            )
            .filter(UserAttributionEventORM.install_source != "")
            .group_by(UserAttributionEventORM.install_source)
            .all()
        )
        by_os = (
            base()
            .with_entities(
                UserAttributionEventORM.os,
                func.count(UserAttributionEventORM.id),
            )
            .filter(UserAttributionEventORM.os != "")
            .group_by(UserAttributionEventORM.os)
            .all()
        )
        ads_count = (
            base()
            .filter(UserAttributionEventORM.ad_id != "")
            .with_entities(func.count(UserAttributionEventORM.id))
            .scalar()
        ) or 0
        gclid_count = (
            base()
            .filter(UserAttributionEventORM.gclid != "")
            .with_entities(func.count(UserAttributionEventORM.id))
            .scalar()
        ) or 0
        campaign_count = (
            base()
            .filter(UserAttributionEventORM.gad_campaignid != "")
            .with_entities(func.count(UserAttributionEventORM.id))
            .scalar()
        ) or 0
        by_product = (
            base()
            .with_entities(
                UserAttributionEventORM.product_event,
                func.count(UserAttributionEventORM.id),
            )
            .filter(
                UserAttributionEventORM.event_type == "product",
                UserAttributionEventORM.product_event != "",
            )
            .group_by(UserAttributionEventORM.product_event)
            .order_by(func.count(UserAttributionEventORM.id).desc())
            .limit(50)
            .all()
        )

    return ok(data={
        "date_filter": _date_filter_meta(start_d, end_d),
        "start_date": start_d.strftime("%Y-%m-%d") if start_d else None,
        "end_date": end_d.strftime("%Y-%m-%d") if end_d else None,
        "max_days": ATTR_DATE_MAX_DAYS,
        "total": total,
        "by_event_type": {row[0]: row[1] for row in by_event},
        "by_install_source": {row[0]: row[1] for row in by_source},
        "by_os": {row[0]: row[1] for row in by_os},
        "by_product_event": {row[0]: row[1] for row in by_product},
        "with_ad_id": ads_count,
        "with_gclid": gclid_count,
        "with_gad_campaignid": campaign_count,
    })


def _event_out(r: UserAttributionEventORM) -> dict:
    return {
        "id": r.id,
        "user_id": r.user_id,
        "device_id": r.device_id or "",
        "event_type": r.event_type or "",
        "product_event": getattr(r, "product_event", None) or "",
        "firebase_event_name": getattr(r, "firebase_event_name", None) or "",
        "firebase_event_params": getattr(r, "firebase_event_params", None) or {},
        "app_env": getattr(r, "app_env", None) or "",
        "install_source": r.install_source or "",
        "utm_source": r.utm_source or "",
        "utm_medium": r.utm_medium or "",
        "utm_campaign": r.utm_campaign or "",
        "utm_content": r.utm_content or "",
        "utm_term": r.utm_term or "",
        "gclid": r.gclid or "",
        "gad_campaignid": getattr(r, "gad_campaignid", None) or "",
        "invite_code": r.invite_code or "",
        "ad_id": r.ad_id or "",
        "ad_id_type": r.ad_id_type or "",
        "idfv": r.idfv or "",
        "android_id": r.android_id or "",
        "network": r.network or "",
        "login_method": r.login_method or "",
        "app_version": r.app_version or "",
        "os": r.os or "",
        "os_version": r.os_version or "",
        "device_model": r.device_model or "",
        "locale": r.locale or "",
        "timezone": r.timezone or "",
        "storefront_country": r.storefront_country or "",
        "ip": r.ip or "",
        "ip_country": r.ip_country or "",
        "attribution_provider": getattr(r, "attribution_provider", None) or "",
        "raw_payload": r.raw_payload or {},
        "created_at": serialize_datetime(r.created_at),
    }
