"""优先人工等待到期：扫描 pending 并交 AI。"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_SCAN_INTERVAL_S = max(2, int(os.getenv("IM_HUMAN_FIRST_SCAN_S", "3") or 3))


async def human_first_scheduler() -> None:
    await asyncio.sleep(3)
    logger.info(
        "[im_human_first] scheduler started interval=%ss",
        _SCAN_INTERVAL_S,
    )
    while True:
        try:
            from services.im_reply_mode import fire_due_human_first_jobs

            n = await fire_due_human_first_jobs(limit=50)
            if n:
                logger.info("[im_human_first] fired %s overdue jobs", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[im_human_first] scan failed")
        await asyncio.sleep(_SCAN_INTERVAL_S)
