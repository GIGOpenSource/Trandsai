import asyncio
import logging
import os
from core.database import CompanionORM, MomentORM, get_db
from core.state import get_companion_manager
from services.image_generation import (
    generate_avatar_prompt,
    generate_image_with_cache,
    generate_moment_image_prompt,
)

logger = logging.getLogger(__name__)

# 并发控制：防止批量生成时图片API被压垮（默认5个并发，可通过环境变量调整）
MAX_CONCURRENT_IMAGES = int(os.getenv("MAX_CONCURRENT_IMAGES", "5"))
_image_semaphore = asyncio.Semaphore(MAX_CONCURRENT_IMAGES)


def _clear_moment_image_generating_flag(moment_id: int) -> None:
    """生成失败或异常时去掉 __GENERATING__ 占位，避免管理端列表永远转圈。"""
    with get_db() as db:
        row = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
        if row and (row.image_url or "") == "__GENERATING__":
            row.image_url = None


def _clear_companion_avatar_generating_flag(companion_id: str) -> None:
    with get_db() as db:
        row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
        if row and (row.avatar_url or "") == "__GENERATING__":
            row.avatar_url = ""


def _sync_companion_avatar_in_memory(companion_id: str, avatar_url: str) -> None:
    """管理端列表从内存 Companion 读；仅写库不更新 profile 会永远显示「生成中」。"""
    cm = get_companion_manager()
    if not cm:
        return
    c = cm.get(companion_id)
    if c:
        c.profile.avatar_url = avatar_url


async def _run_in_thread(func, *args, **kwargs):
    """在线程池中运行同步阻塞函数"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


async def generate_companion_avatar_task(companion_id: str, profile_data: dict):
    """后台异步生成智能体头像并更新数据库（带并发限制）"""
    async with _image_semaphore:
        try:
            prompt = generate_avatar_prompt(profile_data)
            image_url = await _run_in_thread(
                generate_image_with_cache,
                prompt,
                style="portrait",
                width=512,
                height=512,
            )
            if image_url:
                with get_db() as db:
                    row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
                    if row:
                        row.avatar_url = image_url
                _sync_companion_avatar_in_memory(companion_id, image_url)
                logger.info("[AsyncTask] 智能体 %s 头像生成成功: %s", companion_id, image_url)
            else:
                logger.warning("[AsyncTask] 智能体 %s 头像生成失败，无返回 URL", companion_id)
                _clear_companion_avatar_generating_flag(companion_id)
                _sync_companion_avatar_in_memory(companion_id, "")
        except Exception as e:
            logger.error("[AsyncTask] 智能体 %s 头像生成异常: %s", companion_id, e, exc_info=True)
            _clear_companion_avatar_generating_flag(companion_id)
            _sync_companion_avatar_in_memory(companion_id, "")


async def generate_moment_image_task(moment_id: int, caption: str):
    """后台异步生成朋友圈配图并更新数据库：prompt 与风格由 generate_moment_image_prompt
    按文案与人设决定（写实/动漫、风景或人物等）。"""
    async with _image_semaphore:
        try:
            # 加载伴侣个人资料（如果可能）
            profile_dict = None
            with get_db() as db:
                moment = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
                if moment and moment.companion_id:
                    companion = db.query(CompanionORM).filter(CompanionORM.id == moment.companion_id).first()
                    if companion:
                        profile_dict = {
                            "gender": companion.gender or "女",
                            "age": companion.age or 22,
                            "city": companion.city or "",
                            "personality": companion.personality or "",
                            "hobbies": companion.hobbies or "",
                            "mbti": companion.mbti or "",
                        }

            img_prompt, img_style = generate_moment_image_prompt(caption, profile=profile_dict)
            image_url = await _run_in_thread(
                generate_image_with_cache,
                img_prompt,
                style=img_style,
                width=600,
                height=600,
            )
            if image_url:
                with get_db() as db:
                    row = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
                    if row:
                        row.image_url = image_url
                logger.info("[AsyncTask] 朋友圈 %s 配图生成成功: %s", moment_id, image_url)
            else:
                logger.warning("[AsyncTask] 朋友圈 %s 配图生成失败，无返回 URL", moment_id)
                _clear_moment_image_generating_flag(moment_id)
        except Exception as e:
            logger.error("[AsyncTask] 朋友圈 %s 配图生成异常: %s", moment_id, e, exc_info=True)
            _clear_moment_image_generating_flag(moment_id)


def start_avatar_generation(companion_id: str, profile_data: dict):
    """启动智能体头像后台生成任务"""
    asyncio.create_task(generate_companion_avatar_task(companion_id, profile_data))


def start_moment_image_generation(moment_id: int, caption: str):
    """启动朋友圈配图后台生成任务"""
    asyncio.create_task(generate_moment_image_task(moment_id, caption))
