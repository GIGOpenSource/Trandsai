"""Feedback 1 分钟自动回复后台扫描。"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_SCAN_INTERVAL_S = max(5, int(os.getenv("FEEDBACK_AUTO_REPLY_SCAN_S", "10") or 10))


async def feedback_auto_reply_scheduler() -> None:
    await asyncio.sleep(5)
    logger.info("[feedback] auto-reply scheduler started interval=%ss", _SCAN_INTERVAL_S)
    while True:
        try:
            from core.database import get_db
            from services.feedback import fire_due_auto_replies

            with get_db() as db:
                n = fire_due_auto_replies(db, limit=80)
            if n:
                logger.info("[feedback] auto-replied %s threads", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[feedback] auto-reply scan failed")
        await asyncio.sleep(_SCAN_INTERVAL_S)
