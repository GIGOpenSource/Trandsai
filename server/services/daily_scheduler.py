"""
每日定时任务

1. VIP 每日赠送刷新
2. 智能体每日生命消耗

在 main.py lifespan 中启动，每天 UTC 0:00 执行。
通过文件锁确保多 worker 下只执行一次。
"""

import asyncio
import logging
from datetime import datetime, timezone

from core.file_lock import acquire_process_lock

logger = logging.getLogger(__name__)


async def daily_scheduler():
    """后台任务：每天 UTC 0:00 执行每日任务"""
    lock_path = "/tmp/trandsai_daily_scheduler.lock"
    fd = acquire_process_lock(lock_path)
    if not fd:
        logger.info("[DailyScheduler] 其他 worker 已持有调度器锁，跳过")
        return

    logger.info("[DailyScheduler] 当前 worker 获得调度器锁，启动每日任务")

    while True:
        try:
            # 计算距离下一个 UTC 0:00 的秒数
            now = datetime.now(timezone.utc)
            tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if now.hour >= 0:
                from datetime import timedelta
                tomorrow = tomorrow + timedelta(days=1)
            wait_seconds = (tomorrow - now).total_seconds()
            if wait_seconds < 0:
                wait_seconds = 60

            logger.info("[DailyScheduler] 距离下次执行: %.0f 秒", wait_seconds)
            await asyncio.sleep(wait_seconds)

            # 执行每日任务
            await _run_daily_tasks()

        except asyncio.CancelledError:
            logger.info("[DailyScheduler] 任务已取消")
            break
        except Exception as e:
            logger.warning("[DailyScheduler] 执行异常: %s", e)
            await asyncio.sleep(300)  # 出错后等5分钟重试


async def _run_daily_tasks():
    """执行每日任务"""
    loop = asyncio.get_event_loop()

    # 1. 智能体每日生命消耗
    try:
        result = await loop.run_in_executor(None, _agent_daily_cost)
        logger.info("[DailyScheduler] 智能体每日消耗完成: 处理 %d 个用户", result)
    except Exception as e:
        logger.warning("[DailyScheduler] 智能体每日消耗失败: %s", e)

    # 2. VIP 过期检查（清理过期VIP）
    try:
        result = await loop.run_in_executor(None, _cleanup_expired_vip)
        logger.info("[DailyScheduler] VIP过期清理完成: 清理 %d 个", result)
    except Exception as e:
        logger.warning("[DailyScheduler] VIP过期清理失败: %s", e)


def _agent_daily_cost() -> int:
    """
    智能体每日生命消耗：遍历所有有智能体的用户，每个智能体消耗指定积分。
    积分不足时智能体进入休眠（不扣费，不删除）。
    返回处理的用户数。
    """
    from core.database import CompanionORM, UserCreditORM, get_db
    from services.credit_service import get_cost_config, consume_credits, get_effective_balance

    cost_cfg = get_cost_config()
    daily_cost = cost_cfg.get("agent_daily_cost", 10)
    if daily_cost <= 0:
        return 0

    processed = 0
    with get_db() as db:
        # 获取所有有智能体的用户（created_by 非空）
        companions = db.query(CompanionORM).filter(
            CompanionORM.is_deleted == False,
            CompanionORM.created_by != "",
            CompanionORM.created_by.isnot(None),
        ).all()

        # 按 user_id 分组
        user_agents: dict[int, int] = {}
        for c in companions:
            try:
                uid = int(c.created_by)
                user_agents[uid] = user_agents.get(uid, 0) + 1
            except (ValueError, TypeError):
                continue

        for user_id, agent_count in user_agents.items():
            total_cost = daily_cost * agent_count
            uc = db.query(UserCreditORM).filter(UserCreditORM.user_id == user_id).first()
            if not uc:
                continue

            effective = get_effective_balance(uc)
            if effective < total_cost:
                # 积分不足，不扣费（智能体进入"休眠"状态由前端判断）
                logger.info(
                    "[DailyScheduler] 用户 %s 积分不足（需%d/有%d），跳过扣费",
                    user_id, total_cost, effective,
                )
                continue

            consume_credits(db, user_id, total_cost, "agent_daily", f"智能体每日消耗 x{agent_count}")
            processed += 1

        db.commit()

    return processed


def _cleanup_expired_vip() -> int:
    """清理已过期的VIP状态"""
    from core.database import UserCreditORM, VipSubscriptionORM, get_db

    now = datetime.now(timezone.utc)
    cleaned = 0

    with get_db() as db:
        # 查找所有标记为 active 但已过期的订阅
        expired_subs = db.query(VipSubscriptionORM).filter(
            VipSubscriptionORM.status == "active",
            VipSubscriptionORM.expire_at < now,
            VipSubscriptionORM.plan_type != "lifetime",
        ).all()

        for sub in expired_subs:
            sub.status = "expired"

            # 同步清理用户积分状态
            uc = db.query(UserCreditORM).filter(UserCreditORM.user_id == sub.user_id).first()
            if uc and uc.vip_type == sub.plan_type:
                # 检查是否还有其他有效订阅
                other_active = db.query(VipSubscriptionORM).filter(
                    VipSubscriptionORM.user_id == sub.user_id,
                    VipSubscriptionORM.status == "active",
                    VipSubscriptionORM.id != sub.id,
                ).first()
                if not other_active:
                    uc.vip_type = ""
                    uc.daily_gift_total = 0
                    uc.daily_gift_remaining = 0
                    uc.max_agents = 0

            cleaned += 1

        db.commit()

    return cleaned
