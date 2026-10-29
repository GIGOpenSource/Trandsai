"""提示词用的默认亲密档位（全局 config_groups，不改库里真实 affection）。

仅广告户（users.ua_tags 含 ua:source:ads 等）使用托底：
effective = max(用户与智能体亲密度, 默认档)。
非广告户不托底，用真实亲密度（初始 0）。
配置组 disabled 时广告户也不托底。
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Optional

from core.database import ConfigGroupORM, get_db

logger = logging.getLogger(__name__)

# 本轮聊天是否广告户；由 agent_runner 按 user_id 写入
_prompt_is_ads: ContextVar[bool] = ContextVar("prompt_is_ads_user", default=False)

CONFIG_KEY = "default_affection_floor"
FLOOR_MIN = 20
FLOOR_MAX = 100
# 与当前 TEMP_AFF80 行为对齐，避免上线后尺度骤降；运营可在后台改低
FLOOR_DEFAULT = 80

_CACHE: Optional[dict] = None


def clamp_affection_floor(raw: Any) -> int:
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        n = FLOOR_DEFAULT
    return max(FLOOR_MIN, min(FLOOR_MAX, n))


def refresh_affection_floor_cache() -> dict:
    """从 config_groups 加载；启动与管理端保存后调用。缺失则自动补一条。"""
    global _CACHE
    payload = {
        "enabled": True,
        "default_affection_floor": FLOOR_DEFAULT,
    }
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
                    name="默认亲密档位",
                    description=(
                        "仅广告户（ua_tags 含 ads）提示词托底："
                        "亲密度未超过本值时按本值进档，超过则用用户真实值。"
                        "非广告户从 0 起、不托底。不改库内亲密度。范围 20～100。"
                    ),
                    config_type="system",
                    config_json={"default_affection_floor": FLOOR_DEFAULT},
                    enabled=1,
                    sort_order=7,
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                logger.info("[affection_floor] seeded config_groups.%s", CONFIG_KEY)
            cfg = row.config_json or {}
            payload["enabled"] = bool(row.enabled)
            if cfg.get("default_affection_floor") is not None:
                payload["default_affection_floor"] = clamp_affection_floor(
                    cfg.get("default_affection_floor")
                )
    except Exception as e:
        logger.warning("[affection_floor] refresh failed, use default: %s", e)
    _CACHE = payload
    logger.info("[affection_floor] loaded: %s", payload)
    return dict(_CACHE)


def get_affection_floor_settings() -> dict:
    global _CACHE
    if _CACHE is None:
        refresh_affection_floor_cache()
    return dict(_CACHE or {})


def get_default_affection_floor() -> Optional[float]:
    """返回默认档；配置禁用时返回 None（提示词只用用户真实亲密度）。"""
    s = get_affection_floor_settings()
    if not s.get("enabled", True):
        return None
    return float(clamp_affection_floor(s.get("default_affection_floor")))


def ua_tags_indicate_ads(ua_tags: Any) -> bool:
    """users.ua_tags 含广告标识：ua:source:ads，或任意段为 ads。"""
    from services.ad_attribution import user_has_ad_audience_tags
    from services.recommend_tags import normalize_tag_list

    tags = normalize_tag_list(ua_tags)
    if user_has_ad_audience_tags(tags):
        return True
    for t in tags:
        parts = [p for p in str(t).lower().replace("/", ":").split(":") if p]
        if "ads" in parts:
            return True
    return False


def load_user_is_ads(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    try:
        from core.database import UserORM

        with get_db() as db:
            row = db.query(UserORM.ua_tags).filter(UserORM.id == int(user_id)).first()
            return ua_tags_indicate_ads(row[0] if row else None)
    except Exception as e:
        logger.warning("[affection_floor] load ua_tags user=%s failed: %s", user_id, e)
        return False


def bind_prompt_ads_user(
    *,
    user_id: Optional[int] = None,
    ua_tags: Any = None,
    is_ads: Optional[bool] = None,
):
    """本轮提示词是否按广告户托底。返回 ContextVar token，用完须 reset。"""
    if is_ads is not None:
        flag = bool(is_ads)
    elif ua_tags is not None:
        flag = ua_tags_indicate_ads(ua_tags)
    else:
        flag = load_user_is_ads(user_id)
    return _prompt_is_ads.set(flag)


def reset_prompt_ads_user(token) -> None:
    try:
        _prompt_is_ads.reset(token)
    except Exception:
        pass


def is_prompt_ads_user() -> bool:
    """本轮 ContextVar：是否按广告户处理成人/托底。"""
    return bool(_prompt_is_ads.get())


def effective_prompt_affection(
    user_affection: float,
    is_ads_user: Optional[bool] = None,
) -> float:
    """提示词分层用亲密度。不写回 DB。

    广告户：max(用户真实值, 全局默认档)；用户更高则用用户的。
    非广告户：只用真实亲密度（初始 0，不托底）。
    """
    raw = float(user_affection or 0.0)
    raw = max(0.0, min(100.0, raw))
    ads = bool(is_ads_user) if is_ads_user is not None else bool(_prompt_is_ads.get())
    if not ads:
        return raw
    floor = get_default_affection_floor()
    if floor is None:
        return raw
    return max(raw, float(floor))


def normalize_floor_config_json(raw: Any) -> dict:
    """管理端写入时规范化 config_json。"""
    data = dict(raw or {}) if isinstance(raw, dict) else {}
    data["default_affection_floor"] = clamp_affection_floor(
        data.get("default_affection_floor", FLOOR_DEFAULT)
    )
    return data
