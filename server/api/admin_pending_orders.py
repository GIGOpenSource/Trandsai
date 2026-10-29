"""管理后台 - 预订单（pending_orders）增删改查 + 业务筛选"""
from __future__ import annotations

from datetime import date, datetime, timezone
from math import ceil
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_

from api.admin_common import admin_auth_required, router
from core.database import (
    BenefitProductORM,
    PendingOrderORM,
    UserORM,
    get_db,
    serialize_datetime,
)
from core.response import fail, ok
from services.admin_data_stats import (
    MAX_RANGE_DAYS,
    _day_end,
    _day_start,
    parse_date,
    resolve_date_range,
)

STATUS_VALUES = ("pending", "completed", "failed", "expired")
PRODUCT_TYPES = ("subscribe", "point")
STORE_TYPES = ("app_store", "google_play")

STATUS_LABELS = {
    "pending": {"zh": "待确认", "en": "Pending"},
    "completed": {"zh": "已完成", "en": "Completed"},
    "failed": {"zh": "失败", "en": "Failed"},
    "expired": {"zh": "已过期", "en": "Expired"},
}
PRODUCT_TYPE_LABELS = {
    "subscribe": {"zh": "订阅", "en": "Subscribe"},
    "point": {"zh": "积分充值", "en": "Credits"},
}
STORE_TYPE_LABELS = {
    "app_store": {"zh": "App Store", "en": "App Store"},
    "google_play": {"zh": "Google Play", "en": "Google Play"},
}
PLAN_TYPE_LABELS = {
    "weekly": {"zh": "周订阅", "en": "Weekly"},
    "monthly": {"zh": "月订阅", "en": "Monthly"},
    "yearly": {"zh": "年订阅", "en": "Yearly"},
    "lifetime": {"zh": "终身", "en": "Lifetime"},
    "daily": {"zh": "日订阅", "en": "Daily"},
}

DATE_DIMENSION = "natural_day"
DATE_TIMEZONE = "UTC"
DATE_FORMAT = "YYYY-MM-DD"
DATE_MAX_DAYS = MAX_RANGE_DAYS


class PendingOrderCreate(BaseModel):
    transaction_id: str = Field(..., min_length=1, max_length=255)
    original_transaction_id: str = Field("", max_length=255)
    product_id: str = Field(..., min_length=1, max_length=100)
    product_type: str = Field(..., description="subscribe / point")
    plan_type: str = Field("", max_length=20)
    package_key: str = Field("", max_length=100)
    price_usd: float = 0
    credits_amount: int = 0
    duration_days: int = 0
    store_type: str = Field(..., description="app_store / google_play")
    status: str = Field("pending", description="pending / completed / failed / expired")
    user_id: Optional[int] = None
    callback_data: str = ""


class PendingOrderUpdate(BaseModel):
    transaction_id: Optional[str] = Field(None, min_length=1, max_length=255)
    original_transaction_id: Optional[str] = Field(None, max_length=255)
    product_id: Optional[str] = Field(None, min_length=1, max_length=100)
    product_type: Optional[str] = None
    plan_type: Optional[str] = None
    package_key: Optional[str] = None
    price_usd: Optional[float] = None
    credits_amount: Optional[int] = None
    duration_days: Optional[int] = None
    store_type: Optional[str] = None
    status: Optional[str] = None
    user_id: Optional[int] = None
    callback_data: Optional[str] = None


def _date_filter_meta(start_d: Optional[date], end_d: Optional[date]) -> Dict[str, Any]:
    return {
        "dimension": DATE_DIMENSION,
        "timezone": DATE_TIMEZONE,
        "format": DATE_FORMAT,
        "max_days": DATE_MAX_DAYS,
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
    point = (date_point or "").strip()
    if point:
        d = parse_date(point)
        return (d, d) if d else (None, None)

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
    return resolve_date_range(s, e, max_days=DATE_MAX_DAYS)


def _apply_created_at_range(q, start_d: Optional[date], end_d: Optional[date]):
    if start_d is not None:
        q = q.filter(PendingOrderORM.created_at >= _day_start(start_d))
    if end_d is not None:
        q = q.filter(PendingOrderORM.created_at <= _day_end(end_d))
    return q


def _label(mapping: Dict[str, Dict[str, str]], key: str) -> Dict[str, str]:
    item = mapping.get(key or "") or {}
    return {"zh": item.get("zh") or (key or ""), "en": item.get("en") or (key or "")}


def _validate_enums(
    *,
    status: Optional[str] = None,
    product_type: Optional[str] = None,
    store_type: Optional[str] = None,
) -> Optional[str]:
    if status is not None and status not in STATUS_VALUES:
        return f"status 必须是: {', '.join(STATUS_VALUES)}"
    if product_type is not None and product_type not in PRODUCT_TYPES:
        return f"product_type 必须是: {', '.join(PRODUCT_TYPES)}"
    if store_type is not None and store_type not in STORE_TYPES:
        return f"store_type 必须是: {', '.join(STORE_TYPES)}"
    return None


def _users_map(db, user_ids: Set[int]) -> Dict[int, Dict[str, Any]]:
    ids = [i for i in user_ids if i]
    if not ids:
        return {}
    rows = db.query(UserORM).filter(UserORM.id.in_(ids)).all()
    out: Dict[int, Dict[str, Any]] = {}
    for u in rows:
        out[u.id] = {
            "id": u.id,
            "username": u.username or "",
            "nickname": u.nickname or "",
            "email": getattr(u, "email", "") or "",
            "avatar_url": u.avatar_url or "",
            "account_status": getattr(u, "account_status", "") or "",
        }
    return out


def _products_map(db, product_ids: Set[str]) -> Dict[str, Dict[str, Any]]:
    ids = [p for p in product_ids if p]
    if not ids:
        return {}
    rows = (
        db.query(BenefitProductORM)
        .filter(
            or_(
                BenefitProductORM.apple_product_id.in_(ids),
                BenefitProductORM.store_product_id.in_(ids),
            )
        )
        .all()
    )
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        info = {
            "id": r.id,
            "title": r.title or "",
            "product_type": r.product_type or "",
            "period": r.period or "",
            "price_usd": float(r.price_usd or 0),
            "credits_amount": int(r.credits_amount or 0),
            "apple_product_id": r.apple_product_id or "",
            "store_product_id": r.store_product_id or "",
            "is_active": bool(r.is_active),
        }
        if r.apple_product_id:
            out[r.apple_product_id] = info
        if r.store_product_id:
            out[r.store_product_id] = info
    return out


def _product_display_name(row: PendingOrderORM, product: Optional[Dict[str, Any]]) -> str:
    if product and product.get("title"):
        return str(product["title"])
    if row.product_type == "point" and row.package_key:
        return row.package_key
    if row.plan_type:
        plan = _label(PLAN_TYPE_LABELS, row.plan_type).get("zh") or row.plan_type
        return f"{plan}({row.product_id})" if row.product_id else plan
    return row.product_id or ""


def _order_out(
    row: PendingOrderORM,
    *,
    user: Optional[Dict[str, Any]] = None,
    product: Optional[Dict[str, Any]] = None,
    include_callback: bool = False,
) -> Dict[str, Any]:
    status = row.status or "pending"
    product_type = row.product_type or ""
    store_type = row.store_type or ""
    plan_type = row.plan_type or ""
    data: Dict[str, Any] = {
        "id": row.id,
        "transaction_id": row.transaction_id or "",
        "original_transaction_id": row.original_transaction_id or "",
        "product_id": row.product_id or "",
        "product_type": product_type,
        "product_type_label": _label(PRODUCT_TYPE_LABELS, product_type),
        "plan_type": plan_type,
        "plan_type_label": _label(PLAN_TYPE_LABELS, plan_type) if plan_type else {"zh": "", "en": ""},
        "package_key": row.package_key or "",
        "price_usd": float(row.price_usd or 0),
        "credits_amount": int(row.credits_amount or 0),
        "duration_days": int(row.duration_days or 0),
        "store_type": store_type,
        "store_type_label": _label(STORE_TYPE_LABELS, store_type),
        "status": status,
        "status_label": _label(STATUS_LABELS, status),
        "user_id": row.user_id,
        "user": user,
        "product": product,
        "product_name": _product_display_name(row, product),
        "created_at": serialize_datetime(row.created_at),
        "updated_at": serialize_datetime(row.updated_at),
    }
    if include_callback:
        data["callback_data"] = row.callback_data or ""
    return data


def _enrich_orders(db, rows: List[PendingOrderORM], *, include_callback: bool = False) -> List[Dict[str, Any]]:
    user_ids = {int(r.user_id) for r in rows if r.user_id}
    product_ids = {str(r.product_id) for r in rows if r.product_id}
    users = _users_map(db, user_ids)
    products = _products_map(db, product_ids)
    return [
        _order_out(
            r,
            user=users.get(int(r.user_id)) if r.user_id else None,
            product=products.get(str(r.product_id or "")),
            include_callback=include_callback,
        )
        for r in rows
    ]


def _apply_filters(
    q,
    *,
    status: Optional[str] = None,
    store_type: Optional[str] = None,
    product_type: Optional[str] = None,
    plan_type: Optional[str] = None,
    user_id: Optional[int] = None,
    transaction_id: Optional[str] = None,
    product_id: Optional[str] = None,
    package_key: Optional[str] = None,
    keyword: Optional[str] = None,
    start_d: Optional[date] = None,
    end_d: Optional[date] = None,
    db=None,
):
    q = _apply_created_at_range(q, start_d, end_d)
    if status:
        q = q.filter(PendingOrderORM.status == status)
    if store_type:
        q = q.filter(PendingOrderORM.store_type == store_type)
    if product_type:
        q = q.filter(PendingOrderORM.product_type == product_type)
    if plan_type:
        q = q.filter(PendingOrderORM.plan_type == plan_type)
    if user_id is not None:
        q = q.filter(PendingOrderORM.user_id == user_id)
    if transaction_id:
        q = q.filter(PendingOrderORM.transaction_id.ilike(f"%{transaction_id.strip()}%"))
    if product_id:
        q = q.filter(PendingOrderORM.product_id.ilike(f"%{product_id.strip()}%"))
    if package_key:
        q = q.filter(PendingOrderORM.package_key == package_key.strip())
    kw = (keyword or "").strip()
    if kw and db is not None:
        user_ids = [
            u.id
            for u in db.query(UserORM.id)
            .filter(
                or_(
                    UserORM.username.ilike(f"%{kw}%"),
                    UserORM.nickname.ilike(f"%{kw}%"),
                    UserORM.email.ilike(f"%{kw}%"),
                )
            )
            .limit(200)
            .all()
        ]
        conds = [
            PendingOrderORM.transaction_id.ilike(f"%{kw}%"),
            PendingOrderORM.original_transaction_id.ilike(f"%{kw}%"),
            PendingOrderORM.product_id.ilike(f"%{kw}%"),
            PendingOrderORM.package_key.ilike(f"%{kw}%"),
        ]
        if user_ids:
            conds.append(PendingOrderORM.user_id.in_(user_ids))
        if kw.isdigit():
            conds.append(PendingOrderORM.user_id == int(kw))
            conds.append(PendingOrderORM.id == int(kw))
        q = q.filter(or_(*conds))
    return q


@router.get("/api/admin/pending-orders/filter-options", summary="预订单筛选约定")
async def admin_pending_orders_filter_options(_token: str = Depends(admin_auth_required)):
    return ok(
        data={
            **_date_filter_meta(None, None),
            "statuses": [
                {"value": s, "label": STATUS_LABELS[s]} for s in STATUS_VALUES
            ],
            "product_types": [
                {"value": t, "label": PRODUCT_TYPE_LABELS[t]} for t in PRODUCT_TYPES
            ],
            "store_types": [
                {"value": t, "label": STORE_TYPE_LABELS[t]} for t in STORE_TYPES
            ],
            "plan_types": [
                {"value": p, "label": PLAN_TYPE_LABELS[p]} for p in PLAN_TYPE_LABELS
            ],
            "filters": [
                "status",
                "store_type",
                "product_type",
                "plan_type",
                "user_id",
                "transaction_id",
                "product_id",
                "package_key",
                "keyword",
                "date",
                "start_date",
                "end_date",
            ],
        }
    )


@router.get("/api/admin/pending-orders/summary", summary="预订单概览统计")
async def admin_pending_orders_summary(
    _token: str = Depends(admin_auth_required),
    status: Optional[str] = Query(None),
    store_type: Optional[str] = Query(None),
    product_type: Optional[str] = Query(None),
    plan_type: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    transaction_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    package_key: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    date: Optional[str] = Query(None, description="时间点 YYYY-MM-DD"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    start_d, end_d = _resolve_filter_dates(
        date_point=date, start_date=start_date, end_date=end_date
    )
    with get_db() as db:
        def base():
            return _apply_filters(
                db.query(PendingOrderORM),
                status=status,
                store_type=store_type,
                product_type=product_type,
                plan_type=plan_type,
                user_id=user_id,
                transaction_id=transaction_id,
                product_id=product_id,
                package_key=package_key,
                keyword=keyword,
                start_d=start_d,
                end_d=end_d,
                db=db,
            )

        total = base().with_entities(func.count(PendingOrderORM.id)).scalar() or 0
        by_status = {
            k: int(v)
            for k, v in base()
            .with_entities(PendingOrderORM.status, func.count(PendingOrderORM.id))
            .group_by(PendingOrderORM.status)
            .all()
        }
        by_store = {
            k: int(v)
            for k, v in base()
            .with_entities(PendingOrderORM.store_type, func.count(PendingOrderORM.id))
            .group_by(PendingOrderORM.store_type)
            .all()
        }
        by_product_type = {
            k: int(v)
            for k, v in base()
            .with_entities(PendingOrderORM.product_type, func.count(PendingOrderORM.id))
            .group_by(PendingOrderORM.product_type)
            .all()
        }
        amount_row = (
            base()
            .with_entities(
                func.coalesce(func.sum(PendingOrderORM.price_usd), 0),
                func.coalesce(func.sum(PendingOrderORM.credits_amount), 0),
            )
            .first()
        )
        completed_amount = (
            base()
            .filter(PendingOrderORM.status == "completed")
            .with_entities(func.coalesce(func.sum(PendingOrderORM.price_usd), 0))
            .scalar()
            or 0
        )

    return ok(
        data={
            "total": int(total),
            "by_status": {s: by_status.get(s, 0) for s in STATUS_VALUES},
            "by_store_type": {s: by_store.get(s, 0) for s in STORE_TYPES},
            "by_product_type": {t: by_product_type.get(t, 0) for t in PRODUCT_TYPES},
            "price_usd_sum": float(amount_row[0] if amount_row else 0),
            "credits_amount_sum": int(amount_row[1] if amount_row else 0),
            "completed_price_usd_sum": float(completed_amount),
            "date_filter": _date_filter_meta(start_d, end_d),
        }
    )


@router.get("/api/admin/pending-orders", summary="预订单列表")
async def admin_list_pending_orders(
    _token: str = Depends(admin_auth_required),
    status: Optional[str] = Query(None, description="pending/completed/failed/expired"),
    store_type: Optional[str] = Query(None, description="app_store/google_play"),
    product_type: Optional[str] = Query(None, description="subscribe/point"),
    plan_type: Optional[str] = Query(None, description="weekly/monthly/yearly/lifetime/daily"),
    user_id: Optional[int] = Query(None, description="充值人用户ID"),
    transaction_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    package_key: Optional[str] = Query(None),
    keyword: Optional[str] = Query(
        None,
        description="模糊搜：交易号/商品ID/package_key/用户名/昵称/邮箱/订单ID/用户ID",
    ),
    date: Optional[str] = Query(None, description="创建时间点 YYYY-MM-DD（UTC 自然日）"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
):
    err = _validate_enums(
        status=status if status else None,
        product_type=product_type if product_type else None,
        store_type=store_type if store_type else None,
    )
    if err:
        return fail(message=err, code=400)

    start_d, end_d = _resolve_filter_dates(
        date_point=date, start_date=start_date, end_date=end_date
    )
    with get_db() as db:
        q = _apply_filters(
            db.query(PendingOrderORM),
            status=status,
            store_type=store_type,
            product_type=product_type,
            plan_type=plan_type,
            user_id=user_id,
            transaction_id=transaction_id,
            product_id=product_id,
            package_key=package_key,
            keyword=keyword,
            start_d=start_d,
            end_d=end_d,
            db=db,
        )
        total = q.count()
        rows = (
            q.order_by(desc(PendingOrderORM.created_at), desc(PendingOrderORM.id))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = _enrich_orders(db, rows, include_callback=False)

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


@router.get("/api/admin/pending-orders/{order_id}", summary="预订单详情")
async def admin_get_pending_order(
    order_id: int,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        row = db.query(PendingOrderORM).filter(PendingOrderORM.id == order_id).first()
        if not row:
            return fail(message="预订单不存在", code=404)
        items = _enrich_orders(db, [row], include_callback=True)
        return ok(data=items[0])


@router.post("/api/admin/pending-orders", summary="创建预订单")
async def admin_create_pending_order(
    body: PendingOrderCreate,
    _token: str = Depends(admin_auth_required),
):
    err = _validate_enums(
        status=body.status,
        product_type=body.product_type,
        store_type=body.store_type,
    )
    if err:
        return fail(message=err, code=400)

    tx = body.transaction_id.strip()
    with get_db() as db:
        exists = (
            db.query(PendingOrderORM)
            .filter(PendingOrderORM.transaction_id == tx)
            .first()
        )
        if exists:
            return fail(message=f"transaction_id 已存在: {tx}", code=400)
        if body.user_id is not None:
            user = db.query(UserORM).filter(UserORM.id == body.user_id).first()
            if not user:
                return fail(message="充值用户不存在", code=400)

        now = datetime.now(timezone.utc)
        row = PendingOrderORM(
            transaction_id=tx,
            original_transaction_id=(body.original_transaction_id or "").strip(),
            product_id=body.product_id.strip(),
            product_type=body.product_type,
            plan_type=(body.plan_type or "").strip(),
            package_key=(body.package_key or "").strip(),
            price_usd=float(body.price_usd or 0),
            credits_amount=int(body.credits_amount or 0),
            duration_days=int(body.duration_days or 0),
            store_type=body.store_type,
            status=body.status or "pending",
            user_id=body.user_id,
            callback_data=body.callback_data or "",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        items = _enrich_orders(db, [row], include_callback=True)
        return ok(data=items[0], message="创建成功")


@router.put("/api/admin/pending-orders/{order_id}", summary="更新预订单")
async def admin_update_pending_order(
    order_id: int,
    body: PendingOrderUpdate,
    _token: str = Depends(admin_auth_required),
):
    payload = body.model_dump(exclude_unset=True)
    if not payload:
        return fail(message="无更新字段", code=400)

    err = _validate_enums(
        status=payload.get("status"),
        product_type=payload.get("product_type"),
        store_type=payload.get("store_type"),
    )
    if err:
        return fail(message=err, code=400)

    with get_db() as db:
        row = db.query(PendingOrderORM).filter(PendingOrderORM.id == order_id).first()
        if not row:
            return fail(message="预订单不存在", code=404)

        if "transaction_id" in payload:
            tx = (payload["transaction_id"] or "").strip()
            if not tx:
                return fail(message="transaction_id 不能为空", code=400)
            dup = (
                db.query(PendingOrderORM)
                .filter(
                    PendingOrderORM.transaction_id == tx,
                    PendingOrderORM.id != order_id,
                )
                .first()
            )
            if dup:
                return fail(message=f"transaction_id 已存在: {tx}", code=400)
            row.transaction_id = tx

        if "user_id" in payload and payload["user_id"] is not None:
            user = db.query(UserORM).filter(UserORM.id == payload["user_id"]).first()
            if not user:
                return fail(message="充值用户不存在", code=400)
            row.user_id = payload["user_id"]
        elif "user_id" in payload:
            row.user_id = None

        for field in (
            "original_transaction_id",
            "product_id",
            "product_type",
            "plan_type",
            "package_key",
            "store_type",
            "status",
            "callback_data",
        ):
            if field in payload and payload[field] is not None:
                val = payload[field]
                if isinstance(val, str) and field != "callback_data":
                    val = val.strip()
                setattr(row, field, val)

        if "price_usd" in payload and payload["price_usd"] is not None:
            row.price_usd = float(payload["price_usd"])
        if "credits_amount" in payload and payload["credits_amount"] is not None:
            row.credits_amount = int(payload["credits_amount"])
        if "duration_days" in payload and payload["duration_days"] is not None:
            row.duration_days = int(payload["duration_days"])

        row.updated_at = datetime.now(timezone.utc)
        db.flush()
        items = _enrich_orders(db, [row], include_callback=True)
        return ok(data=items[0], message="更新成功")


@router.delete("/api/admin/pending-orders/{order_id}", summary="删除预订单")
async def admin_delete_pending_order(
    order_id: int,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        row = db.query(PendingOrderORM).filter(PendingOrderORM.id == order_id).first()
        if not row:
            return fail(message="预订单不存在", code=404)
        db.delete(row)
        return ok(data={"id": order_id, "deleted": True}, message="删除成功")
