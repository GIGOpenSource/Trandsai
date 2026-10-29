"""
支付相关 API 路由

端点：
- POST /api/payment/verify-apple     — Apple IAP 收据验证
- POST /api/payment/verify-google    — Google Play 购买验证
- POST /api/payment/apple-notification — Apple Server Notifications V2 回调
- POST /api/payment/google-notification — Google RTDN 回调
- GET  /api/payment/vip-status       — 当前用户VIP和积分状态（free_turns_* 为账号累计剩余）
- GET  /api/payment/free-turns       — 账号剩余免费消息次数（不依赖 WS）
- GET  /api/payment/config           — VIP档位和充值包配置
- GET  /api/payment/transactions     — 用户消费账单
- POST /api/payment/check-permission — 权限检查（对话/创建智能体/评论）
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query, Depends, Request
from core.client_context import extract_client_device, extract_client_ip
from core.dependencies import require_permissions
from core.auth import verify_token
from core.database import CreditTransactionORM, RechargeRecordORM, VipSubscriptionORM, get_db
from core.permissions import IsAuthenticated
from core.i18n import (
    _,
    auth_required_message,
    credit_event_display_name,
    localize_credit_description,
    recharge_package_display_name,
    validation_message,
    vip_plan_display_name,
)
from core.response import ok, fail, paginated
from services.credit_service import (
    activate_vip,
    check_agent_creation_permission,
    check_chat_permission,
    get_cost_config,
    get_or_create_user_credits,
    get_recharge_packages,
    get_user_vip_status,
    get_vip_plans,
    recharge_credits,
    refresh_daily_gifts,
    touch_user_client_context,
)
from services.payment_receipt import (
    extract_google_obfuscated_account_id,
    normalize_apple_uuid,
    parse_apple_account_user_id,
    parse_google_account_user_id,
)
from services.payment_verify import (
    acknowledge_google_subscription,
    parse_apple_notification_v2,
    parse_google_rtdn_message,
    verify_apple_receipt,
    verify_google_purchase,
    verify_google_pubsub_push_auth,
)

_REDACT_PAY_KEYS = (
    "transaction_receipt",
    "receipt",
    "receipt_data",
    "purchaseToken",
    "purchase_token",
    "signedPayload",
)


def _redact_pay_body(data):
    if isinstance(data, dict):
        out = {}
        for k, val in data.items():
            if k in _REDACT_PAY_KEYS and val:
                s = str(val)
                out[k] = f"{s[:6]}…len={len(s)}" if len(s) > 6 else "***"
            else:
                out[k] = _redact_pay_body(val)
        return out
    if isinstance(data, list):
        return [_redact_pay_body(x) for x in data]
    return data


def _apple_account_fields(info: Optional[dict]) -> dict:
    """从 JWS 交易/续订里抽出账号相关字段（appAccountToken / username 等）。"""
    if not isinstance(info, dict):
        return {}
    out = {}
    for k, v in info.items():
        if v in (None, ""):
            continue
        lk = str(k).lower()
        if any(s in lk for s in ("account", "user", "username", "token")):
            out[k] = v
    return out


def _apple_token_candidates(notification: Optional[dict]) -> list:
    n = notification or {}
    tx = n.get("tx_info") if isinstance(n.get("tx_info"), dict) else {}
    renewal = n.get("renewal_info") if isinstance(n.get("renewal_info"), dict) else {}
    return [
        n.get("app_account_token"),
        tx.get("appAccountToken"),
        renewal.get("appAccountToken"),
        tx.get("applicationUsername"),
        tx.get("username"),
        tx.get("userName"),
        renewal.get("applicationUsername"),
    ]


def _lookup_user_id_by_apple_token(db, token) -> Optional[int]:
    from core.database import AppleAppAccountTokenORM

    key = normalize_apple_uuid(token)
    if not key:
        return None
    row = (
        db.query(AppleAppAccountTokenORM)
        .filter(AppleAppAccountTokenORM.app_account_token == key)
        .first()
    )
    if row and row.user_id:
        return int(row.user_id)
    return None


def _upsert_apple_app_account_token(db, token, user_id: int) -> None:
    from core.database import AppleAppAccountTokenORM

    key = normalize_apple_uuid(token)
    if not key or not user_id:
        return
    row = (
        db.query(AppleAppAccountTokenORM)
        .filter(AppleAppAccountTokenORM.app_account_token == key)
        .first()
    )
    if row:
        if int(row.user_id) != int(user_id):
            logger.warning(
                "[VERIFY_APPLE] app_account_token 映射冲突 token=%s mapped_user=%s x-token_user=%s，以 x-token 为准",
                key,
                row.user_id,
                user_id,
            )
            row.user_id = int(user_id)
        return
    db.add(
        AppleAppAccountTokenORM(
            app_account_token=key,
            user_id=int(user_id),
        )
    )


def _payer_from_apple_notification(notification: Optional[dict], db=None) -> Optional[int]:
    for c in _apple_token_candidates(notification):
        if db is not None:
            uid = _lookup_user_id_by_apple_token(db, c)
            if uid:
                return uid
        uid = parse_apple_account_user_id(c)
        if uid:
            return uid
        s = str(c or "").strip()
        if s.isdigit() and int(s) > 0:
            return int(s)
    return None


def _pay_msg(request: Request, key: str) -> str:
    return validation_message(key, request.headers.get("accept-language"))


def _google_expire_at(vr: dict) -> Optional[datetime]:
    expiry_rfc = str((vr or {}).get("expiry_time") or "").strip()
    if expiry_rfc:
        try:
            dt = datetime.fromisoformat(expiry_rfc.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    expiry_ms = str((vr or {}).get("expiry_time_millis") or "").strip()
    if expiry_ms.isdigit() and int(expiry_ms) > 0:
        return datetime.fromtimestamp(int(expiry_ms) / 1000, tz=timezone.utc)
    return None


def _google_purchase_time(vr: dict) -> Optional[datetime]:
    ptms = str((vr or {}).get("purchase_time_ms") or "").strip()
    if ptms.isdigit() and int(ptms) > 0:
        return datetime.fromtimestamp(int(ptms) / 1000, tz=timezone.utc)
    return None


def _lookup_benefit_google(db, store_product_id: str):
    from core.database import BenefitProductORM

    pid = (store_product_id or "").strip()
    if not pid:
        return None
    return db.query(BenefitProductORM).filter(
        BenefitProductORM.store_product_id == pid
    ).first()


def _google_tx_ids(*values) -> list[str]:
    out: list[str] = []
    seen = set()
    for v in values:
        s = str(v or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _lookup_google_paid_user_id(db, *tx_ids) -> Optional[int]:
    """验单已入账时，用 recharge_records / vip_subscriptions 反查付款人。"""
    ids = _google_tx_ids(*tx_ids)
    if not ids:
        return None
    rec = (
        db.query(RechargeRecordORM)
        .filter(RechargeRecordORM.store_transaction_id.in_(ids))
        .first()
    )
    if rec and rec.user_id:
        return int(rec.user_id)
    vip = (
        db.query(VipSubscriptionORM)
        .filter(VipSubscriptionORM.store_transaction_id.in_(ids))
        .first()
    )
    if vip and vip.user_id:
        return int(vip.user_id)
    return None


def _google_vr_account_user_id(vr: Optional[dict]) -> Optional[int]:
    return parse_google_account_user_id(extract_google_obfuscated_account_id(vr or {}))


def _bind_google_pending_payer(pending, user_id: Optional[int], *, completed: bool = False) -> None:
    if pending is None or not user_id:
        return
    pending.user_id = int(user_id)
    if completed:
        pending.status = "completed"


def _find_google_pending_order(db, order_id: str = "", purchase_token: str = ""):
    from core.database import PendingOrderORM

    pending_order = None
    oid = str(order_id or "").strip()
    token = str(purchase_token or "").strip()
    if oid:
        pending_order = db.query(PendingOrderORM).filter(
            PendingOrderORM.transaction_id == oid
        ).first()
        if not pending_order:
            pending_order = db.query(PendingOrderORM).filter(
                PendingOrderORM.original_transaction_id == oid
            ).first()
    if not pending_order and token:
        pending_order = db.query(PendingOrderORM).filter(
            PendingOrderORM.transaction_id == token
        ).first()
        if not pending_order:
            pending_order = db.query(PendingOrderORM).filter(
                PendingOrderORM.original_transaction_id == token
            ).first()
    if pending_order:
        return pending_order
    if not oid and not token:
        return None
    pending_orders = db.query(PendingOrderORM).filter(
        PendingOrderORM.store_type == "google_play",
        PendingOrderORM.status == "pending",
    ).all()
    for po in pending_orders:
        blob = po.callback_data or ""
        if oid and oid in blob:
            return po
        if token and token in blob:
            return po
    return None


def _resolve_google_pending_payer(db, *, purchase_token: str = "", order_id: str = "", vr: Optional[dict] = None) -> Optional[int]:
    paid = _lookup_google_paid_user_id(db, purchase_token, order_id, (vr or {}).get("order_id"))
    if paid:
        return paid
    return _google_vr_account_user_id(vr)


_GOOGLE_WAIT_REASONS = frozenset({
    "subscription_pending",
    "purchase_not_found",
    "google_auth_failed",
    "missing_product_or_token",
})


def _google_vr_is_waiting(vr: Optional[dict]) -> bool:
    if not vr:
        return True
    if vr.get("valid"):
        return False
    reason = str(vr.get("reason") or "")
    state = str(vr.get("subscription_state") or "")
    if state == "SUBSCRIPTION_STATE_PENDING" or reason == "subscription_pending":
        return True
    if reason in _GOOGLE_WAIT_REASONS or reason.startswith("verify_error"):
        return True
    return False


def _google_pending_verify_result(message: Optional[str] = None) -> dict:
    return {
        "status": "pending",
        "processed": False,
        "message": message or _("支付处理中"),
    }


def _credits_added_fields(result: Optional[dict] = None, recharge_row=None) -> dict:
    """本次到账金币。前端 Toast 用 credits_added 或 credits，不要用 balance。"""
    n = 0
    if isinstance(result, dict) and result.get("credits_added") is not None:
        try:
            n = int(result.get("credits_added") or 0)
        except (TypeError, ValueError):
            n = 0
    elif recharge_row is not None:
        try:
            n = int(getattr(recharge_row, "credits", 0) or 0)
        except (TypeError, ValueError):
            n = 0
    return {"credits_added": n, "credits": n}


router = APIRouter(tags=["支付"])
logger = logging.getLogger(__name__)


def _extract_client_ip(request: Request) -> str:
    return extract_client_ip(request)


def _extract_client_device(request: Request, x_device_id: Optional[str]) -> str:
    return extract_client_device(request, x_device_id)


# ===== 商品 ID 映射（商店 product_id ↔ 内部 plan/package key） =====
# key: 内部标识, value: 商店中的 product_id
APPLE_PRODUCT_MAP = {
    "weekly":   "com.trandsai.vip.weekly",
    "monthly":  "com.trandsai.vip.monthly",
    "yearly":   "com.trandsai.vip.yearly",
    "lifetime": "Lifetime.VIP",
    "pack_250":  "Points4.99",
    "pack_550":  "Points9.99",
    "pack_800":  "Points14.99",
    "pack_1900": "Points1900",
}

GOOGLE_PRODUCT_MAP = {
    "weekly":   "vip_weekly",
    "monthly":  "vip_monthly",
    "yearly":   "vip_yearly",
    "lifetime": "vip_lifetime",
    "pack_250":  "pack_250",
    "pack_550":  "pack_550",
    "pack_800":  "pack_800",
    "pack_1900": "pack_1900",
}


def _get_user_id(x_token: Optional[str]) -> Optional[int]:
    if not x_token:
        return None
    return verify_token(x_token)


def _reject_guest_pay(request: Request, user_id: int):
    """临时（guest）登录禁止 IAP；返回 fail dict，正式账号返回 None。"""
    from services.guest_auth import assert_user_can_pay

    al = request.headers.get("accept-language")
    try:
        with get_db() as db:
            assert_user_can_pay(db, int(user_id), accept_language=al)
    except HTTPException as e:
        return fail(message=str(e.detail), code=e.status_code)
    return None


# ===== 用户端接口 =====

@router.get("/api/payment/credit-explain", summary="积分说明")
async def api_credit_explain():
    """返回积分消费说明"""
    costs = get_cost_config()
    return ok(data={
        "chat": costs["chat_cost"],
        "create_companion": costs["create_agent_cost"],
        "maintain_companion": costs["agent_daily_cost"],
        "free_turns_limit": costs["free_turns_limit"],
    })


@router.get("/api/payment/vip-status", summary="获取VIP和积分状态")
async def api_vip_status(
    companion_id: Optional[str] = Query(
        None, description="可选；仅回传 companion_id。free_turns_* 始终为该账号累计剩余"
    ),
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    from core.database import UserORM
    from services.guest_auth import guest_flags

    cid = (companion_id or "").strip()[:8] or None
    with get_db() as db:
        status = get_user_vip_status(db, user_id, companion_id=cid)
        user = db.query(UserORM).filter(UserORM.id == int(user_id)).first()
        status = {**(status or {}), **guest_flags(user)}
    return ok(data=status)


@router.get("/api/payment/free-turns", summary="账号剩余免费消息次数")
async def api_payment_free_turns(
    companion_id: str = Query(..., min_length=1, description="当前聊天智能体 id"),
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    """进房展示剩余次数：不依赖长连接握手，可与 WS 并行请求。"""
    from services.credit_service import get_chat_free_turns_status

    cid = (companion_id or "").strip()[:8]
    if not cid:
        return fail(message=validation_message("companion_id_required"), code=400)
    return ok(data=get_chat_free_turns_status(user_id, cid))


@router.get("/api/payment/config", summary="获取VIP和充值包配置")
async def api_payment_config():
    """返回VIP档位配置和充值包配置（不含价格，价格由前端从商店获取）"""
    plans = get_vip_plans()
    packages = get_recharge_packages()
    costs = get_cost_config()
    return ok(data={
        "plans": plans,
        "packages": packages,
        "costs": costs,
    })


@router.get("/api/payment/transactions", summary="获取交易记录")
async def api_transactions(
    request: Request,
    user_id: int = Depends(require_permissions(IsAuthenticated)),
    type: str = Query(..., description="记录类型: vip_recharge_record=VIP充值 | point_recharge_record=积分充值 | free_recharge_record=赠送记录 | point_use_record=积分使用"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    x_device_id: Optional[str] = Header(None, alias="X-Device-Id"),
):
    """
    获取当前用户的交易记录（四种类型）

    - type=vip_recharge_record: VIP充值/续费记录
    - type=point_recharge_record: 积分充值记录（不含赠送）
    - type=free_recharge_record: 赠送记录（VIP开通赠送的积分）
    - type=point_use_record: 积分使用记录（对话/创建智能体/智能体每日消耗）
    """
    offset = (page - 1) * page_size
    client_ip = _extract_client_ip(request)
    client_device = _extract_client_device(request, x_device_id)
    logger.info(
        "[payment.transactions] user_id=%s type=%s page=%s page_size=%s ip=%s device=%s",
        user_id,
        type,
        page,
        page_size,
        client_ip,
        client_device,
    )

    with get_db() as db:
        touch_user_client_context(
            db,
            user_id,
            ip=client_ip,
            device=client_device,
        )
        db.commit()

        # VIP充值记录
        if type == "vip_recharge_record":
            query = db.query(VipSubscriptionORM).filter(VipSubscriptionORM.user_id == user_id)
            total = query.count()
            items = query.order_by(VipSubscriptionORM.created_at.desc()).offset(offset).limit(page_size).all()
            return paginated(
                items=[{
                    "id": s.id,
                    "plan_type": s.plan_type or "",
                    "plan_name": vip_plan_display_name(s.plan_type),
                    "price_usd": s.price_usd or 0,
                    "daily_credits": s.daily_credits or 0,
                    "max_agents": s.max_agents or 0,
                    "start_at": s.start_at.isoformat() if s.start_at else None,
                    "expire_at": s.expire_at.isoformat() if s.expire_at else None,
                    "status": s.status or "",
                    "store_type": s.store_type or "",
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                } for s in items],
                total=total, page=page, page_size=page_size,
            )

        # 积分充值记录（排除赠送）
        if type == "point_recharge_record":
            query = db.query(RechargeRecordORM).filter(
                RechargeRecordORM.user_id == user_id,
                ~RechargeRecordORM.package_key.startswith("vip_gift_"),  # 排除赠送记录
            )
            total = query.count()
            items = query.order_by(RechargeRecordORM.created_at.desc()).offset(offset).limit(page_size).all()
            return paginated(
                items=[{
                    "id": r.id,
                    "package_key": r.package_key or "",
                    "package_name": recharge_package_display_name(r.package_key),
                    "credits": r.credits or 0,
                    "price_usd": r.price_usd or 0,
                    "store_type": r.store_type or "",
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                } for r in items],
                total=total, page=page, page_size=page_size,
            )

        # 赠送记录（VIP开通赠送的积分）
        if type == "free_recharge_record":
            query = db.query(RechargeRecordORM).filter(
                RechargeRecordORM.user_id == user_id,
                RechargeRecordORM.package_key.startswith("vip_gift_"),  # 只查赠送记录
            )
            total = query.count()
            items = query.order_by(RechargeRecordORM.created_at.desc()).offset(offset).limit(page_size).all()
            return paginated(
                items=[{
                    "id": r.id,
                    "package_key": r.package_key or "",
                    "gift_type": _get_gift_type(r.package_key),  # 从 vip_gift_weekly 提取 weekly
                    "credits": r.credits or 0,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                } for r in items],
                total=total, page=page, page_size=page_size,
            )

        # 积分使用记录（消耗类账单）
        if type == "point_use_record":
            # 只查消耗类记录（amount < 0），排除免费轮次（amount=0）
            query = db.query(CreditTransactionORM).filter(
                CreditTransactionORM.user_id == user_id,
                CreditTransactionORM.amount < 0,  # 只查消耗类
            )
            total = query.count()
            items = query.order_by(CreditTransactionORM.created_at.desc()).offset(offset).limit(page_size).all()
            return paginated(
                items=[{
                    "id": tx.id,
                    "event_type": tx.event_type,
                    "event_name": credit_event_display_name(tx.event_type),
                    "amount": abs(tx.amount),  # 返回正数，前端展示更直观
                    "balance_after": tx.balance_after,
                    "description": localize_credit_description(tx.description or ""),
                    "created_at": tx.created_at.isoformat() if tx.created_at else None,
                } for tx in items],
                total=total, page=page, page_size=page_size,
            )

        return ok(data={"items": [], "total": 0})


def _get_vip_plan_name(plan_type: str) -> str:
    """VIP档位展示名（随请求语言）。"""
    return vip_plan_display_name(plan_type)


def _get_recharge_package_name(package_key: str) -> str:
    """积分包展示名（随请求语言）。"""
    return recharge_package_display_name(package_key)


def _get_gift_type(package_key: str) -> str:
    """从 vip_gift_weekly 提取赠送类型"""
    # package_key 格式: vip_gift_weekly, vip_gift_monthly 等
    if package_key.startswith("vip_gift_"):
        plan_type = package_key.replace("vip_gift_", "")
        return _get_vip_plan_name(plan_type)
    return package_key


def _get_event_name(event_type: str) -> str:
    """积分使用类型展示名（随请求语言）。"""
    return credit_event_display_name(event_type)


@router.post("/api/payment/check-permission", summary="权限检查")
async def api_check_permission(
    request: Request,
    data: dict = Body(...),
    x_token: Optional[str] = Header(None),
):
    """
    检查用户是否有权限执行某操作。
    body: {"action": "chat" | "create_agent" | "comment"}
    """
    user_id = _get_user_id(x_token)
    if not user_id:
        return fail(
            message=auth_required_message(request.headers.get("accept-language")),
            code=401,
        )

    action = data.get("action", "")
    if not action:
        return fail(message=_pay_msg(request, "missing_action"))

    with get_db() as db:
        if action == "chat":
            result = check_chat_permission(db, user_id)
        elif action == "create_agent":
            result = check_agent_creation_permission(db, user_id)
        else:
            result = {"allowed": True, "reason": "no_restriction"}

    return ok(data=result)


# ===== Apple IAP 收据验证 =====

@router.post("/api/payment/verify-apple", summary="Apple IAP 收据验证")
async def api_verify_apple(
    request: Request,
    data: dict = Body(...),
    x_token: Optional[str] = Header(None),
):
    """
    前端传入 Apple 购买收据，后端验证并完成充值/激活VIP。
    body: {
        "receipt_data": "base64编码的收据",
        "product_type": "vip" | "recharge",  // 产品类型
        "plan_type": "weekly",               // VIP类型（product_type=vip时必填）
        "package_key": "pack_250"            // 充值包（product_type=recharge时必填）
    }
    """
    user_id = _get_user_id(x_token)
    if not user_id:
        return fail(
            message=auth_required_message(request.headers.get("accept-language")),
            code=401,
        )
    guest_block = _reject_guest_pay(request, user_id)
    if guest_block is not None:
        return guest_block

    receipt_data = data.get("receipt_data", "")
    product_type = data.get("product_type", "")
    plan_type = data.get("plan_type", "")
    package_key = data.get("package_key", "")

    if not receipt_data:
        return fail(message=_pay_msg(request, "missing_receipt_data"))
    if product_type not in ("vip", "recharge"):
        return fail(message=_pay_msg(request, "product_type_invalid"))
    if product_type == "vip" and not plan_type:
        return fail(message=_pay_msg(request, "vip_missing_plan"))
    if product_type == "recharge" and not package_key:
        return fail(message=_pay_msg(request, "recharge_missing_package"))

    # 验证收据
    verify_result = verify_apple_receipt(receipt_data)
    if not verify_result.get("valid"):
        return fail(message=_pay_msg(request, "apple_receipt_invalid"), code=400)

    transaction_id = verify_result.get("transaction_id", "")
    product_id = verify_result.get("product_id", "")

    # 校验收据中的 product_id 是否与客户端声称的一致
    expected_key = plan_type if product_type == "vip" else package_key
    expected_product_id = APPLE_PRODUCT_MAP.get(expected_key, "")
    if expected_product_id and product_id != expected_product_id:
        logger.warning("[payment] Apple product_id 不匹配: 收据=%s, 期望=%s", product_id, expected_product_id)
        return fail(message=_pay_msg(request, "product_id_mismatch"), code=400)

    if not transaction_id:
        return fail(message=_pay_msg(request, "missing_transaction_id"))

    # 从 Apple 验证结果获取真实购买时间
    apple_purchased_at = None
    pdms = verify_result.get("purchase_date_ms", "")
    if pdms and str(pdms).isdigit() and int(pdms) > 0:
        apple_purchased_at = datetime.fromtimestamp(int(pdms) / 1000, tz=timezone.utc)

    with get_db() as db:
        if product_type == "vip":
            result = activate_vip(
                db, user_id, plan_type, "app_store",
                transaction_id, json.dumps(verify_result.get("raw", {})),
                product_id=product_id,
                purchased_at=apple_purchased_at,
            )
        else:
            result = recharge_credits(
                db, user_id, package_key, "app_store",
                transaction_id, json.dumps(verify_result.get("raw", {})),
                purchased_at=apple_purchased_at,
            )

        if not result.get("ok"):
            return fail(message=result.get("reason") or _pay_msg(request, "operation_failed"), code=400)

        db.commit()

        status = get_user_vip_status(db, user_id)

    return ok(data={**result, **status}, message=_("验证成功"))


# ===== Apple 收据验证（前端调用） =====

@router.post("/api/payment/verify-apple-receipt", summary="Apple 收据验证（前端调用）")
async def api_verify_apple_receipt(
    request: Request,
    data: dict = Body(...),
    x_token: Optional[str] = Header(None),
):
    """
    前端支付成功后调用此接口验证收据并创建订单。

    请求示例：
    {
        "transaction_identifier": "2000001219257861",
        "transaction_receipt": "MIIm3AY...",
        "product_id": "7subscription"
    }

    响应示例：
    {
        "ok": true,
        "data": {
            "status": "completed",
            "processed": true,
            "transaction_id": "2000001219257861",
            "product_type": "subscribe",
            "message": "支付成功"
        }
    }
    """
    # 打印请求日志
    logger.info("[VERIFY_APPLE] 收到验证请求 user_id pending, body=%s", _redact_pay_body(data))

    # 获取用户ID
    user_id = _get_user_id(x_token)
    if not user_id:
        logger.info("[VERIFY_APPLE] 未登录")
        return fail(
            message=auth_required_message(request.headers.get("accept-language")),
            code=401,
        )
    guest_block = _reject_guest_pay(request, user_id)
    if guest_block is not None:
        return guest_block

    transaction_id = data.get("transaction_identifier", "")

    logger.info(f"[VERIFY_APPLE] user_id: {user_id}")
    logger.info(f"[VERIFY_APPLE] transaction_id: {transaction_id}")

    if not transaction_id:
        logger.info("[VERIFY_APPLE] 缺少 transaction_identifier")
        return fail(message=_pay_msg(request, "missing_transaction_identifier"))

    # 查询预订单
    from core.database import VipSubscriptionORM, RechargeRecordORM, PendingOrderORM

    # 保存预订单信息（避免 DetachedInstanceError）
    pending_order_data = None

    with get_db() as db:
        _upsert_apple_app_account_token(
            db,
            data.get("app_account_token") or data.get("appAccountToken"),
            user_id,
        )
        # 1. 检查订单是否已完成（幂等）
        existing_vip = db.query(VipSubscriptionORM).filter(
            VipSubscriptionORM.store_transaction_id == transaction_id
        ).first()
        existing_recharge = db.query(RechargeRecordORM).filter(
            RechargeRecordORM.store_transaction_id == transaction_id
        ).first()

        if existing_vip or existing_recharge:
            logger.info(f"[VERIFY_APPLE] 订单已完成: {transaction_id}")
            # 获取最新状态返回给前端
            status = get_user_vip_status(db, user_id)
            logger.info("="*60)
            pending_row = db.query(PendingOrderORM).filter(
                PendingOrderORM.transaction_id == transaction_id
            ).first()
            if pending_row:
                pending_row.user_id = user_id
                pending_row.status = "completed"
                db.commit()
            return ok(data={
                "status": "completed",
                "processed": True,
                "transaction_id": transaction_id,
                "message": _("订单已处理"),
                **status,
                **_credits_added_fields(recharge_row=existing_recharge),
            })

        # 2. 查询预订单
        logger.info(f"[VERIFY_APPLE] 查询预订单: transaction_id={transaction_id}")
        pending_order = db.query(PendingOrderORM).filter(
            PendingOrderORM.transaction_id == transaction_id
        ).first()

        client_product_id = str(
            data.get("product_id") or data.get("sku") or data.get("apple_product_id") or ""
        ).strip()

        if not pending_order:
            pending_order = db.query(PendingOrderORM).filter(
                PendingOrderORM.original_transaction_id == transaction_id,
                PendingOrderORM.store_type == "app_store",
            ).first()

        if not pending_order:
            logger.info(
                "[VERIFY_APPLE] 无预订单 tx=%s sku=%s（回调可能未到）",
                transaction_id,
                client_product_id or "(未传 product_id)",
            )
        else:
            # 保存预订单数据（在 session 关闭前）
            pending_order_data = {
                "id": pending_order.id,
                "transaction_id": pending_order.transaction_id,
                "product_id": pending_order.product_id,
                "product_type": pending_order.product_type,
                "plan_type": pending_order.plan_type,
                "package_key": pending_order.package_key,
                "price_usd": pending_order.price_usd,
                "credits_amount": pending_order.credits_amount,
                "status": pending_order.status,
                "callback_data": pending_order.callback_data,
            }
            logger.info(f"[VERIFY_APPLE] ✓ 找到预订单: ID={pending_order_data['id']}")
            logger.info(f"[VERIFY_APPLE] 预订单详情:")
            logger.info(f"[VERIFY_APPLE]   - transaction_id: {pending_order_data['transaction_id']}")
            logger.info(f"[VERIFY_APPLE]   - product_id: {pending_order_data['product_id']}")
            logger.info(f"[VERIFY_APPLE]   - product_type: {pending_order_data['product_type']}")
            logger.info(f"[VERIFY_APPLE]   - plan_type: {pending_order_data['plan_type']}")
            logger.info(f"[VERIFY_APPLE]   - package_key: {pending_order_data['package_key']}")
            logger.info(f"[VERIFY_APPLE]   - price_usd: {pending_order_data['price_usd']}")
            logger.info(f"[VERIFY_APPLE]   - credits_amount: {pending_order_data['credits_amount']}")
            logger.info(f"[VERIFY_APPLE]   - status: {pending_order_data['status']}")

    if not pending_order_data:
        client_product_id = str(
            data.get("product_id") or data.get("sku") or data.get("apple_product_id") or ""
        ).strip()
        if client_product_id:
            from core.database import BenefitProductORM

            with get_db() as db:
                product = db.query(BenefitProductORM).filter(
                    BenefitProductORM.apple_product_id == client_product_id
                ).first()
                if product:
                    pending_order_data = {
                        "id": None,
                        "transaction_id": transaction_id,
                        "product_id": client_product_id,
                        "product_type": product.product_type,
                        "plan_type": product.period if product.product_type == "subscribe" else "",
                        "package_key": "" if product.product_type == "subscribe" else (
                            product.apple_product_id or client_product_id
                        ),
                        "price_usd": product.price_usd or 0,
                        "credits_amount": int(product.credits_amount or 0),
                        "status": "pending",
                        "callback_data": "",
                    }
                    logger.info(
                        "[VERIFY_APPLE] 无回调预订单，按 sku 入账 product_id=%s type=%s",
                        client_product_id,
                        product.product_type,
                    )
        if not pending_order_data:
            logger.info("[VERIFY_APPLE] 等待 Apple 回调或请带 product_id 重试 tx=%s", transaction_id)
            return ok(data={
                "status": "pending",
                "processed": False,
                "transaction_id": transaction_id,
                "message": _("支付处理中"),
            })

    # 使用预订单信息
    product_type = pending_order_data['product_type']
    plan_type = pending_order_data['plan_type']
    package_key = pending_order_data['package_key']
    price_usd = pending_order_data['price_usd']

    logger.info(f"[VERIFY_APPLE] 最终商品信息: type={product_type}, plan={plan_type}, package={package_key}, price={price_usd}")

    # 从 callback_data 提取 Apple 真实购买时间
    apple_purchased_at = None
    try:
        cb = json.loads(pending_order_data['callback_data'] or "{}")
        pd_raw = cb.get("purchase_date") or cb.get("purchase_date_ms") or ""
        pd_str = str(pd_raw)
        if pd_str.isdigit() and int(pd_str) > 0:
            apple_purchased_at = datetime.fromtimestamp(int(pd_str) / 1000, tz=timezone.utc)
            logger.info(f"[VERIFY_APPLE] 使用 Apple purchase_date: {apple_purchased_at.isoformat()}")
    except Exception as e:
        logger.warning("[VERIFY_APPLE] 解析 purchase_date 失败，回退服务端时间: %s", e)

    # 创建正式订单
    with get_db() as db:
        if product_type == "subscribe":
            # VIP 订阅
            logger.info(f"[VERIFY_APPLE] 激活VIP: user_id={user_id}, plan={plan_type}, price={price_usd}")
            result = activate_vip(
                db, user_id, plan_type, "app_store",
                transaction_id, pending_order_data['callback_data'] or "",
                price_usd=price_usd,
                product_id=pending_order_data.get('product_id', ''),
                purchased_at=apple_purchased_at,
            )
        else:
            # 充值积分
            logger.info(f"[VERIFY_APPLE] 充值积分: user_id={user_id}, package={package_key}, price={price_usd}")
            result = recharge_credits(
                db, user_id, package_key or pending_order_data.get("product_id") or "",
                "app_store",
                transaction_id, pending_order_data['callback_data'] or "",
                price_usd=price_usd,
                purchased_at=apple_purchased_at,
                credits_amount=int(pending_order_data.get("credits_amount") or 0),
            )

        if not result.get("ok"):
            logger.info(f"[VERIFY_APPLE] 处理失败: {result.get('reason')}")
            # 更新预订单状态为失败
            pending_order = db.query(PendingOrderORM).filter(
                PendingOrderORM.transaction_id == transaction_id
            ).first()
            if pending_order:
                pending_order.status = "failed"
                pending_order.user_id = user_id
            logger.info("="*60)
            return ok(data={
                "status": "failed",
                "processed": False,
                "transaction_id": transaction_id,
                "message": result.get("reason", _("处理失败")),
            })

        # 更新预订单状态为完成
        pending_order = db.query(PendingOrderORM).filter(
            PendingOrderORM.transaction_id == transaction_id
        ).first()
        if pending_order:
            pending_order.status = "completed"
            pending_order.user_id = user_id
        logger.info(f"[VERIFY_APPLE] 预订单状态更新为 completed")

        # 获取最新状态（在 commit 之前调用，确保读取到最新数据）
        status = get_user_vip_status(db, user_id)

        db.commit()
        logger.info(f"[VERIFY_APPLE] 处理成功: {transaction_id}")

    logger.info(f"[VERIFY_APPLE] 完成")
    logger.info("="*60)

    return ok(data={
        "status": "completed",
        "processed": True,
        "transaction_id": transaction_id,
        "product_type": product_type,
        "message": _("支付成功"),
        **status,
        **result,
        **_credits_added_fields(result),
    })


# ===== Google Play 购买验证 =====

@router.post("/api/payment/verify-google-receipt", summary="Google Play 购买验证")
async def api_verify_google_receipt(
    request: Request,
    data: dict = Body(...),
    x_token: Optional[str] = Header(None),
):
    """
    前端支付成功后调用此接口验证并创建订单。
    调起收银台时请设置 BillingFlowParams.setObfuscatedAccountId(String.valueOf(userId))，
    服务端会从 Play API 解析并回填 pending_orders.user_id。

    请求示例：
    {
        "order_id": "GPA.3363-1774-5682-33853",        // Google Play 订单ID
        "purchase_token": "enaihedbeplddcmaaccdcbgl..."  // 购买token
    }

    响应示例：
    {
        "ok": true,
        "data": {
            "status": "completed",
            "processed": true,
            "message": "支付成功"
        }
    }
    """
    # 打印请求日志
    logger.info("[VERIFY_GOOGLE] 收到验证请求 body=%s", _redact_pay_body(data))

    # 获取用户ID
    user_id = _get_user_id(x_token)
    if not user_id:
        logger.info("[VERIFY_GOOGLE] 未登录")
        return fail(
            message=auth_required_message(request.headers.get("accept-language")),
            code=401,
        )
    guest_block = _reject_guest_pay(request, user_id)
    if guest_block is not None:
        return guest_block

    # 获取参数
    order_id = data.get("order_id", "") or data.get("orderId", "")
    purchase_token = data.get("purchase_token", "") or data.get("purchaseToken", "")

    logger.info(f"[VERIFY_GOOGLE] user_id: {user_id}")
    logger.info(f"[VERIFY_GOOGLE] order_id: {order_id}")
    logger.info("[VERIFY_GOOGLE] token_len=%s", len(purchase_token or ""))

    if not order_id and not purchase_token:
        logger.info("[VERIFY_GOOGLE] 缺少 order_id 或 purchase_token")
        return fail(message=_pay_msg(request, "missing_google_order"))

    # 从数据库查询预订单
    from core.database import VipSubscriptionORM, RechargeRecordORM

    with get_db() as db:
        # 1. 检查订单是否已完成（幂等）
        # 优先用 order_id 检查，再用 purchase_token
        existing_vip = None
        existing_recharge = None

        if order_id:
            existing_vip = db.query(VipSubscriptionORM).filter(
                VipSubscriptionORM.store_transaction_id == order_id
            ).first()
            existing_recharge = db.query(RechargeRecordORM).filter(
                RechargeRecordORM.store_transaction_id == order_id
            ).first()

        if not existing_vip and not existing_recharge and purchase_token:
            existing_vip = db.query(VipSubscriptionORM).filter(
                VipSubscriptionORM.store_transaction_id == purchase_token
            ).first()
            existing_recharge = db.query(RechargeRecordORM).filter(
                RechargeRecordORM.store_transaction_id == purchase_token
            ).first()

        if existing_vip or existing_recharge:
            logger.info(f"[VERIFY_GOOGLE] 订单已完成")
            pending_order = _find_google_pending_order(db, order_id, purchase_token)
            _bind_google_pending_payer(pending_order, user_id, completed=True)
            db.commit()
            status = get_user_vip_status(db, user_id)
            logger.info("="*60)
            return ok(data={
                "status": "completed",
                "processed": True,
                "message": _("订单已处理"),
                **status,
                **_credits_added_fields(recharge_row=existing_recharge),
            })

        pending_order = _find_google_pending_order(db, order_id, purchase_token)

        client_product_id = str(data.get("product_id") or data.get("sku") or "").strip()
        pending_product_id = ((pending_order.product_id or "").strip() if pending_order else "")
        pending_type = ((pending_order.product_type or "").strip() if pending_order else "")
        if not purchase_token:
            logger.info("[VERIFY_GOOGLE] 缺少 purchase_token，无法向 Google 验单")
            return ok(data={
                "status": "failed",
                "processed": False,
                "message": _("未找到对应的订单"),
            })

        treat_as_one_time = pending_type == "point" or (
            pending_type and pending_type != "subscribe"
        )
        vr = {}
        is_sub = False
        if treat_as_one_time:
            sku = client_product_id or pending_product_id
            vr = verify_google_purchase(sku, purchase_token, is_subscription=False)
        else:
            vr_sub = verify_google_purchase(
                client_product_id or pending_product_id, purchase_token, is_subscription=True
            )
            sub_state = str(vr_sub.get("subscription_state") or "")
            if sub_state == "SUBSCRIPTION_STATE_PENDING" or vr_sub.get("reason") == "subscription_pending":
                logger.info("[VERIFY_GOOGLE] Google 订阅仍在 PENDING，等待扣款")
                _bind_google_pending_payer(pending_order, user_id, completed=False)
                db.commit()
                return ok(data=_google_pending_verify_result())
            is_sub = bool(vr_sub.get("valid")) or bool(vr_sub.get("product_id"))
            if is_sub:
                vr = vr_sub
            else:
                sku = client_product_id or pending_product_id
                vr = verify_google_purchase(sku, purchase_token, is_subscription=False)
        if not vr.get("valid"):
            logger.info("[VERIFY_GOOGLE] Google 验单未通过: %s", vr.get("reason"))
            if _google_vr_is_waiting(vr):
                _bind_google_pending_payer(pending_order, user_id, completed=False)
                db.commit()
                return ok(data=_google_pending_verify_result())
            return ok(data={
                "status": "failed",
                "processed": False,
                "message": vr.get("reason") or _("未找到对应的订单"),
            })

        google_product_id = (
            (vr.get("product_id") or "").strip()
            or client_product_id
            or ((pending_order.product_id or "").strip() if pending_order else "")
        )
        product = _lookup_benefit_google(db, google_product_id)
        if pending_order:
            product_type = pending_order.product_type
            plan_type = pending_order.plan_type
            package_key = pending_order.package_key
            price_usd = pending_order.price_usd
            google_product_id = google_product_id or pending_order.product_id or ""
            logger.info("[VERIFY_GOOGLE] 找到预订单: ID=%s", pending_order.id)
        elif product:
            product_type = product.product_type
            price_usd = product.price_usd
            plan_type = product.period if product_type == "subscribe" else ""
            package_key = "" if product_type == "subscribe" else google_product_id
        else:
            product_type = "subscribe" if is_sub else "point"
            plan_type = "monthly" if is_sub else ""
            package_key = "" if is_sub else google_product_id
            price_usd = 0

        # Google 订阅以 purchase_token 作为主键，RTDN 续费才能对上
        final_transaction_id = purchase_token
        purchased_at = _google_purchase_time(vr)
        expire_at = _google_expire_at(vr) if product_type == "subscribe" else None
        logger.info(
            "[VERIFY_GOOGLE] 最终商品 type=%s plan=%s product=%s expire=%s trial=%s",
            product_type,
            plan_type,
            google_product_id,
            expire_at.isoformat() if expire_at else "",
            vr.get("is_trial_period"),
        )

        if product_type == "subscribe":
            logger.info("[VERIFY_GOOGLE] 激活VIP: user_id=%s, plan=%s", user_id, plan_type)
            result = activate_vip(
                db, user_id, plan_type, "google_play",
                final_transaction_id, json.dumps(vr.get("raw") or {}, ensure_ascii=False, default=str),
                price_usd=price_usd,
                product_id=google_product_id,
                purchased_at=purchased_at,
                expire_at=expire_at,
            )
        else:
            if pending_order:
                package_key = package_key or pending_order.product_id or google_product_id
                credits_hint = int(pending_order.credits_amount or 0)
            else:
                package_key = package_key or google_product_id
                credits_hint = int(getattr(product, "credits_amount", 0) or 0) if product else 0
            logger.info(
                "[VERIFY_GOOGLE] 充值积分: user_id=%s package=%s price=%s credits_hint=%s",
                user_id, package_key, price_usd, credits_hint,
            )
            result = recharge_credits(
                db, user_id, package_key, "google_play",
                final_transaction_id,
                (pending_order.callback_data if pending_order else "") or json.dumps(vr.get("raw") or {}, default=str),
                price_usd=price_usd,
                purchased_at=purchased_at,
                credits_amount=credits_hint,
            )

        if not result.get("ok"):
            logger.info(f"[VERIFY_GOOGLE] 处理失败: {result.get('reason')}")
            _bind_google_pending_payer(pending_order, user_id, completed=False)
            if pending_order:
                pending_order.status = "failed"
            logger.info("="*60)
            return ok(data={
                "status": "failed",
                "processed": False,
                "message": result.get("reason", _("操作失败")),
            })

        _bind_google_pending_payer(pending_order, user_id, completed=True)
        if pending_order:
            logger.info(f"[VERIFY_GOOGLE] 预订单状态更新为 completed")

        # 获取最新状态（在 commit 之前调用，确保读取到最新数据）
        status = get_user_vip_status(db, user_id)

        db.commit()
        logger.info(f"[VERIFY_GOOGLE] 处理成功: {order_id}")

    # 消耗 Google 一次性商品（不消耗则用户无法再次购买）
    logger.info(
        "[VERIFY_GOOGLE] 消耗检查 product_type=%s google_product_id=%s token_len=%s",
        product_type,
        google_product_id,
        len(purchase_token or ""),
    )
    if product_type != "subscribe":
        logger.info(f"[VERIFY_GOOGLE] 非订阅商品，调用 consume...")
        try:
            from services.payment_verify import consume_google_purchase
            consume_result = consume_google_purchase(google_product_id, purchase_token)
            logger.info(f"[VERIFY_GOOGLE] 消耗结果: {consume_result}")
        except Exception as ce:
            logger.info(f"[VERIFY_GOOGLE] ❌ 消耗调用异常（不影响订单）: {ce}")
    else:
        logger.info("[VERIFY_GOOGLE] 订阅商品，acknowledge")
        try:
            ack = acknowledge_google_subscription(google_product_id, purchase_token)
            logger.info("[VERIFY_GOOGLE] acknowledge 结果: %s", ack)
        except Exception as ae:
            logger.warning("[VERIFY_GOOGLE] acknowledge 异常（不影响订单）: %s", ae)

    logger.info(f"[VERIFY_GOOGLE] 完成")
    logger.info("="*60)

    return ok(data={
        "status": "completed",
        "processed": True,
        "product_type": product_type,
        "message": _("支付成功"),
        **status,
        **result,
        **_credits_added_fields(result),
    })


# ===== Apple Server Notifications V2 回调 =====

@router.post("/api/payment/apple-notification", summary="Apple 服务端通知回调")
async def api_apple_notification(body: dict = Body(...)):
    """
    Apple App Store Server Notifications V2 回调。
    Apple 直接调用此端点，无需用户认证。
    body: {"signedPayload": "..."}
    """
    signed_len = len(str((body or {}).get("signedPayload") or ""))
    logger.info("[APPLE_CALLBACK] 收到回调 signedPayload_len=%s keys=%s", signed_len, list((body or {}).keys()))

    # 保存到文件
    import os
    from datetime import datetime
    try:
        os.makedirs("/app/data", exist_ok=True)
        with open("/app/data/apple_callback.txt", "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"时间: {datetime.now().isoformat()}\n")
            f.write(f"内容: {str(body)}\n")
    except:
        pass

    signed_payload = body.get("signedPayload", "")
    if not signed_payload:
        return ok(data={"status": "ignored"})

    # 解析通知（跳过签名验证，因为验证有问题）
    notification = parse_apple_notification_v2(signed_payload, skip_verification=True)
    if not notification:
        return ok(data={"status": "parse_failed"})

    notif_type = notification.get("notification_type", "")
    original_tx_id = notification.get("original_transaction_id", "")
    product_id = notification.get("product_id", "")
    transaction_id = notification.get("transaction_id", "")

    tx_info = notification.get("tx_info") if isinstance(notification.get("tx_info"), dict) else {}
    renewal_info = notification.get("renewal_info") if isinstance(notification.get("renewal_info"), dict) else {}
    account_tx = _apple_account_fields(tx_info)
    account_renewal = _apple_account_fields(renewal_info)
    logger.info("=" * 60)
    logger.info("[APPLE_CALLBACK] ===== 解析通知数据 =====")
    logger.info("[APPLE_CALLBACK] notification_type=%s subtype=%s", notif_type, notification.get("subtype", ""))
    logger.info("[APPLE_CALLBACK] product_id=%s transaction_id=%s original_tx=%s", product_id, transaction_id, original_tx_id)
    logger.info("[APPLE_CALLBACK] tx_info.keys=%s", list(tx_info.keys()))
    logger.info(
        "[APPLE_CALLBACK] tx_info 全文=%s",
        json.dumps(tx_info, ensure_ascii=False, default=str),
    )
    logger.info(
        "[APPLE_CALLBACK] 账号字段(交易)=%s",
        json.dumps(account_tx, ensure_ascii=False, default=str) if account_tx else "(无 appAccountToken/username)",
    )
    if renewal_info:
        logger.info("[APPLE_CALLBACK] renewal_info.keys=%s", list(renewal_info.keys()))
        logger.info(
            "[APPLE_CALLBACK] 账号字段(续订)=%s",
            json.dumps(account_renewal, ensure_ascii=False, default=str) if account_renewal else "(无)",
        )
    with get_db() as _db:
        callback_payer = _payer_from_apple_notification(notification, _db)
    logger.info(
        "[APPLE_CALLBACK] app_account_token=%s mapped_user_id=%s",
        notification.get("app_account_token") or "(空)",
        callback_payer,
    )

    logger.info("[payment] Apple 通知: type=%s, subtype=%s, product=%s, original_tx=%s",
                notif_type, notification.get("subtype", ""), product_id, original_tx_id)

    # 根据通知类型处理
    if notif_type in ("SUBSCRIBED", "ONE_TIME_CHARGE"):
        # 首次订阅或一次性购买 → 创建预订单
        logger.info(f"[APPLE_CALLBACK] 处理通知类型: {notif_type}，创建预订单")
        _handle_apple_subscribed(notification)
    else:
        # 其他通知类型仅记录日志，不做业务处理
        logger.info(f"[APPLE_CALLBACK] 通知类型 {notif_type} 仅记录，不做业务处理")
        logger.info("[payment] Apple 通知类型 %s 已记录，等待前端验证处理", notif_type)

    logger.info("[payment] Apple 通知处理完成")
    logger.info("="*60)

    return ok(data={"status": "ok"})


def _handle_apple_subscribed(notification: dict):
    """处理 Apple 首次订阅通知（创建预订单）"""
    from core.database import BenefitProductORM, PendingOrderORM

    original_tx_id = notification.get("original_transaction_id", "")
    transaction_id = notification.get("transaction_id", "")
    product_id = notification.get("product_id", "")
    expires_date = notification.get("expires_date", "")
    purchase_date = notification.get("purchase_date", "")

    logger.info("="*60)
    logger.info(f"[APPLE_CALLBACK] ===== 开始处理首次订阅 =====")
    logger.info(f"[APPLE_CALLBACK] 原始 transaction_id: {transaction_id}")
    logger.info(f"[APPLE_CALLBACK] 原始 original_tx_id: {original_tx_id}")
    logger.info(f"[APPLE_CALLBACK] 产品ID (product_id): {product_id}")
    logger.info(f"[APPLE_CALLBACK] 到期时间 (expires_date): {expires_date}")
    logger.info(f"[APPLE_CALLBACK] 购买时间 (purchase_date): {purchase_date}")

    # 确定商品类型和参数
    product_type = "subscribe"
    plan_type = ""
    package_key = ""
    price_usd = 0
    credits_amount = 0
    duration_days = 0

    # 在同一个 Session 中完成所有操作
    try:
        with get_db() as db:
            payer_id = _payer_from_apple_notification(notification, db)
            if not payer_id:
                logger.warning(
                    "[APPLE_CALLBACK] 映射表未命中 app_account_token=%s（等验单 x-token 补映射）",
                    notification.get("app_account_token") or "(空)",
                )
            else:
                logger.info(
                    "[APPLE_CALLBACK] 建单用付款人 user_id=%s app_account_token=%s",
                    payer_id,
                    notification.get("app_account_token") or "(空)",
                )
            logger.info(f"[APPLE_CALLBACK] 步骤1: 查询数据库商品信息...")
            logger.info(f"[APPLE_CALLBACK] 查询条件: apple_product_id = {product_id}")

            # 从数据库查询商品信息
            product = db.query(BenefitProductORM).filter(
                BenefitProductORM.apple_product_id == product_id
            ).first()

            if product:
                logger.info(f"[APPLE_CALLBACK] ✓ 数据库找到商品:")
                logger.info(f"[APPLE_CALLBACK]   - product_type: {product.product_type}")
                logger.info(f"[APPLE_CALLBACK]   - period: {product.period}")
                logger.info(f"[APPLE_CALLBACK]   - price_usd: {product.price_usd}")
                logger.info(f"[APPLE_CALLBACK]   - credits_amount: {product.credits_amount}")
                logger.info(f"[APPLE_CALLBACK]   - daily_gift_credits: {product.daily_gift_credits}")
                logger.info(f"[APPLE_CALLBACK]   - max_agents: {product.max_agents}")
                logger.info(f"[APPLE_CALLBACK]   - store_product_id: {product.store_product_id}")
                logger.info(f"[APPLE_CALLBACK]   - apple_product_id: {product.apple_product_id}")

                product_type = product.product_type
                price_usd = product.price_usd
                credits_amount = product.credits_amount or 0

                if product_type == "subscribe":
                    # 根据 period 确定 plan_type 和 duration_days
                    if product.period == "weekly":
                        plan_type = "weekly"
                        duration_days = 7
                    elif product.period == "monthly":
                        plan_type = "monthly"
                        duration_days = 30
                    elif product.period == "yearly":
                        plan_type = "yearly"
                        duration_days = 365
                    elif product.period == "lifetime":
                        plan_type = "lifetime"
                        duration_days = 36500  # 100年
                    else:
                        logger.info(f"[APPLE_CALLBACK] ⚠ 未知的订阅周期: {product.period}")
                else:
                    # Apple 使用 apple_product_id 作为 package_key
                    package_key = product.apple_product_id or product_id
                    logger.info(f"[APPLE_CALLBACK]   - 充值包 package_key: {package_key}")
            else:
                logger.info(f"[APPLE_CALLBACK] ⚠ 数据库未找到商品，使用硬编码逻辑")
                # 硬编码逻辑
                if "subscription" in product_id.lower():
                    product_type = "subscribe"
                    if "week" in product_id.lower() or product_id == "7subscription":
                        plan_type = "weekly"
                        duration_days = 7
                    elif "month" in product_id.lower():
                        plan_type = "monthly"
                        duration_days = 30
                    elif "year" in product_id.lower():
                        plan_type = "yearly"
                        duration_days = 365
                    logger.info(f"[APPLE_CALLBACK] 硬编码识别为订阅: plan={plan_type}, days={duration_days}")
                elif "Points" in product_id or "points" in product_id.lower():
                    product_type = "point"
                    package_key = product_id
                    logger.info(f"[APPLE_CALLBACK] 硬编码识别为积分包: package_key={package_key}")
                else:
                    logger.info(f"[APPLE_CALLBACK] ⚠ 无法识别产品类型: {product_id}")

            logger.info(f"[APPLE_CALLBACK] 步骤2: 最终商品信息:")
            logger.info(f"[APPLE_CALLBACK]   - product_type: {product_type}")
            logger.info(f"[APPLE_CALLBACK]   - plan_type: {plan_type}")
            logger.info(f"[APPLE_CALLBACK]   - package_key: {package_key}")
            logger.info(f"[APPLE_CALLBACK]   - price_usd: {price_usd}")
            logger.info(f"[APPLE_CALLBACK]   - credits_amount: {credits_amount}")
            logger.info(f"[APPLE_CALLBACK]   - duration_days: {duration_days}")

            # 检查是否已存在预订单
            logger.info(f"[APPLE_CALLBACK] 步骤3: 检查预订单是否已存在...")
            existing = db.query(PendingOrderORM).filter(
                PendingOrderORM.transaction_id == transaction_id
            ).first()

            if existing:
                if payer_id and not existing.user_id:
                    existing.user_id = payer_id
                    db.commit()
                logger.info(
                    "[APPLE_CALLBACK] ✓ 预订单已存在: ID=%s status=%s user_id=%s",
                    existing.id,
                    existing.status,
                    existing.user_id,
                )
                return

            logger.info(f"[APPLE_CALLBACK] 步骤4: 创建预订单...")

            # 创建预订单
            pending_order = PendingOrderORM(
                transaction_id=transaction_id,
                original_transaction_id=original_tx_id,
                product_id=product_id,
                product_type=product_type,
                plan_type=plan_type,
                package_key=package_key,
                price_usd=price_usd,
                credits_amount=credits_amount,
                duration_days=duration_days,
                store_type="app_store",
                status="pending",
                user_id=payer_id,
                callback_data=json.dumps(notification, ensure_ascii=False, default=str),
            )
            db.add(pending_order)
            logger.info(f"[APPLE_CALLBACK] 预订单已添加到 session")

            logger.info(f"[APPLE_CALLBACK] 步骤5: 提交到数据库...")
            db.commit()
            logger.info(f"[APPLE_CALLBACK] ✓ 数据库提交成功!")

            logger.info(
                "[APPLE_CALLBACK] 预订单创建成功: ID=%s user_id=%s tx=%s",
                pending_order.id,
                pending_order.user_id,
                pending_order.transaction_id,
            )
            logger.info(f"[APPLE_CALLBACK] 等待前端验证...")
            logger.info("="*60)

    except Exception as e:
        logger.info(f"[APPLE_CALLBACK] ✗ 错误: {str(e)}")
        logger.info(f"[APPLE_CALLBACK] 错误类型: {type(e).__name__}")
        import traceback
        logger.info(f"[APPLE_CALLBACK] 堆栈:\n{traceback.format_exc()}")
        logger.info("="*60)


# ===== Google RTDN 回调 =====

@router.post("/api/payment/google-notification", summary="Google RTDN 通知回调")
async def api_google_notification(
    body: dict = Body(...),
    authorization: Optional[str] = Header(None),
):
    """
    Google Real-time Developer Notifications 回调。
    需携带 Pub/Sub Push OIDC Bearer；本地可用 GOOGLE_RTDN_SKIP_AUTH=1。
    body: {"message": {"data": "base64编码的消息", ...}}
    """
    if not verify_google_pubsub_push_auth(authorization):
        logger.info("[GOOGLE_CALLBACK] 认证失败")
        raise HTTPException(status_code=401, detail="invalid pubsub oidc token")

    message = body.get("message", {})
    data_b64 = message.get("data", "")

    if not data_b64:
        logger.info("[GOOGLE_CALLBACK] 缺少 message.data")
        return ok(data={"status": "ignored"})

    notification = parse_google_rtdn_message(data_b64)
    if not notification:
        logger.info("[GOOGLE_CALLBACK] 消息解析失败")
        return ok(data={"status": "parse_failed"})

    notif_type = notification.get("notification_type", 0)
    notif_name = notification.get("notification_type_name", "")
    purchase_token = notification.get("purchase_token", "")
    subscription_id = notification.get("subscription_id", "")

    logger.info(
        "[GOOGLE_CALLBACK] type=%s(%s) product=%s token_len=%s",
        notif_name,
        notif_type,
        notification.get("product_id") or subscription_id,
        len(purchase_token or ""),
    )

    raw = notification.get("raw") or {}
    one_time = raw.get("oneTimeProductNotification") or {}
    if one_time:
        # 一次性金币：PURCHASED=1，CANCELED=2（不要当成订阅 RECOVERED/RENEWED）
        if int(one_time.get("notificationType") or notif_type or 0) == 1:
            logger.info("[GOOGLE_CALLBACK] 处理: 一次性金币购买")
            _handle_google_subscribed(notification)
        else:
            logger.info("[GOOGLE_CALLBACK] 一次性商品通知忽略: type=%s", notif_type)
    elif notif_type == 4:  # SUBSCRIPTION_PURCHASED（含免费试用）
        logger.info("[GOOGLE_CALLBACK] 处理: 首次订阅/购买")
        _handle_google_subscribed(notification)
    elif notif_type in (1, 2, 7):  # RECOVERED / RENEWED / RESTARTED
        logger.info("[GOOGLE_CALLBACK] 处理: 续费/恢复")
        _handle_google_renewed(notification)
    elif notif_type == 3:  # CANCELED：只关自动续订，到期前仍有效
        logger.info("[GOOGLE_CALLBACK] 处理: 取消自动续订")
        _handle_google_auto_renew_off(notification)
    elif notif_type in (12, 13):  # REVOKED / EXPIRED
        logger.info("[GOOGLE_CALLBACK] 处理: 撤销/过期")
        _handle_google_expired(notification)
    elif notif_type in (5, 9, 10):  # ON_HOLD / DEFERRED / PAUSED
        logger.info("[GOOGLE_CALLBACK] 处理: 暂停/扣款失败挂起")
        _handle_google_paused(notification)
    else:
        logger.info(f"[GOOGLE_CALLBACK] 未处理的通知类型: {notif_name}({notif_type})")

    logger.info("[GOOGLE_CALLBACK] 处理完成")
    logger.info("="*60)

    return ok(data={"status": "ok"})


def _handle_google_subscribed(notification: dict):
    """处理 Google 首次订阅/购买（创建预订单）"""
    from core.database import BenefitProductORM, PendingOrderORM

    purchase_token = notification.get("purchase_token", "")
    subscription_id = notification.get("subscription_id", "")
    product_id = notification.get("product_id", "")

    store_product_id = subscription_id or product_id
    raw = notification.get("raw") or {}
    is_one_time = bool(raw.get("oneTimeProductNotification"))
    vr = {}
    if purchase_token:
        if is_one_time and store_product_id:
            vr = verify_google_purchase(store_product_id, purchase_token, is_subscription=False)
        else:
            vr = verify_google_purchase(store_product_id or "", purchase_token, is_subscription=True)
            if not (vr.get("valid") or vr.get("product_id")) and store_product_id:
                vr = verify_google_purchase(store_product_id, purchase_token, is_subscription=False)
        store_product_id = store_product_id or (vr.get("product_id") or "").strip()
        if vr.get("needs_ack") and store_product_id:
            acknowledge_google_subscription(store_product_id, purchase_token)

    # 确定商品类型和参数
    product_type = "subscribe"
    plan_type = ""
    package_key = ""
    price_usd = 0
    credits_amount = 0
    duration_days = 0

    # 在同一个 Session 中完成所有操作
    with get_db() as db:
        # 从数据库查询商品信息
        product = db.query(BenefitProductORM).filter(
            BenefitProductORM.store_product_id == store_product_id
        ).first()

        if product:
            product_type = product.product_type
            price_usd = product.price_usd
            credits_amount = product.credits_amount or 0

            if product_type == "subscribe":
                if product.period == "weekly":
                    plan_type = "weekly"
                    duration_days = 7
                elif product.period == "monthly":
                    plan_type = "monthly"
                    duration_days = 30
                elif product.period == "yearly":
                    plan_type = "yearly"
                    duration_days = 365
            else:
                package_key = store_product_id
        else:
            # 硬编码逻辑
            if store_product_id:
                if "weekly" in store_product_id.lower() or "week" in store_product_id.lower():
                    product_type = "subscribe"
                    plan_type = "weekly"
                    duration_days = 7
                elif "monthly" in store_product_id.lower() or "month" in store_product_id.lower():
                    product_type = "subscribe"
                    plan_type = "monthly"
                    duration_days = 30
                elif "yearly" in store_product_id.lower() or "year" in store_product_id.lower():
                    product_type = "subscribe"
                    plan_type = "yearly"
                    duration_days = 365
                else:
                    product_type = "point"
                    package_key = store_product_id

        existing = db.query(PendingOrderORM).filter(
            PendingOrderORM.original_transaction_id == purchase_token
        ).first()
        payer_id = _resolve_google_pending_payer(
            db,
            purchase_token=purchase_token,
            order_id=str((vr or {}).get("order_id") or ""),
            vr=vr,
        )
        already_paid = bool(payer_id and _lookup_google_paid_user_id(
            db, purchase_token, (vr or {}).get("order_id")
        ))

        if existing:
            _bind_google_pending_payer(existing, payer_id, completed=already_paid)
            db.commit()
            logger.info(
                "[GOOGLE_CALLBACK] 预订单已存在: %s user_id=%s completed=%s",
                existing.transaction_id,
                existing.user_id,
                already_paid,
            )
            return

        pending_order = PendingOrderORM(
            transaction_id=purchase_token,
            original_transaction_id=purchase_token,
            product_id=store_product_id,
            product_type=product_type,
            plan_type=plan_type,
            package_key=package_key,
            price_usd=price_usd,
            credits_amount=credits_amount,
            duration_days=duration_days,
            store_type="google_play",
            status="completed" if already_paid else "pending",
            user_id=payer_id,
            callback_data=json.dumps(notification, ensure_ascii=False, default=str),
        )
        db.add(pending_order)
        db.commit()

        logger.info(
            "[GOOGLE_CALLBACK] 预订单创建成功: ID=%s user_id=%s status=%s",
            pending_order.id,
            pending_order.user_id,
            pending_order.status,
        )


def _handle_google_renewed(notification: dict):
    """处理 Google 续费/新订阅（幂等：新到期时间不晚于当前则跳过）"""
    from core.database import UserCreditORM, VipSubscriptionORM

    purchase_token = notification.get("purchase_token", "")
    subscription_id = notification.get("subscription_id", "")

    with get_db() as db:
        sub = db.query(VipSubscriptionORM).filter(
            VipSubscriptionORM.store_transaction_id == purchase_token
        ).first()
        if not sub:
            logger.info(
                "[payment] Google 续费通知未找到订阅，改为创建预订单 token_len=%s",
                len(purchase_token or ""),
            )
            _handle_google_subscribed(notification)
            return

        # 通过 Google API 查询实际到期时间
        if subscription_id and purchase_token:
            verify_result = verify_google_purchase(subscription_id, purchase_token, is_subscription=True)
            expiry_ms = verify_result.get("expiry_time_millis", "")
            if expiry_ms:
                try:
                    new_expire = datetime.fromtimestamp(int(expiry_ms) / 1000, tz=timezone.utc)

                    # 幂等检查：新到期时间不晚于当前存储的到期时间，说明已处理过
                    current_expire = sub.expire_at
                    if current_expire and current_expire.tzinfo is None:
                        current_expire = current_expire.replace(tzinfo=timezone.utc)
                    if current_expire and new_expire <= current_expire:
                        logger.info("[payment] Google 续费通知已处理过，跳过 token_len=%s", len(purchase_token or ""))
                        return

                    sub.expire_at = new_expire
                    sub.status = "active"
                    sub.auto_renew = True

                    # 同步到 user_credits
                    uc = db.query(UserCreditORM).filter(UserCreditORM.user_id == sub.user_id).first()
                    if uc:
                        uc.vip_expire_at = new_expire
                        uc.vip_type = sub.plan_type

                    db.commit()
                    logger.info("[payment] Google 续费处理完成: user=%s, 新到期=%s",
                                sub.user_id, new_expire.isoformat())
                    return
                except Exception as e:
                    logger.error("[payment] Google 续费到期时间解析失败: %s", e)

        # API 查询失败时，仅标记为 active（到期时间由 daily_scheduler 兜底）
        sub.status = "active"
        sub.auto_renew = True
        db.commit()
        logger.info("[payment] Google 续费处理（未获取到期时间）: user=%s", sub.user_id)


def _handle_google_auto_renew_off(notification: dict):
    """用户取消自动续订：到期前仍可使用，不清 VIP。"""
    from core.database import VipSubscriptionORM

    purchase_token = notification.get("purchase_token", "")
    with get_db() as db:
        sub = db.query(VipSubscriptionORM).filter(
            VipSubscriptionORM.store_transaction_id == purchase_token
        ).first()
        if not sub:
            logger.info("[payment] Google 取消续订未找到订阅 token_len=%s", len(purchase_token or ""))
            return
        sub.auto_renew = False
        db.commit()
        logger.info("[payment] Google 已关闭自动续订: user=%s", sub.user_id)


def _handle_google_expired(notification: dict):
    """处理 Google 过期/取消（幂等：已过期则跳过）"""
    from core.database import UserCreditORM, VipSubscriptionORM

    purchase_token = notification.get("purchase_token", "")
    with get_db() as db:
        sub = db.query(VipSubscriptionORM).filter(
            VipSubscriptionORM.store_transaction_id == purchase_token
        ).first()
        if not sub:
            logger.warning("[payment] Google 过期通知未找到订阅 token_len=%s", len(purchase_token or ""))
            return

        if sub.status == "expired":
            logger.info("[payment] Google 过期通知已处理过，跳过 token_len=%s", len(purchase_token or ""))
            return

        sub.status = "expired"
        sub.auto_renew = False

        # 检查是否还有其他有效订阅
        other_active = db.query(VipSubscriptionORM).filter(
            VipSubscriptionORM.user_id == sub.user_id,
            VipSubscriptionORM.status == "active",
            VipSubscriptionORM.id != sub.id,
        ).first()
        if not other_active:
            uc = db.query(UserCreditORM).filter(UserCreditORM.user_id == sub.user_id).first()
            if uc:
                uc.vip_type = ""
                uc.daily_gift_total = 0
                uc.daily_gift_remaining = 0
                uc.max_agents = 0

        db.commit()
        logger.info("[payment] Google 过期处理: user=%s", sub.user_id)


def _handle_google_paused(notification: dict):
    """处理 Google 暂停"""
    from core.database import VipSubscriptionORM

    purchase_token = notification.get("purchase_token", "")
    with get_db() as db:
        sub = db.query(VipSubscriptionORM).filter(
            VipSubscriptionORM.store_transaction_id == purchase_token
        ).first()
        if sub:
            sub.auto_renew = False
            db.commit()
            logger.info("[payment] Google 暂停处理: user=%s", sub.user_id)
