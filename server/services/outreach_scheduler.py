"""
运营触达调度器：周期扫描到期的 outreach_jobs；并周期跑 Phase3 沉默/纪念日扫描。
多 worker 用文件锁，仅一个进程跑循环。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from core.file_lock import acquire_process_lock

logger = logging.getLogger(__name__)

_POLL_SECONDS = float(os.getenv("OUTREACH_POLL_SECONDS", "20"))
_BATCH = int(os.getenv("OUTREACH_BATCH_SIZE", "15"))
# Phase3 全量扫描间隔（秒），默认 1 小时
_PHASE3_SCAN_SECONDS = float(os.getenv("OUTREACH_PHASE3_SCAN_SECONDS", "3600"))


async def outreach_scheduler():
    lock_path = "/tmp/trandsai_outreach_scheduler.lock"
    fd = acquire_process_lock(lock_path)
    if not fd:
        logger.info("[OutreachScheduler] 其他 worker 已持有锁，跳过")
        return

    logger.info(
        "[OutreachScheduler] 启动，poll=%.0fs batch=%d phase3_scan=%.0fs",
        _POLL_SECONDS,
        _BATCH,
        _PHASE3_SCAN_SECONDS,
    )
    loop = asyncio.get_event_loop()
    last_phase3 = 0.0
    while True:
        try:
            from services.outreach import process_due_jobs

            stats = await loop.run_in_executor(None, lambda: process_due_jobs(_BATCH))
            if stats.get("claimed"):
                logger.info("[OutreachScheduler] tick %s", stats)

            now = time.time()
            if now - last_phase3 >= _PHASE3_SCAN_SECONDS:
                last_phase3 = now
                try:
                    from services.outreach_phase3 import run_phase3_periodic_scans

                    p3 = await loop.run_in_executor(None, run_phase3_periodic_scans)
                    logger.info("[OutreachScheduler] phase3 scan %s", p3)
                except Exception as e:
                    logger.warning("[OutreachScheduler] phase3 scan error: %s", e)
        except asyncio.CancelledError:
            logger.info("[OutreachScheduler] 已取消")
            break
        except Exception as e:
            logger.warning("[OutreachScheduler] 异常: %s", e)
        try:
            await asyncio.sleep(_POLL_SECONDS)
        except asyncio.CancelledError:
            break
