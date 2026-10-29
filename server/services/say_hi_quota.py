"""Say Hi 每日免费次数（独立于每智能体 free_turns_limit）。

配置组 key=say_hi_daily_free_limit，config_json={"say_hi_daily_free_limit": 10}
用户维度：user_credits.say_hi_daily_used + say_hi_daily_date（UTC 日切）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from core.database import ConfigGroupORM, get_db

logger = logging.getLogger(__name__)

CONFIG_KEY = "say_hi_daily_free_limit"
LIMIT_DEFAULT = 10
LIMIT_MIN = 0
LIMIT_MAX = 9999

_CACHE: Optional[dict] = None


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def clamp_daily_limit(raw: Any) -> int:
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        n = LIMIT_DEFAULT
    return max(LIMIT_MIN, min(LIMIT_MAX, n))


def normalize_say_hi_daily_config_json(raw: Any) -> dict:
    data = dict(raw or {}) if isinstance(raw, dict) else {}
    data[CONFIG_KEY] = clamp_daily_limit(data.get(CONFIG_KEY, LIMIT_DEFAULT))
    return data


def refresh_say_hi_daily_limit_cache() -> dict:
    """从 config_groups 加载；缺失则自动补一条。"""
    global _CACHE
    payload = {"enabled": True, CONFIG_KEY: LIMIT_DEFAULT}
    try:
        with get_db() as db:
            row = (
                db.query(ConfigGroupORM)
                .filter(ConfigGroupORM.key == CONFIG_KEY)
                .first()
            )
            if not row:
                row = ConfigGroupORM(
                    key=CONFIG_KEY,
                    name="Say Hi 每日免费次数",
                    description=(
                        "每位用户每天可免费 Say Hi 的次数（UTC 日切）。"
                        "用完后 Say Hi 按正常聊天扣免费轮次/积分。"
                    ),
                    config_type="system",
                    config_json={CONFIG_KEY: LIMIT_DEFAULT},
                    enabled=1,
                    sort_order=8,
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                logger.info("[say_hi_quota] seeded config_groups.%s", CONFIG_KEY)
            cfg = row.config_json or {}
            payload["enabled"] = bool(row.enabled)
            if cfg.get(CONFIG_KEY) is not None:
                payload[CONFIG_KEY] = clamp_daily_limit(cfg.get(CONFIG_KEY))
    except Exception as e:
        logger.warning("[say_hi_quota] refresh failed, use default: %s", e)
    _CACHE = payload
    logger.info("[say_hi_quota] loaded: %s", payload)
    return dict(_CACHE)


def get_say_hi_daily_limit_settings() -> dict:
    global _CACHE
    if _CACHE is None:
        refresh_say_hi_daily_limit_cache()
    return dict(_CACHE or {})


def get_say_hi_daily_free_limit() -> int:
    """禁用配置组时视为 0（不送每日免费）。"""
    s = get_say_hi_daily_limit_settings()
    if not s.get("enabled", True):
        return 0
    return int(clamp_daily_limit(s.get(CONFIG_KEY, LIMIT_DEFAULT)))


def _sync_day(uc, today: str) -> None:
    if (getattr(uc, "say_hi_daily_date", None) or "") != today:
        uc.say_hi_daily_date = today
        uc.say_hi_daily_used = 0


def get_say_hi_daily_snapshot(user_id: int) -> dict:
    """今日 Say Hi 免费：额度 / 已用 / 剩余 / 是否用尽。与每智能体聊天免费分开。"""
    limit = get_say_hi_daily_free_limit()
    used = 0
    if limit <= 0:
        return {
            "say_hi_daily_limit": 0,
            "say_hi_daily_used": 0,
            "say_hi_daily_remaining": 0,
            "say_hi_daily_exhausted": True,
        }
    today = _utc_today()
    try:
        from services.credit_service import get_or_create_user_credits

        with get_db() as db:
            uc = get_or_create_user_credits(db, int(user_id))
            _sync_day(uc, today)
            used = int(getattr(uc, "say_hi_daily_used", 0) or 0)
            db.commit()
    except Exception:
        logger.exception("[say_hi_quota] snapshot failed user=%s", user_id)
        used = 0
    remaining = max(0, limit - used)
    return {
        "say_hi_daily_limit": limit,
        "say_hi_daily_used": max(0, used),
        "say_hi_daily_remaining": remaining,
        "say_hi_daily_exhausted": remaining <= 0,
    }


def get_say_hi_daily_remaining(user_id: int) -> int:
    return int(get_say_hi_daily_snapshot(user_id)["say_hi_daily_remaining"])


def try_consume_say_hi_daily_free(user_id: int) -> Optional[dict]:
    """消耗 1 次每日免费 Say Hi。成功返回 charge_meta；额度用尽返回 None。"""
    limit = get_say_hi_daily_free_limit()
    if limit <= 0:
        return None
    today = _utc_today()
    from services.credit_service import get_or_create_user_credits

    with get_db() as db:
        uc = get_or_create_user_credits(db, int(user_id))
        _sync_day(uc, today)
        used = int(getattr(uc, "say_hi_daily_used", 0) or 0)
        if used >= limit:
            db.commit()
            return None
        uc.say_hi_daily_used = used + 1
        remaining = max(0, limit - int(uc.say_hi_daily_used))
        db.commit()
        return {
            "use_say_hi_daily_free": True,
            "use_free": False,
            "credits_charged": 0,
            "say_hi_daily_remaining": remaining,
            "say_hi_daily_limit": limit,
            "free_turns_remaining": 0,
        }


def _charge_chat_turn(user_id: int, companion_id: str) -> dict:
    from services.chat_turn import charge_chat_turn

    return charge_chat_turn(int(user_id), companion_id)


def decide_say_hi_charge(
    user_id: int,
    companion_id: str,
    *,
    use_chat_free: bool = False,
) -> dict:
    """Say Hi 扣费：每日免费与聊天免费分开。

    返回：
      ok=True + charge_meta：可以继续写打招呼
      need_confirm=True：日额用尽且未确认，不扣聊天免费
      charge_error：确认后聊天免费/积分也不够
    """
    charge_meta = try_consume_say_hi_daily_free(int(user_id))
    if charge_meta is not None:
        return {"ok": True, "charge_meta": charge_meta}
    daily = get_say_hi_daily_snapshot(int(user_id))
    if not use_chat_free:
        return {
            "ok": False,
            "need_confirm": True,
            "charge_meta": {
                "use_say_hi_daily_free": False,
                "use_free": False,
                "credits_charged": 0,
                "say_hi_daily_remaining": int(daily.get("say_hi_daily_remaining") or 0),
                "say_hi_daily_limit": int(daily.get("say_hi_daily_limit") or 0),
                "say_hi_daily_exhausted": True,
                "need_confirm_use_chat_free": True,
            },
        }
    cid = (companion_id or "").strip()[:8]
    try:
        charge_meta = _charge_chat_turn(int(user_id), cid)
        charge_meta["say_hi_daily_remaining"] = int(daily.get("say_hi_daily_remaining") or 0)
        charge_meta["say_hi_daily_limit"] = int(daily.get("say_hi_daily_limit") or 0)
        charge_meta["say_hi_daily_exhausted"] = True
        return {"ok": True, "charge_meta": charge_meta}
    except Exception as e:
        from services.chat_turn import ChatTurnError

        if not isinstance(e, ChatTurnError):
            raise
        return {
            "ok": False,
            "charge_error": {
                "status_code": int(e.status_code),
                "detail": e.detail,
            },
        }


def restore_say_hi_daily_free(user_id: int) -> None:
    """退回 1 次每日免费（回复失败时）。"""
    today = _utc_today()
    try:
        from services.credit_service import get_or_create_user_credits

        with get_db() as db:
            uc = get_or_create_user_credits(db, int(user_id))
            if (getattr(uc, "say_hi_daily_date", None) or "") != today:
                db.commit()
                return
            used = int(getattr(uc, "say_hi_daily_used", 0) or 0)
            if used > 0:
                uc.say_hi_daily_used = used - 1
            db.commit()
    except Exception:
        logger.exception("[say_hi_quota] restore failed user=%s", user_id)
