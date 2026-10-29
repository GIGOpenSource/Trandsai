"""朋友圈评论条数上限：同一用户 + 同一条动态。

配置组 key=moment_comment_limit，config_json={"moment_comment_limit": 1}
智能体回复（无 user_id / 有 companion_id）不计入用户条数。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from core.database import ConfigGroupORM, MomentCommentORM, get_db

logger = logging.getLogger(__name__)

CONFIG_KEY = "moment_comment_limit"
LIMIT_DEFAULT = 1
LIMIT_MIN = 0
LIMIT_MAX = 9999

# 业务码：前端识别后跳转私聊（勿与 400 评论失败混淆）
COMMENT_LIMIT_CODE = 460
COMMENT_LIMIT_ERROR = "comment_limit_reached"

_CACHE: Optional[dict] = None


def clamp_comment_limit(raw: Any) -> int:
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        n = LIMIT_DEFAULT
    return max(LIMIT_MIN, min(LIMIT_MAX, n))


def normalize_moment_comment_limit_config_json(raw: Any) -> dict:
    data = dict(raw or {}) if isinstance(raw, dict) else {}
    data[CONFIG_KEY] = clamp_comment_limit(data.get(CONFIG_KEY, LIMIT_DEFAULT))
    return data


def refresh_moment_comment_limit_cache() -> dict:
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
                    name="朋友圈评论次数上限",
                    description=(
                        "同一用户对同一条朋友圈可成功发送的评论条数上限。"
                        "智能体回复不计入；达上限后引导私聊（业务码 460）。"
                    ),
                    config_type="system",
                    config_json={CONFIG_KEY: LIMIT_DEFAULT},
                    enabled=1,
                    sort_order=9,
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                logger.info("[moment_comment_limit] seeded config_groups.%s", CONFIG_KEY)
            cfg = row.config_json or {}
            payload["enabled"] = bool(row.enabled)
            if cfg.get(CONFIG_KEY) is not None:
                payload[CONFIG_KEY] = clamp_comment_limit(cfg.get(CONFIG_KEY))
    except Exception as e:
        logger.warning("[moment_comment_limit] refresh failed, use default: %s", e)
    _CACHE = payload
    logger.info("[moment_comment_limit] loaded: %s", payload)
    return dict(_CACHE)


def get_moment_comment_limit() -> int:
    global _CACHE
    if _CACHE is None:
        refresh_moment_comment_limit_cache()
    if not (_CACHE or {}).get("enabled", True):
        # 配置组关闭：不限制（视为很大上限）
        return LIMIT_MAX
    return int((_CACHE or {}).get(CONFIG_KEY, LIMIT_DEFAULT))


def count_user_moment_comments(db, *, moment_id: int, user_id: int) -> int:
    """已成功落库的用户评论数（智能体回复无 user_id，天然不计）。"""
    if not user_id:
        return 0
    n = (
        db.query(MomentCommentORM.id)
        .filter(
            MomentCommentORM.moment_id == int(moment_id),
            MomentCommentORM.user_id == int(user_id),
        )
        .count()
    )
    return int(n or 0)


def check_user_can_comment(db, *, moment_id: int, user_id: Optional[int]) -> dict:
    """返回 {ok, used, limit}；ok=False 表示已达上限。"""
    limit = get_moment_comment_limit()
    if not user_id:
        return {"ok": True, "used": 0, "limit": limit}
    used = count_user_moment_comments(db, moment_id=moment_id, user_id=int(user_id))
    if used >= limit:
        return {"ok": False, "used": used, "limit": limit}
    return {"ok": True, "used": used, "limit": limit}
