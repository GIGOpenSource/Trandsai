"""IM 接管空闲超时：定时扫描并自动释放。"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_SCAN_INTERVAL_S = max(15, int(os.getenv("IM_TAKEOVER_IDLE_SCAN_S", "30") or 30))


async def takeover_idle_scheduler() -> None:
    """每 worker 都会跑；释放用 DB 条件，重复释放幂等。"""
    await asyncio.sleep(5)
    from services.im_admin import takeover_idle_timeout_seconds as _timeout_fn

    logger.info(
        "[takeover_idle] scheduler started interval=%ss timeout=%ss",
        _SCAN_INTERVAL_S,
        _timeout_fn(),
    )
    while True:
        try:
            from services.im_admin import (
                list_idle_takeovers,
                release_takeover_session,
                takeover_idle_timeout_seconds,
            )

            idle = list_idle_takeovers(limit=100)
            if idle:
                logger.info(
                    "[takeover_idle] scan hit count=%s timeout=%ss",
                    len(idle),
                    takeover_idle_timeout_seconds(),
                )
            for user_id, companion_id in idle:
                try:
                    result = await release_takeover_session(
                        user_id,
                        companion_id,
                        actor={
                            "admin_id": None,
                            "username": "system",
                            "is_super": True,
                        },
                        reason="timeout",
                    )
                    if result.get("released"):
                        logger.info(
                            "[takeover_idle] auto-released user=%s companion=%s prev=%s",
                            user_id,
                            companion_id,
                            result.get("previous_takeover_by"),
                        )
                except Exception:
                    logger.exception(
                        "[takeover_idle] release failed user=%s companion=%s",
                        user_id,
                        companion_id,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[takeover_idle] scan failed")
        await asyncio.sleep(_SCAN_INTERVAL_S)
