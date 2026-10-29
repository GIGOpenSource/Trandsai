"""
积分 & VIP 核心服务

职责：
- 用户积分余额查询 / 扣费 / 赠送
- VIP 激活 / 续费 / 过期处理
- 充值包到账
- 免费轮次管理
- 智能体创建权限判断
- 消费账单写入
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core.database import (
    ConfigGroupORM,
    CreditTransactionORM,
    RechargeRecordORM,
    UserCompanionStateORM,
    UserCreditORM,
    VipSubscriptionORM,
    get_db,
)
from core.i18n import vip_plan_display_name

logger = logging.getLogger(__name__)

# 登录补发时，最多回溯多少天的遗漏日赠（防久未登录一次刷爆）
_MAX_DAILY_GIFT_CATCHUP_DAYS = 30

# ===== 默认配置（数据库读不到时的兜底） =====
_DEFAULT_VIP_PLANS = {
    "weekly":   {"daily_credits": 24, "max_agents": 1, "duration_days": 7},
    "monthly":  {"daily_credits": 50, "max_agents": 3, "duration_days": 30},
    "yearly":   {"daily_credits": 25, "max_agents": 5, "duration_days": 365},
    "lifetime": {"daily_credits": 20, "max_agents": 2, "duration_days": 36500},
}

_DEFAULT_RECHARGE_PACKAGES = {
    "pack_250":  {"credits": 250},
    "pack_550":  {"credits": 550},
    "pack_800":  {"credits": 800},
    "pack_1900": {"credits": 1900},
}

_DEFAULT_COSTS = {
    "chat_cost": 3,
    "create_agent_cost": 50,
    "agent_daily_cost": 10,
    "free_turns_limit": 3,
}

# 进程内缓存：启动时 load 一次；管理端改 config-groups 后 refresh，避免每条消息查 SQL
_COST_CONFIG_CACHE: dict = dict(_DEFAULT_COSTS)


def _parse_cost_value(key: str, raw) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_COSTS[key]
    if key == "free_turns_limit":
        return max(0, min(v, 9999))
    return max(0, v)


def refresh_cost_config_cache() -> dict:
    """从 config_groups 加载扣费/免费次数配置到内存（启动与管理端保存后调用）。"""
    global _COST_CONFIG_CACHE
    merged = dict(_DEFAULT_COSTS)
    try:
        with get_db() as db:
            rows = (
                db.query(ConfigGroupORM)
                .filter(
                    ConfigGroupORM.key.in_(("system_costs", "free_turns_limit", "vip_plans")),
                    ConfigGroupORM.enabled == 1,
                )
                .all()
            )
            by_key = {r.key: (r.config_json or {}) for r in rows}
            # 优先级从低到高：vip_plans(旧) → system_costs → free_turns_limit(专用)
            vip_cfg = by_key.get("vip_plans") or {}
            for k in _DEFAULT_COSTS:
                if k in vip_cfg and vip_cfg[k] is not None:
                    merged[k] = _parse_cost_value(k, vip_cfg[k])
            sys_cfg = by_key.get("system_costs") or {}
            for k in _DEFAULT_COSTS:
                if k in sys_cfg and sys_cfg[k] is not None:
                    merged[k] = _parse_cost_value(k, sys_cfg[k])
            ft_cfg = by_key.get("free_turns_limit") or {}
            if ft_cfg.get("free_turns_limit") is not None:
                merged["free_turns_limit"] = _parse_cost_value(
                    "free_turns_limit", ft_cfg["free_turns_limit"]
                )
    except Exception as e:
        logger.warning("[credit_service] refresh_cost_config_cache 失败，使用默认值: %s", e)
    _COST_CONFIG_CACHE = merged
    logger.info("[credit_service] 扣费配置已加载: %s", merged)
    return dict(_COST_CONFIG_CACHE)


# ===== 配置读取 =====

def _load_vip_config() -> dict:
    """从 config_groups 表读取 vip_plans（VIP 档位/充值包）；扣费字段走内存缓存。"""
    fallback = {
        "plans": _DEFAULT_VIP_PLANS,
        "recharge_packages": _DEFAULT_RECHARGE_PACKAGES,
    }
    try:
        with get_db() as db:
            row = db.query(ConfigGroupORM).filter(ConfigGroupORM.key == "vip_plans").first()
            if row and row.config_json:
                cfg = dict(row.config_json)
                cfg.setdefault("plans", _DEFAULT_VIP_PLANS)
                cfg.setdefault("recharge_packages", _DEFAULT_RECHARGE_PACKAGES)
                return cfg
    except Exception as e:
        logger.warning("[credit_service] 读取 vip_plans 配置失败: %s", e)
    return fallback


def get_vip_plans() -> dict:
    """返回 VIP 档位配置（不含价格，价格由前端从商店获取）"""
    cfg = _load_vip_config()
    plans = cfg.get("plans", _DEFAULT_VIP_PLANS)
    result = {}
    for key, plan in plans.items():
        result[key] = {
            "daily_credits": plan.get("daily_credits", 0),
            "max_agents": plan.get("max_agents", 0),
            "duration_days": plan.get("duration_days", 0),
        }
    return result


def get_recharge_packages() -> dict:
    """返回充值包配置（不含价格）"""
    cfg = _load_vip_config()
    return cfg.get("recharge_packages", _DEFAULT_RECHARGE_PACKAGES)


def get_cost_config() -> dict:
    """返回扣费规则配置（读内存缓存，不查 SQL）。"""
    return dict(_COST_CONFIG_CACHE)


# ===== 用户积分记录 =====

def get_or_create_user_credits(db: Session, user_id: int) -> UserCreditORM:
    """获取用户积分记录，不存在则创建默认记录"""
    uc = db.query(UserCreditORM).filter(UserCreditORM.user_id == user_id).first()
    if not uc:
        uc = UserCreditORM(
            user_id=user_id,
            balance=0,
            daily_gift_remaining=0,
            daily_gift_total=0,
            vip_type="",
            free_turns_used=0,
            max_agents=0,
            last_daily_gift_date="",
        )
        db.add(uc)
        db.flush()
    return uc


def touch_user_client_context(
    db: Session,
    user_id: int,
    *,
    ip: Optional[str] = None,
    device: Optional[str] = None,
) -> None:
    """将客户端 IP / 设备写入 user_credits；有值才更新，缺省则跳过。"""
    ip = (ip or "").strip()
    device = (device or "").strip()
    if not ip and not device:
        return

    uc = get_or_create_user_credits(db, user_id)
    changed = False
    if ip and hasattr(uc, "last_ip"):
        uc.last_ip = ip[:64]
        changed = True
    if device and hasattr(uc, "last_device"):
        uc.last_device = device[:512]
        changed = True
    if changed:
        db.flush()


def _parse_gift_date(value: str) -> Optional[date]:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def refresh_daily_gifts(db: Session, user_id: int) -> UserCreditORM:
    """
    每日赠送刷新（登录 / 查余额 / 发消息时调用）：
    - 有效 VIP 且今日未领：发放当日赠送到 daily_gift_remaining
    - 若中间漏了整天：按 daily_gift_total 补发到 balance（最多回溯 30 天）
    """
    uc = get_or_create_user_credits(db, user_id)
    today = datetime.now(timezone.utc).date()
    today_str = today.strftime("%Y-%m-%d")

    # 非VIP或已过期，不赠送
    if not is_vip_active(uc):
        # 过期清零额度（lifetime 永远不会进这里）
        if uc.vip_type and uc.vip_type != "lifetime":
            expire_at = uc.vip_expire_at
            if expire_at:
                if expire_at.tzinfo is None:
                    expire_at = expire_at.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > expire_at:
                    uc.vip_type = ""
                    uc.daily_gift_total = 0
                    uc.daily_gift_remaining = 0
                    uc.max_agents = 0
        return uc

    gift_amount = int(uc.daily_gift_total or 0)
    if gift_amount <= 0:
        return uc

    # 今天已经领过了
    if (uc.last_daily_gift_date or "") == today_str:
        return uc

    last = _parse_gift_date(uc.last_daily_gift_date or "")
    # 漏发的完整自然日 → 打进充值余额
    if last and last < today:
        missed_days = (today - last).days - 1
        if missed_days > 0:
            missed_days = min(missed_days, _MAX_DAILY_GIFT_CATCHUP_DAYS)
            catchup = missed_days * gift_amount
            uc.balance = int(uc.balance or 0) + catchup
            _write_vip_gift_recharge(
                db, user_id, catchup,
                package_key=f"vip_gift_catchup_{uc.vip_type or 'vip'}",
                tx_suffix=f"catchup_{today_str}",
            )
            _write_transaction(
                db, user_id, "vip_gift", catchup, get_effective_balance(uc),
                vip_type=uc.vip_type or "",
                description=f"补发VIP赠送 {missed_days}天共{catchup}积分",
            )
            logger.info(
                "[credit_service] 用户 %s 补发 VIP 日赠 %d 天共 %d 积分",
                user_id, missed_days, catchup,
            )

    # 发放今日赠送（重置当日额度）
    uc.daily_gift_remaining = gift_amount
    uc.last_daily_gift_date = today_str
    _write_vip_gift_recharge(
        db, user_id, gift_amount,
        package_key=f"vip_gift_daily_{uc.vip_type or 'vip'}",
        tx_suffix=f"daily_{today_str}",
    )
    _write_transaction(
        db, user_id, "vip_gift", gift_amount, get_effective_balance(uc),
        vip_type=uc.vip_type or "",
        description=f"每日VIP赠送 {gift_amount} 积分",
    )
    logger.info("[credit_service] 用户 %s 每日赠送 %d 积分", user_id, gift_amount)
    db.flush()
    return uc


def _write_vip_gift_recharge(
    db: Session,
    user_id: int,
    credits: int,
    *,
    package_key: str,
    tx_suffix: str,
) -> None:
    """日赠 / 补发写入赠送充值记录，便于「赠送记录」页查询。"""
    if credits <= 0:
        return
    tid = f"vip_gift_{user_id}_{tx_suffix}"[:128]
    exists = (
        db.query(RechargeRecordORM.id)
        .filter(RechargeRecordORM.store_transaction_id == tid)
        .first()
    )
    if exists:
        return
    db.add(
        RechargeRecordORM(
            user_id=user_id,
            package_key=(package_key or "vip_gift")[:30],
            credits=int(credits),
            price_usd=0,
            store_type="system",
            store_transaction_id=tid,
            store_receipt="",
        )
    )


# ===== 余额查询 =====

def is_vip_active(uc: Optional[UserCreditORM]) -> bool:
    """
    是否有效 VIP。终身与订阅在程序身份上等价：
    - lifetime：永不过期（不依赖 vip_expire_at）
    - 其它档位：未过期即有效
    权限/创建/聊天/赠送一律走此函数，禁止再散落「必须有 expire_at」的判断。
    """
    if not uc or not uc.vip_type:
        return False
    if uc.vip_type == "lifetime":
        return True
    if not uc.vip_expire_at:
        return False
    expire_at = uc.vip_expire_at
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < expire_at


def get_effective_balance(uc: UserCreditORM) -> int:
    """返回有效总余额 = 每日赠送剩余 + 充值余额"""
    return max(0, (uc.daily_gift_remaining or 0) + (uc.balance or 0))


def get_user_vip_status(
    db: Session, user_id: int, companion_id: Optional[str] = None
) -> dict:
    """返回用户完整的VIP和积分状态。

    companion_id 仅回传会话 id；free_turns_* 为该账号注册以来的总免费次数。
    """
    uc = refresh_daily_gifts(db, user_id)

    is_vip = is_vip_active(uc)
    vip_type = (uc.vip_type or "") if is_vip else ""
    # 终身不对外暴露「假到期日」，展示与订阅一致靠 is_vip，文案侧用永久有效
    vip_expire_str = None
    if is_vip and vip_type != "lifetime" and uc.vip_expire_at:
        expire_at = uc.vip_expire_at
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        vip_expire_str = expire_at.isoformat()

    cid = _normalize_companion_id(companion_id)
    snap = get_user_free_turns_snapshot(db, user_id)
    limit = snap["free_turns_limit"]
    free_used = snap["free_turns_used"]
    free_remaining = snap["free_turns_remaining"]
    out = {
        "is_vip": is_vip,
        "vip_type": vip_type,
        "vip_name": vip_plan_display_name(vip_type) if vip_type else "",
        "vip_expire_at": vip_expire_str,
        "balance": uc.balance or 0,
        "daily_gift_remaining": uc.daily_gift_remaining or 0,
        "daily_gift_total": uc.daily_gift_total or 0,
        "effective_balance": get_effective_balance(uc),
        "free_turns_used": free_used,
        "free_turns_limit": limit,
        "free_turns_remaining": free_remaining,
        "max_agents": uc.max_agents or 0,
        "create_agent_cost": get_cost_config()["create_agent_cost"],
    }
    if cid:
        out["companion_id"] = cid
        out["remaining"] = free_remaining
    return out


# ===== 账单写入 =====

def _write_transaction(db: Session, user_id: int, event_type: str, amount: int,
                       balance_after: int, vip_type: str = "", description: str = ""):
    """写入积分消费账单"""
    tx = CreditTransactionORM(
        user_id=user_id,
        event_type=event_type,
        amount=amount,
        balance_after=balance_after,
        vip_type=vip_type,
        description=description,
    )
    db.add(tx)


# ===== 免费轮次（账号注册以来累计，不按智能体拆分） =====

def _normalize_companion_id(companion_id: Optional[str]) -> str:
    return (companion_id or "").strip()[:8]


def get_or_create_user_companion_state(
    db: Session, user_id: int, companion_id: str
) -> UserCompanionStateORM:
    """获取/创建用户-智能体状态行（含 free_turns_used）。"""
    cid = _normalize_companion_id(companion_id)
    if not cid:
        raise ValueError("companion_id required")
    row = (
        db.query(UserCompanionStateORM)
        .filter(
            UserCompanionStateORM.user_id == int(user_id),
            UserCompanionStateORM.companion_id == cid,
        )
        .first()
    )
    if not row:
        row = UserCompanionStateORM(
            user_id=int(user_id),
            companion_id=cid,
            affection=0,
            turns=0,
            free_turns_used=0,
        )
        db.add(row)
        db.flush()
    return row


def _legacy_companion_free_turns_sum(db: Session, user_id: int) -> int:
    """旧版按智能体计数的合计，用于迁到账号累计。"""
    summed = (
        db.query(func.coalesce(func.sum(UserCompanionStateORM.free_turns_used), 0))
        .filter(UserCompanionStateORM.user_id == int(user_id))
        .scalar()
    )
    return max(0, int(summed or 0))


def _account_free_turns_used(db: Session, user_id: int) -> int:
    """账号已用免费次数；若 user_credits 落后于旧分表合计则抬升。"""
    uc = get_or_create_user_credits(db, user_id)
    stored = max(0, int(uc.free_turns_used or 0))
    summed = _legacy_companion_free_turns_sum(db, user_id)
    used = max(stored, summed)
    if used != stored:
        uc.free_turns_used = used
        db.flush()
    return used


def get_user_free_turns_snapshot(db: Session, user_id: int) -> dict:
    """该账号注册以来的免费次数快照：总额（配置）/ 已用 / 剩余。"""
    limit = max(0, int(get_cost_config().get("free_turns_limit") or 0))
    used = _account_free_turns_used(db, user_id)
    return {
        "free_turns_limit": limit,
        "free_turns_used": used,
        "free_turns_remaining": max(0, limit - used),
        "report_free_quota_reached": False,
    }


def get_companion_free_turns_snapshot(
    db: Session, user_id: int, companion_id: str
) -> dict:
    """兼容旧名：次数按账号计，companion_id 忽略。"""
    return get_user_free_turns_snapshot(db, user_id)


def free_turn_used_payload(
    snap: dict,
    *,
    report_free_quota_reached: Optional[bool] = None,
    say_hi_snap: Optional[dict] = None,
) -> dict:
    """WS `free_turn_used` 帧。report_free_quota_reached 仅在本轮刚打满配置额度时为 true。"""
    limit = int(snap.get("free_turns_limit") or 0)
    used = int(snap.get("free_turns_used") or 0)
    remaining = int(snap.get("free_turns_remaining") or max(0, limit - used))
    report = snap.get("report_free_quota_reached")
    if report_free_quota_reached is not None:
        report = report_free_quota_reached
    out = {
        "type": "free_turn_used",
        "remaining": remaining,
        "free_turns_limit": limit,
        "free_turns_used": used,
        "report_free_quota_reached": bool(report),
    }
    if say_hi_snap:
        out["say_hi_daily_remaining"] = int(say_hi_snap.get("say_hi_daily_remaining") or 0)
        out["say_hi_daily_exhausted"] = bool(say_hi_snap.get("say_hi_daily_exhausted"))
        out["say_hi_daily_limit"] = int(say_hi_snap.get("say_hi_daily_limit") or 0)
    return out


def get_companion_free_turns_remaining(
    db: Session, user_id: int, companion_id: str
) -> int:
    """该账号剩余免费次数（companion_id 仅兼容旧调用）。"""
    return int(get_user_free_turns_snapshot(db, user_id)["free_turns_remaining"])


def get_chat_free_turns_status(user_id: int, companion_id: str) -> dict:
    """进房剩余次数：不依赖 WS。次数为账号累计，与当前智能体无关。"""
    cid = _normalize_companion_id(companion_id)
    with get_db() as db:
        snap = get_user_free_turns_snapshot(db, user_id)
        uc = db.query(UserCreditORM).filter(UserCreditORM.user_id == int(user_id)).first()
        is_vip = bool(is_vip_active(uc)) if uc else False
    say_hi: dict = {}
    try:
        from services.say_hi_quota import get_say_hi_daily_snapshot

        say_hi = get_say_hi_daily_snapshot(int(user_id))
    except Exception:
        logger.exception("[credit_service] say_hi snapshot failed user=%s", user_id)
    remaining = int(snap["free_turns_remaining"])
    return {
        "companion_id": cid,
        "remaining": remaining,
        "free_turns_remaining": remaining,
        "free_turns_limit": int(snap["free_turns_limit"]),
        "free_turns_used": int(snap["free_turns_used"]),
        "is_vip": is_vip,
        "say_hi_daily_remaining": int(say_hi.get("say_hi_daily_remaining") or 0),
        "say_hi_daily_exhausted": bool(say_hi.get("say_hi_daily_exhausted")),
        "say_hi_daily_limit": int(say_hi.get("say_hi_daily_limit") or 0),
    }


def check_free_turn(
    db: Session, user_id: int, companion_id: Optional[str] = None
) -> bool:
    """检查账号是否还有免费轮次。companion_id 忽略。"""
    snap = get_user_free_turns_snapshot(db, user_id)
    return int(snap["free_turns_remaining"]) > 0


def consume_free_turn(
    db: Session, user_id: int, companion_id: Optional[str] = None
) -> dict:
    """消耗一次账号免费轮次。companion_id 只用于账单/触达，不拆分额度。"""
    cid = _normalize_companion_id(companion_id)

    uc = get_or_create_user_credits(db, user_id)
    limit = max(0, int(get_cost_config()["free_turns_limit"] or 0))
    used = _account_free_turns_used(db, user_id)
    if used >= limit:
        return {"ok": False, "reason": "no_free_turns"}

    uc.free_turns_used = used + 1
    remaining = limit - uc.free_turns_used
    report_reached = bool(limit > 0 and used < limit and uc.free_turns_used >= limit)
    desc = f"免费轮次 (剩余{remaining})"
    if cid:
        desc = f"免费轮次 companion={cid} (剩余{remaining})"
    _write_transaction(
        db, user_id, "free_turn", 0,
        get_effective_balance(uc),
        vip_type=uc.vip_type or "",
        description=desc,
    )
    result = {
        "ok": True,
        "free_turns_remaining": remaining,
        "free_turns_limit": limit,
        "free_turns_used": int(uc.free_turns_used or 0),
        "companion_id": cid,
        "report_free_quota_reached": report_reached,
    }
    if remaining <= 0 and cid:
        try:
            from services.outreach import schedule_quota_exhaust_outreach

            schedule_quota_exhaust_outreach(user_id=user_id, companion_id=cid)
        except Exception as e:
            logger.warning("[credit_service] quota exhaust outreach failed: %s", e)
    return result


# ===== 积分扣费 =====

def consume_credits(db: Session, user_id: int, amount: int, event_type: str,
                    description: str = "") -> dict:
    """
    扣除积分：先扣 daily_gift_remaining，再扣 balance。
    amount > 0 表示消耗（正数传入，内部取负）。
    返回 {"ok": True/False, "balance": ..., "reason": ...}
    """
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}

    uc = get_or_create_user_credits(db, user_id)
    effective = get_effective_balance(uc)
    if effective < amount:
        return {
            "ok": False,
            "reason": "insufficient",
            "balance": effective,
            "daily_gift_remaining": uc.daily_gift_remaining or 0,
            "recharge_balance": uc.balance or 0,
        }

    remaining = amount
    # 先扣每日赠送
    daily_use = min(remaining, uc.daily_gift_remaining or 0)
    if daily_use > 0:
        uc.daily_gift_remaining = (uc.daily_gift_remaining or 0) - daily_use
        remaining -= daily_use

    # 再扣充值余额
    if remaining > 0:
        uc.balance = (uc.balance or 0) - remaining

    new_balance = get_effective_balance(uc)
    _write_transaction(
        db, user_id, event_type, -amount, new_balance,
        vip_type=uc.vip_type or "",
        description=description,
    )
    return {"ok": True, "balance": new_balance}


def refund_credits(db: Session, user_id: int, amount: int, event_type: str = "chat_refund",
                   description: str = "") -> dict:
    """退回积分到充值余额（不还原每日赠送拆分）。"""
    if amount <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    uc = get_or_create_user_credits(db, user_id)
    uc.balance = (uc.balance or 0) + amount
    new_balance = get_effective_balance(uc)
    _write_transaction(
        db, user_id, event_type, amount, new_balance,
        vip_type=uc.vip_type or "",
        description=description or f"退回 {amount} 积分",
    )
    return {"ok": True, "balance": new_balance}


def restore_free_turn(
    db: Session, user_id: int, companion_id: Optional[str] = None
) -> dict:
    """退回一次已消耗的账号免费轮次。"""
    cid = _normalize_companion_id(companion_id)
    uc = get_or_create_user_credits(db, user_id)
    used = _account_free_turns_used(db, user_id)
    if used <= 0:
        return {"ok": False, "reason": "none_used"}
    uc.free_turns_used = used - 1
    limit = max(0, int(get_cost_config()["free_turns_limit"] or 0))
    remaining = limit - uc.free_turns_used
    desc = f"免费轮次退回 (剩余{remaining})"
    if cid:
        desc = f"免费轮次退回 companion={cid} (剩余{remaining})"
    _write_transaction(
        db, user_id, "free_turn_refund", 0,
        get_effective_balance(uc),
        vip_type=uc.vip_type or "",
        description=desc,
    )
    return {"ok": True, "free_turns_remaining": remaining, "companion_id": cid}


# ===== VIP 激活 =====

# VIP 档位默认天数（fallback）
_DEFAULT_VIP_DURATION = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "yearly": 365,
    "lifetime": 36500,
}


_VIP_PLAN_RANK = {
    "daily": 1,
    "weekly": 2,
    "monthly": 3,
    "yearly": 4,
    "lifetime": 5,
}


def _resolve_vip_product_config(db: Session, plan_type: str, product_id: str = "") -> tuple:
    """返回 (daily_credits, max_agents, duration_days, product_or_none)。"""
    from core.database import BenefitProductORM

    product = None
    if product_id:
        product = db.query(BenefitProductORM).filter(
            BenefitProductORM.store_product_id == product_id,
            BenefitProductORM.product_type == "subscribe",
            BenefitProductORM.is_active == True,
        ).first()
        if not product:
            product = db.query(BenefitProductORM).filter(
                BenefitProductORM.apple_product_id == product_id,
                BenefitProductORM.product_type == "subscribe",
                BenefitProductORM.is_active == True,
            ).first()

    if not product and plan_type:
        product = db.query(BenefitProductORM).filter(
            BenefitProductORM.period == plan_type,
            BenefitProductORM.product_type == "subscribe",
            BenefitProductORM.is_active == True,
        ).first()

    period = (getattr(product, "period", None) or plan_type or "").strip()
    duration_days = _DEFAULT_VIP_DURATION.get(period, _DEFAULT_VIP_DURATION.get(plan_type, 30))

    if product:
        daily_credits = int(product.daily_gift_credits or 0)
        max_agents = int(product.max_agents or 0)
        logger.info(
            "[credit_service] 从 benefit_products 读取配置: period=%s daily_gift=%d max_agents=%d",
            period, daily_credits, max_agents,
        )
        return daily_credits, max_agents, duration_days, product

    plans = _load_vip_config().get("plans", _DEFAULT_VIP_PLANS)
    plan = plans.get(plan_type, {})
    daily_credits = int(plan.get("daily_credits", 0))
    max_agents = int(plan.get("max_agents", 0))
    logger.info(
        "[credit_service] 从配置表读取: plan=%s daily_gift=%d max_agents=%d",
        plan_type, daily_credits, max_agents,
    )
    return daily_credits, max_agents, duration_days, None


def _stack_vip_expire(current_expire, now: datetime, duration_days: int) -> datetime:
    """未过期则在当前到期日叠加；已过期/无到期日则从 now 起算。"""
    if current_expire:
        if getattr(current_expire, "tzinfo", None) is None:
            current_expire = current_expire.replace(tzinfo=timezone.utc)
        if now < current_expire:
            return current_expire + timedelta(days=duration_days)
    return now + timedelta(days=duration_days)


def _pick_vip_plan_type(current: str, incoming: str) -> str:
    """取档位更高的 VIP 类型；同档保留新购类型。"""
    cur = (current or "").strip()
    inc = (incoming or "").strip()
    if not cur:
        return inc
    if not inc:
        return cur
    if _VIP_PLAN_RANK.get(inc, 0) >= _VIP_PLAN_RANK.get(cur, 0):
        return inc
    return cur


def activate_vip(db: Session, user_id: int, plan_type: str, store_type: str,
                 store_transaction_id: str, store_receipt: str = "",
                 price_usd: float = 0, product_id: str = "",
                 purchased_at: Optional[datetime] = None,
                 expire_at: Optional[datetime] = None) -> dict:
    """
    激活/续费VIP。
    去重：同一 store_transaction_id 不重复入账；但同 token 换档升级仍会续期/提额。

    优先从 benefit_products 表读取 daily_gift_credits 和 max_agents 配置。
    max_agents 取「当前已有」与「本次商品」的较大值。
    """
    plan_type = (plan_type or "").strip()
    daily_credits, max_agents, duration_days, _product = _resolve_vip_product_config(
        db, plan_type, product_id=product_id,
    )

    now = purchased_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if expire_at is not None and expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    uc = get_or_create_user_credits(db, user_id)

    existing = db.query(VipSubscriptionORM).filter(
        VipSubscriptionORM.store_transaction_id == store_transaction_id
    ).first()
    if existing:
        # 同交易号：通常是商店订阅组内升级（周→年）或前端重复校验。
        # 换档时补一次续期；同档仅幂等提额，避免重复叠加天数。
        upgraded = bool(plan_type) and (existing.plan_type or "") != plan_type
        if upgraded:
            prev_plan = existing.plan_type or ""
            new_expire = expire_at or _stack_vip_expire(
                uc.vip_expire_at or existing.expire_at, now, duration_days
            )
            existing.plan_type = plan_type
            existing.expire_at = new_expire
            existing.status = "active"
            existing.auto_renew = True
            if max_agents:
                existing.max_agents = max(int(existing.max_agents or 0), max_agents)
            if daily_credits:
                existing.daily_credits = daily_credits

            uc.vip_type = _pick_vip_plan_type(uc.vip_type or "", plan_type)
            uc.vip_expire_at = new_expire
            uc.vip_started_at = uc.vip_started_at or now
            if daily_credits:
                uc.daily_gift_total = daily_credits
                uc.daily_gift_remaining = daily_credits
                uc.last_daily_gift_date = now.strftime("%Y-%m-%d")
            uc.max_agents = max(int(uc.max_agents or 0), int(max_agents or 0))
            db.flush()
            logger.info(
                "[credit_service] VIP 同交易升级: user=%s %s→%s expire=%s max_agents=%s",
                user_id, prev_plan, plan_type, new_expire.isoformat(), uc.max_agents,
            )
            return {
                "ok": True,
                "reason": "upgraded",
                "subscription_id": existing.id,
                "vip_expire_at": new_expire.isoformat(),
            }

        # 同档重复：只保证额度取最大，不重复加天数
        before_agents = int(uc.max_agents or 0)
        uc.max_agents = max(before_agents, int(max_agents or 0))
        if max_agents:
            existing.max_agents = max(int(existing.max_agents or 0), int(max_agents or 0))
        existing.status = "active"
        db.flush()
        expire_str = None
        if uc.vip_expire_at:
            exp = uc.vip_expire_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            expire_str = exp.isoformat()
        logger.info(
            "[credit_service] VIP 交易已存在，跳过续期: %s max_agents %s→%s",
            store_transaction_id, before_agents, uc.max_agents,
        )
        return {
            "ok": True,
            "reason": "already_processed",
            "subscription_id": existing.id,
            "vip_expire_at": expire_str,
        }

    new_expire = expire_at or _stack_vip_expire(uc.vip_expire_at, now, duration_days)

    # 更新用户积分状态：档位取更高，额度取最大
    uc.vip_type = _pick_vip_plan_type(uc.vip_type or "", plan_type)
    uc.vip_expire_at = new_expire
    uc.vip_started_at = uc.vip_started_at or now
    uc.daily_gift_total = daily_credits
    uc.daily_gift_remaining = daily_credits  # 立即发放当日赠送
    uc.last_daily_gift_date = now.strftime("%Y-%m-%d")
    uc.max_agents = max(int(uc.max_agents or 0), int(max_agents or 0))

    # 写入订阅记录
    sub = VipSubscriptionORM(
        user_id=user_id,
        plan_type=plan_type,
        price_usd=price_usd,
        store_type=store_type,
        store_transaction_id=store_transaction_id,
        store_receipt=store_receipt,
        daily_credits=daily_credits,
        max_agents=max_agents,
        start_at=now,
        expire_at=new_expire,
        auto_renew=True,
        status="active",
    )
    db.add(sub)
    db.flush()

    # 写入充值记录（VIP 赠送金币）
    if daily_credits > 0:
        gift_record = RechargeRecordORM(
            user_id=user_id,
            package_key=f"vip_gift_{plan_type}",
            credits=daily_credits,
            price_usd=0,
            store_type=store_type,
            store_transaction_id=f"vip_gift_{store_transaction_id}",
            store_receipt="",
        )
        db.add(gift_record)

    # 写入账单
    new_balance = get_effective_balance(uc)
    _write_transaction(
        db, user_id, "vip_subscribe", daily_credits, new_balance,
        vip_type=plan_type,
        description=f"开通VIP {plan_type}，赠送{daily_credits}积分",
    )

    logger.info(
        "[credit_service] 用户 %s 激活VIP %s，到期 %s，max_agents=%s",
        user_id, plan_type, new_expire.isoformat(), uc.max_agents,
    )
    try:
        from services.limited_offer import mark_limited_offer_used

        mark_limited_offer_used(db, user_id)
    except Exception:
        logger.exception("[credit_service] mark limited offer used after vip failed")
    try:
        from services.outreach import schedule_post_pay_recall

        schedule_post_pay_recall(user_id=user_id)
    except Exception as e:
        logger.warning("[credit_service] post_pay recall schedule failed: %s", e)
    return {"ok": True, "subscription_id": sub.id, "vip_expire_at": new_expire.isoformat()}


# ===== 充值 =====

def _user_app_key(db: Session, user_id: int) -> str:
    try:
        from core.app_key import normalize_app_key
        from core.database import UserORM

        user = db.query(UserORM).filter(UserORM.id == int(user_id)).first()
        return normalize_app_key(getattr(user, "app_key", None) if user else "")
    except Exception:
        return ""


def _lookup_point_product(db: Session, package_key: str, app_key: str = ""):
    """金币商品：按 Google/Apple 商品 ID 匹配 benefit_products（product_type=point）。"""
    from core.app_key import normalize_app_key
    from core.database import BenefitProductORM

    key = (package_key or "").strip()
    if not key:
        return None
    rows = (
        db.query(BenefitProductORM)
        .filter(
            BenefitProductORM.product_type == "point",
            or_(
                BenefitProductORM.store_product_id == key,
                BenefitProductORM.apple_product_id == key,
            ),
        )
        .all()
    )
    if not rows:
        return None
    want = normalize_app_key(app_key) if app_key else ""
    if want:
        matched = [r for r in rows if normalize_app_key(getattr(r, "app_key", None)) == want]
        if matched:
            rows = matched
    rows.sort(
        key=lambda r: (
            1 if r.is_active else 0,
            1 if int(r.credits_amount or 0) > 0 else 0,
            int(r.credits_amount or 0),
            int(r.id or 0),
        ),
        reverse=True,
    )
    return rows[0]


def _resolve_recharge_credits(
    db: Session,
    package_key: str,
    *,
    app_key: str = "",
    credits_hint: int = 0,
) -> tuple[int, object]:
    """金币数量：福利商品 credits_amount 优先，其次预订单，最后 vip_plans.recharge_packages。"""
    product = _lookup_point_product(db, package_key, app_key=app_key)
    if product:
        amount = int(getattr(product, "credits_amount", 0) or 0)
        if amount > 0:
            logger.info(
                "[credit_service] 金币数量取自 benefit_products: key=%s app=%s credits=%s",
                package_key, getattr(product, "app_key", "") or "", amount,
            )
            return amount, product
    hint = int(credits_hint or 0)
    if hint > 0:
        logger.info("[credit_service] 金币数量取自预订单: key=%s credits=%s", package_key, hint)
        return hint, product
    packages = _load_vip_config().get("recharge_packages", _DEFAULT_RECHARGE_PACKAGES)
    pkg = packages.get(package_key) or {}
    amount = int(pkg.get("credits") or 0)
    if amount > 0:
        logger.info("[credit_service] 金币数量取自 vip_plans 配置: key=%s credits=%s", package_key, amount)
    return amount, product


def recharge_credits(db: Session, user_id: int, package_key: str, store_type: str,
                     store_transaction_id: str, store_receipt: str = "",
                     price_usd: float = 0,
                     purchased_at: Optional[datetime] = None,
                     credits_amount: int = 0,
                     app_key: str = "") -> dict:
    """
    充值积分包。
    去重：同一 store_transaction_id 不重复入账。
    金币数优先读 benefit_products.credits_amount（管理端设置的金币）。
    """
    # 去重
    existing = db.query(RechargeRecordORM).filter(
        RechargeRecordORM.store_transaction_id == store_transaction_id
    ).first()
    if existing:
        logger.info("[credit_service] 充值交易已存在，跳过: %s", store_transaction_id)
        return {"ok": True, "reason": "already_processed", "record_id": existing.id}

    key = (package_key or "").strip()
    resolved_app_key = (app_key or "").strip() or _user_app_key(db, user_id)
    credits, product = _resolve_recharge_credits(
        db, key, app_key=resolved_app_key, credits_hint=credits_amount,
    )
    if product and not price_usd:
        price_usd = getattr(product, "price_usd", 0) or 0
    if credits <= 0:
        logger.warning(
            "[credit_service] 金币入账失败: key=%s hint=%s reason=invalid_credits",
            key, credits_amount,
        )
        return {"ok": False, "reason": "invalid_credits" if key else "invalid_package"}

    uc = get_or_create_user_credits(db, user_id)
    uc.balance = (uc.balance or 0) + credits

    # 写入充值记录
    record_kwargs = dict(
        user_id=user_id,
        package_key=key or package_key,
        credits=credits,
        price_usd=price_usd,
        store_type=store_type,
        store_transaction_id=store_transaction_id,
        store_receipt=store_receipt,
    )
    if purchased_at:
        ts = purchased_at if purchased_at.tzinfo else purchased_at.replace(tzinfo=timezone.utc)
        record_kwargs["created_at"] = ts
    record = RechargeRecordORM(**record_kwargs)
    db.add(record)
    db.flush()

    # 写入账单
    new_balance = get_effective_balance(uc)
    _write_transaction(
        db, user_id, "recharge", credits, new_balance,
        vip_type=uc.vip_type or "",
        description=f"充值 {package_key}，获得{credits}积分",
    )

    logger.info("[credit_service] 用户 %s 充值 %s 获得 %d 积分", user_id, package_key, credits)
    try:
        from services.limited_offer import mark_limited_offer_used

        mark_limited_offer_used(db, user_id)
    except Exception:
        logger.exception("[credit_service] mark limited offer used after recharge failed")
    try:
        from services.outreach import schedule_post_pay_recall

        schedule_post_pay_recall(user_id=user_id)
    except Exception as e:
        logger.warning("[credit_service] post_pay recall after recharge failed: %s", e)
    return {"ok": True, "record_id": record.id, "credits_added": credits, "balance": new_balance}


# ===== 权限检查 =====

def check_chat_permission(
    db: Session, user_id: int, companion_id: Optional[str] = None
) -> dict:
    """
    检查对话权限。
    返回 {"allowed": bool, "reason": str, "use_free": bool, "need_vip": bool, "need_recharge": bool, ...}
    companion_id 不影响额度（账号累计）；仍传入以便调用方带会话上下文。
    """
    cfg = get_cost_config()
    chat_cost = cfg["chat_cost"]

    # 1. 检查免费轮次（账号累计）
    if check_free_turn(db, user_id, companion_id):
        return {"allowed": True, "use_free": True, "reason": "free_turn"}

    # 2. 检查积分余额
    uc = refresh_daily_gifts(db, user_id)
    effective = get_effective_balance(uc)
    if effective >= chat_cost:
        return {
            "allowed": True,
            "use_free": False,
            "reason": "credits_sufficient",
            "balance": effective,
        }

    # 3. 积分不足 → 判断是否VIP（终身与订阅等价）
    if is_vip_active(uc):
        return {
            "allowed": False,
            "reason": "credits_insufficient",
            "need_vip": False,
            "need_recharge": True,
            "balance": effective,
        }
    else:
        return {
            "allowed": False,
            "reason": "no_free_turns_and_no_credits",
            "need_vip": True,
            "need_recharge": True,
            "balance": effective,
        }


def check_agent_creation_permission(db: Session, user_id: int) -> dict:
    """
    检查智能体创建权限。
    返回 {"allowed": bool, "reason": str, "need_vip": bool, ...}
    """
    cfg = get_cost_config()
    create_cost = cfg["create_agent_cost"]

    uc = refresh_daily_gifts(db, user_id)

    # 1. 检查是否VIP（lifetime 与有到期时间的订阅一致）
    if not is_vip_active(uc):
        return {"allowed": False, "reason": "vip_required", "need_vip": True}

    # 2. 检查智能体数量限制
    from core.database import CompanionORM, UserORM
    user_row = db.query(UserORM).filter(UserORM.id == user_id).first()
    owner_keys = {str(user_id)}
    if user_row:
        if (user_row.username or "").strip():
            owner_keys.add(user_row.username.strip())
        if (user_row.nickname or "").strip():
            owner_keys.add(user_row.nickname.strip())
    owned_count = db.query(CompanionORM).filter(
        CompanionORM.created_by.in_(list(owner_keys)),
        CompanionORM.is_deleted.isnot(True),
    ).count()
    if owned_count >= (uc.max_agents or 0):
        return {
            "allowed": False,
            "reason": "agent_limit_reached",
            "need_vip": True,
            "owned_agents": owned_count,
            "max_agents": uc.max_agents or 0,
        }

    # 3. 检查积分
    effective = get_effective_balance(uc)
    if effective < create_cost:
        return {
            "allowed": False,
            "reason": "credits_insufficient",
            "need_recharge": True,
            "balance": effective,
            "cost": create_cost,
        }

    return {"allowed": True, "reason": "ok", "balance": effective, "cost": create_cost}


def consume_agent_creation(db: Session, user_id: int) -> dict:
    """创建智能体成功后扣费"""
    cfg = get_cost_config()
    return consume_credits(
        db, user_id, cfg["create_agent_cost"],
        "create_agent", "创建自定义智能体",
    )
