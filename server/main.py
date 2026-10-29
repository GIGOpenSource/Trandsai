from dotenv import load_dotenv
import os
# 加载.env配置

from fastapi import FastAPI

from core.auth import init_redis
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.admin import router as admin_router
from api.analytics import router as analytics_router
from api.growth import router as growth_router
from api.auth import router as auth_router
from api.companions import router as companions_router
from api.companion_ws import router as companion_ws_router
from api.user_inbox_ws import router as user_inbox_ws_router
from api.culture import router as culture_router
from api.feedback import router as feedback_router
from api.companion_reports import router as companion_reports_router
from api.ugc_reports import router as ugc_reports_router
from api.moments import router as moments_router
from api.posts import router as posts_router
from api.google_login import router as google_auth_router
from api.apple_login import router as apple_auth_router
from api.payment import router as payment_router
from api.benefits import router as benefits_router
from api.devices import router as devices_router

from core.middleware import TimingMiddleware
from core.file_lock import acquire_process_lock
from core.config import ADMIN_DIR, BASE_DIR, DIST_DIR, MEMORY_ROOT
from core.database import AgentConfigORM, ConfigGroupORM, get_db, init_db
from core.health import build_health_payload
from core.openapi_tags import OPENAPI_TAGS, apply_openapi_tags
from core.i18n import _
from core.state import get_companion_manager, set_companion_manager
from services.companion_manager import CompanionManager
from services.knowledge_base import import_cultural_knowledge
from services.memory import start_embedding_download
from services.chat_persist import start_chat_persist_worker, stop_chat_persist_worker

load_dotenv(override=True)
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

from core.log_util import configure_app_logging

configure_app_logging()
logger = logging.getLogger(__name__)


def _get_cors_origins() -> list[str]:
    """CORS：优先 CORS_ORIGINS；未配置时用安全默认列表（不含 *）。"""
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if raw:
        origins = [item.strip() for item in raw.split(",") if item.strip()]
        if origins:
            logger.info("CORS origins from env: %s", origins)
            return origins
    defaults = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
    ]
    logger.info("Using default CORS origins (set CORS_ORIGINS env for production): %s", defaults)
    return defaults

_scheduler_lock_fd = None
_warmup_lock_fd = None
_avatar_recover_lock_fd = None


def _auto_moments_enabled() -> bool:
    """core/set.py：AUTO_MOMENTS_OFF（1=关闭，0=开启）控制自动发朋友圈。"""
    # 注释掉原有逻辑，强制关闭自动发朋友圈
    return False
    # from core.set import auto_moments_enabled
    # return auto_moments_enabled()


def _acquire_lock(lock_path: str) -> bool:
    """尝试获取文件锁，成功返回 True。fd 保持在模块级变量防止 GC 关闭。"""
    return acquire_process_lock(lock_path)


def _log_background_task_result(task: asyncio.Task, task_name: str) -> None:
    """避免 task cancel 时在 done callback 里再次抛 CancelledError。"""
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        logger.warning("%s异常: %s", task_name, exc)


async def _moment_scheduler():
    """后台任务：每30分钟检查一次，遍历所有智能体按概率发朋友圈，并触发AI互动评论。
    通过文件锁确保多 worker（uvicorn --workers N）下只有一个 worker 执行。
    开关：core.set.AUTO_MOMENTS_OFF（1=关闭，0=开启）。
    """
    # 硬关闭自动发朋友圈：不启动定时任务（恢复时删掉下面两行，并恢复 lifespan 启动逻辑）
    logger.info("[Scheduler] 自动发朋友圈已硬关闭，不启动定时任务")
    return

    global _scheduler_lock_fd
    if not _auto_moments_enabled():
        logger.info("[Scheduler] AUTO_MOMENTS_OFF=1，不启动朋友圈定时任务")
        return

    from core.state import get_companion_manager
    from services.moments import auto_generate_ai_comments, auto_generate_moments_for_all

    # 多 worker 场景：只有拿到锁的 worker 才跑调度器
    loop = asyncio.get_event_loop()
    fd = await loop.run_in_executor(None, _acquire_lock, "/tmp/trandsai_moment_scheduler.lock")
    if not fd:
        logger.info("[Scheduler] 其他 worker 已持有调度器锁，跳过")
        return
    _scheduler_lock_fd = fd

    logger.info("[Scheduler] 当前 worker 获得调度器锁，启动定时任务")

    while True:
        try:
            await asyncio.sleep(1800)  # 每30分钟检查一次
            if not _auto_moments_enabled():
                logger.info("[Scheduler] AUTO_MOMENTS_OFF=1，停止定时任务")
                break
            cm = get_companion_manager()
            if not cm:
                continue

            # 遍历所有智能体，每个按概率发朋友圈
            created = await loop.run_in_executor(None, auto_generate_moments_for_all, cm)
            if created:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                logger.info("[Scheduler][%s] 自动生成朋友圈: %s 条", now, created)

            # 触发 AI 之间的互动评论
            comments_created = await loop.run_in_executor(None, auto_generate_ai_comments, cm)
            if comments_created:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                logger.info("[Scheduler][%s] 自动生成 AI 评论: %s 条", now, comments_created)
        except asyncio.CancelledError:
            logger.info("[Scheduler] 后台任务已取消")
            break
        except Exception as e:
            logger.warning("[Scheduler] 错误: %s", e)
            await asyncio.sleep(1800)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import time as _t

    print("[LIFESPAN] startup begin", flush=True)
    try:
        _t0 = _t.monotonic()
        logger.info("[Startup] ▶ 开始初始化...")
        print("[LIFESPAN]   init_redis 开始", flush=True)

        init_redis()
        init_redis_elapsed_ms = (_t.monotonic() - _t0) * 1000
        print(f"[LIFESPAN]   init_redis 完成 {init_redis_elapsed_ms:.1f}ms", flush=True)
        logger.info("[Startup]   init_redis 完成 (%.1fms)", init_redis_elapsed_ms)

        admin_password = (os.getenv("ADMIN_PASSWORD") or "").strip()
        if not admin_password or admin_password == "admin123":
            raise RuntimeError("ADMIN_PASSWORD is missing or insecure; please set a strong password")

        os.makedirs(MEMORY_ROOT, exist_ok=True)

        _t1 = _t.monotonic()
        print("[LIFESPAN]   init_db 开始", flush=True)
        init_db()
        init_db_elapsed_ms = (_t.monotonic() - _t1) * 1000
        print(f"[LIFESPAN]   init_db 完成 {init_db_elapsed_ms:.1f}ms", flush=True)
        logger.info("[Startup]   init_db 完成 (%.1fms)", init_db_elapsed_ms)

        from services.credit_service import refresh_cost_config_cache

        refresh_cost_config_cache()
        from services.affection_floor import refresh_affection_floor_cache

        refresh_affection_floor_cache()
        from services.say_hi_quota import refresh_say_hi_daily_limit_cache

        refresh_say_hi_daily_limit_cache()
        from services.moment_comment_limit import refresh_moment_comment_limit_cache

        refresh_moment_comment_limit_cache()

        _t2 = _t.monotonic()
        print("[LIFESPAN]   CompanionManager 加载开始", flush=True)
        cm = CompanionManager(memory_root=MEMORY_ROOT)
        set_companion_manager(cm)
        companion_elapsed_ms = (_t.monotonic() - _t2) * 1000
        print(
            f"[LIFESPAN]   CompanionManager 加载完成 {len(cm._companions)} 个智能体 {companion_elapsed_ms:.1f}ms",
            flush=True,
        )
        logger.info(
            "[Startup]   CompanionManager 加载完成: %d 个智能体 (%.1fms)",
            len(cm._companions),
            companion_elapsed_ms,
        )

        # 恢复卡住的头像生成：DB 仍为 __GENERATING__ 说明任务曾中断（多 worker / 重启）
        try:
            from services.async_tasks import start_avatar_generation
            from core.database import CompanionORM as _CompanionORM

            global _avatar_recover_lock_fd
            avatar_lock = _acquire_lock("/tmp/trandsai_avatar_recover.lock")
            if avatar_lock:
                _avatar_recover_lock_fd = avatar_lock
                recovered = 0
                with get_db() as db:
                    stuck = (
                        db.query(_CompanionORM)
                        .filter(_CompanionORM.avatar_url == "__GENERATING__")
                        .limit(50)
                        .all()
                    )
                    for row in stuck:
                        c = cm.get(row.id, include_deleted=True)
                        if not c:
                            continue
                        start_avatar_generation(row.id, c.profile.model_dump())
                        recovered += 1
                if recovered:
                    logger.info("[Startup]   已重新排队 %d 个卡住的头像生成任务", recovered)
            else:
                logger.info("[Startup]   其他 worker 已处理头像恢复，跳过")
        except Exception as e:
            logger.warning("[Startup]   头像生成恢复失败: %s", e)

        _t3 = _t.monotonic()
        start_embedding_download()
        logger.info("[Startup]   embedding_download 触发 (%.1fms)", (_t.monotonic() - _t3) * 1000)

        # 避免启动阶段被知识库导入阻塞，先让 API 可用
        async def _import_knowledge_in_background():
            try:
                loop = asyncio.get_event_loop()
                imported = await loop.run_in_executor(None, import_cultural_knowledge)
                if imported:
                    logger.info("[Startup] 文化知识导入完成: %s 条", imported)
                from services.knowledge_base import knowledge_base

                sync = await loop.run_in_executor(None, knowledge_base.ensure_vectors_synced)
                logger.info("[Startup] 知识库向量同步: %s", sync)
            except Exception as e:
                logger.warning("[Startup] 文化知识导入失败: %s", e)

        # 增强容错：知识库初始化失败不阻塞整个服务启动
        # 增强容错：知识库和伴侣加载失败不阻塞服务启动
        knowledge_task = asyncio.create_task(_import_knowledge_in_background())
        knowledge_task.add_done_callback(
            lambda t: _log_background_task_result(t, "知识库导入任务")
        )

        _t4 = _t.monotonic()
        with get_db() as db:
            row = db.query(AgentConfigORM).first()
            if not row:
                default_cfg = {
                    "model_provider": os.getenv("MODEL_PROVIDER", "google"),
                    "temperature": 0.93,
                    "max_tokens": 2048,
                    "system_prompt_zh": "",
                    "system_prompt_en": "",
                    "system_prompt_ja": "",
                    "system_prompt_ko": "",
                    "system_prompt_pt": "",
                    "system_prompt_es": "",
                    "system_prompt_id": "",
                    "system_prompt_ar": "",
                    "system_prompt_fa": "",
                    "system_prompt_ru": "",
                    "system_prompt_bg": "",
                    "system_prompt_tr": "",
                }
                db.add(AgentConfigORM(config_json=default_cfg))

            # 初始化默认配置组
            default_groups = [
                {
                    "key": "model_service",
                    "name": "模型服务配置",
                    "description": "配置大模型 API 密钥和提供商，用于 AI 对话推理",
                    "config_type": "model_service",
                    "config_json": {
                        "model_provider": os.getenv("MODEL_PROVIDER", "google"),
                        "google_ready": bool(os.getenv("GOOGLE_API_KEY", "") or os.getenv("GOOGLEAI_API_KEY", "")),
                        "deepseek_ready": bool(os.getenv("DEEPSEEK_API_KEY", "")),
                        "xai_ready": bool(os.getenv("XAI_API_KEY", "")),
                        "admin_password_set": bool(os.getenv("ADMIN_PASSWORD", "")),
                    },
                    "sort_order": 1,
                },
                {
                    "key": "agent",
                    "name": "Agent 配置",
                    "description": "控制 AI 对话行为、创造力和系统提示词模板",
                    "config_type": "agent",
                    "config_json": {
                        "model_provider": os.getenv("MODEL_PROVIDER", "google"),
                        "temperature": 0.93,
                        "max_tokens": 2048,
                        "system_prompt_zh": "",
                        "system_prompt_en": "",
                        "system_prompt_ja": "",
                        "system_prompt_ko": "",
                        "system_prompt_pt": "",
                        "system_prompt_es": "",
                        "system_prompt_id": "",
                        "system_prompt_ar": "",
                        "system_prompt_fa": "",
                        "system_prompt_ru": "",
                        "system_prompt_bg": "",
                        "system_prompt_tr": "",
                    },
                    "sort_order": 2,
                },
                {
                    "key": "image_gemini",
                    "name": "Gemini 文生图配置",
                    "description": "默认文生图：Gemini 图像模型（同一 Google API Key）",
                    "config_type": "image_generation",
                    "enabled": 1,
                    "sort_order": 3,
                    "config_json": {
                        "provider": "google",
                        "api_key": os.getenv("GOOGLE_API_KEY", os.getenv("GOOGLEAI_API_KEY", "")),
                        "model": os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
                        "base_url": "https://generativelanguage.googleapis.com/v1beta",
                        "timeout": 120,
                    },
                },
                {
                    "key": "image_xai",
                    "name": "xAI Grok 绘图配置",
                    "description": "文生图降级：xAI (Grok) 服务商参数",
                    "config_type": "image_generation",
                    "enabled": 1,
                    "sort_order": 4,
                    "config_json": {
                        "provider": "xai",
                        "api_key": os.getenv("XAI_API_KEY", ""),
                        "model": "grok-imagine-image-quality",
                        "base_url": "https://api.x.ai/v1/images/generations",
                        "timeout": 120
                    }
                },
            ]
            for g in default_groups:
                exists = db.query(ConfigGroupORM).filter(ConfigGroupORM.key == g["key"]).first()
                if not exists:
                    db.add(ConfigGroupORM(**g))

        logger.info("[Startup]   DB 配置初始化完成 (%.1fms)", (_t.monotonic() - _t4) * 1000)
        logger.info("[Startup] ✅ 总启动耗时 %.1fms", (_t.monotonic() - _t0) * 1000)

        # 启动后异步触发一次朋友圈生成，避免阻塞服务可用性
        # 通过文件锁确保多 worker 下只执行一次
        warmup_task = None
        scheduler_task = None
        takeover_idle_task = None
        human_first_task = None
        feedback_auto_task = None

        async def _warmup_generate_moments():
            global _warmup_lock_fd
            _tw = _t.monotonic()
            try:
                if not _auto_moments_enabled():
                    logger.info("[Startup] AUTO_MOMENTS_OFF=1，跳过初始朋友圈生成")
                    return
                # 获取 warmup 锁，确保多 worker 下只执行一次
                fd = _acquire_lock("/tmp/trandsai_moment_warmup.lock")
                if not fd:
                    logger.info("[Startup] 其他 worker 已执行 warmup，跳过")
                    return
                _warmup_lock_fd = fd

                from services.moments import auto_generate_ai_comments, auto_generate_moments_for_all

                cm = get_companion_manager()
                if cm:
                    _loop = asyncio.get_event_loop()
                    created = await _loop.run_in_executor(None, auto_generate_moments_for_all, cm)
                    if created:
                        logger.info("[Startup] 初始生成朋友圈: %s 条", created)
                    comments_created = await _loop.run_in_executor(None, auto_generate_ai_comments, cm)
                    if comments_created:
                        logger.info("[Startup] 初始生成 AI 评论: %s 条", comments_created)
            except Exception as e:
                logger.warning("[Startup] 初始生成朋友圈失败: %s", e)
            logger.info("[Startup]   warmup 完成 (%.1fms)", (_t.monotonic() - _tw) * 1000)

        if False and _auto_moments_enabled():
            warmup_task = asyncio.create_task(_warmup_generate_moments())
            # 启动朋友圈定时生成后台任务
            scheduler_task = asyncio.create_task(_moment_scheduler())
            logger.info("[Startup] AUTO_MOMENTS_OFF=0，已启动朋友圈 warmup + 定时任务")
        else:
            # 硬关闭：即使 AUTO_MOMENTS_OFF=0 也不启动（恢复时去掉上方 `False and`）
            logger.info("[Startup] 自动发朋友圈已硬关闭，不启动 warmup/定时任务")

        start_chat_persist_worker()

        # 启动每日定时任务（VIP赠送刷新 + 智能体生命消耗）
        from services.daily_scheduler import daily_scheduler as _daily_scheduler
        daily_task = asyncio.create_task(_daily_scheduler())

        # 运营触达：Moment 点赞/评论延迟私聊
        from services.outreach_scheduler import outreach_scheduler as _outreach_scheduler
        outreach_task = asyncio.create_task(_outreach_scheduler())

        # IM 接管跨 worker 推送（Redis Pub/Sub）
        from services.im_ws_bridge import start_im_ws_bridge

        await start_im_ws_bridge()

        # 智能体资料缓存跨 worker 失效（Redis Pub/Sub）
        from services.companion_cache_bridge import start_companion_cache_bridge

        await start_companion_cache_bridge()

        # IM 接管空闲超时自动释放
        from services.takeover_idle_scheduler import takeover_idle_scheduler as _takeover_idle

        takeover_idle_task = asyncio.create_task(_takeover_idle())

        from services.im_human_first_scheduler import human_first_scheduler as _human_first

        human_first_task = asyncio.create_task(_human_first())

        # Feedback 不再自动回复，只等人工业务回复

    except Exception as _e:
        print(f"[LIFESPAN] startup error: {_e}", flush=True)
        import traceback
        traceback.print_exc()
        raise

    print("[LIFESPAN] startup complete, entering yield", flush=True)
    yield
    try:
        from services.im_ws_bridge import stop_im_ws_bridge

        await stop_im_ws_bridge()
    except Exception:
        logging.getLogger(__name__).warning("stop_im_ws_bridge failed", exc_info=True)
    try:
        from services.companion_cache_bridge import stop_companion_cache_bridge

        await stop_companion_cache_bridge()
    except Exception:
        logging.getLogger(__name__).warning(
            "stop_companion_cache_bridge failed", exc_info=True
        )
    stop_chat_persist_worker()
    for task in (
        scheduler_task,
        daily_task,
        outreach_task,
        knowledge_task,
        warmup_task,
        takeover_idle_task,
        human_first_task,
        feedback_auto_task,
    ):
        if task is not None:
            task.cancel()
    for task in (
        scheduler_task,
        daily_task,
        outreach_task,
        knowledge_task,
        warmup_task,
        takeover_idle_task,
        human_first_task,
        feedback_auto_task,
    ):
        if task is None:
            continue
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.openapi.utils import get_openapi

# 定义安全方案
security = HTTPBearer(
  scheme_name="Token认证",
  description="在请求头中携带 Token: `Bearer <token值>`",
  auto_error=False  # 不自动抛出错误，由中间件处理
)

app = FastAPI(
    title="TrandsAI API",
    description="AI 数字伴侣后端服务 - 提供对话、朋友圈、知识库等功能",
    version="1.0.0",
    contact={
        "name": "TrandsAI Team",
        "email": "support@example.com",
    },
    license_info={
        "name": "Private",
    },
    lifespan=lifespan,
    openapi_tags=OPENAPI_TAGS,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=86400,
)
# 关键：自定义 OpenAPI schema
def custom_openapi():
  if app.openapi_schema:
      return app.openapi_schema

  openapi_schema = get_openapi(
      title="TrandsAI API",
      version="1.0.0",
      description="AI 数字伴侣后端服务 - 提供对话、朋友圈、知识库等功能",
      routes=app.routes,
      tags=OPENAPI_TAGS,
  )
  apply_openapi_tags(openapi_schema)

  # 添加两种安全方案
  openapi_schema["components"]["securitySchemes"] = {
      "HTTPBearer": {
          "type": "http",
          "scheme": "bearer",
          "bearerFormat": "Token",
          "description": "管理端认证：输入管理 Token（不需要 Bearer 前缀）"
      },
      "XToken": {
          "type": "apiKey",
          "in": "header",
          "name": "x-token",
          "description": "用户端或管理端 Token：请求头 x-token"
      }
  }

  # 全局安全方案
  openapi_schema["security"] = [{"HTTPBearer": []}, {"XToken": []}]
  app.openapi_schema = openapi_schema
  return app.openapi_schema

app.openapi = custom_openapi

from core.middleware import TokenAuthMiddleware, TimingMiddleware

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """统一处理 HTTP 异常，返回标准化响应"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status_code": exc.status_code},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """统一处理未捕获异常，避免堆栈信息泄露到客户端"""
    logger.exception("[ERROR] 未捕获异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": _("服务器内部错误"), "status_code": 500},
    )



app.add_middleware(TokenAuthMiddleware)
app.add_middleware(TimingMiddleware)

# 本地缓存图片目录
_image_dir = Path(
    os.environ.get("IMAGE_STORAGE_DIR", str(Path(BASE_DIR) / "data" / "images"))
).expanduser().resolve()
_image_dir.mkdir(parents=True, exist_ok=True)
app.mount("/data/images", StaticFiles(directory=str(_image_dir)), name="images")

# 用户前端 SPA 静态资源（构建产物 dist/assets）
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

from services.cos_storage import is_cos_enabled


# 管理后台根路径重定向（必须在 Mount /admin 之前注册，否则 Mount 会优先匹配 /admin）
@app.get("/admin", include_in_schema=False)
async def admin_redirect():
    return RedirectResponse(url="/admin/")

# 管理后台静态资源（含 HTML/CSS/JS）
if ADMIN_DIR.exists():
    app.mount("/admin", StaticFiles(directory=str(ADMIN_DIR), html=True), name="admin")


@app.get("/api/health", tags=["系统"], summary="健康检查")
async def api_health():
    return build_health_payload()


@app.get("/",
         summary="API 根路径",
         description="返回 API 基本信息",
         tags=["系统"],
         responses={
             200: {
                 "description": "成功",
                 "content": {
                     "application/json": {
                         "example": {
                             "service": "trandsai API",
                             "version": "1.0.0",
                             "status": "running",
                             "docs": "/docs",
                             "redoc": "/redoc",
                             "health": "/health",
                             "openapi": "/openapi.json"
                         }
                     }
                 }
             }
         })
async def root():
    return {
        "service": "trandsai API",
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc",
    }


# 注册 API 路由（必须在 SPA catch-all 之前注册）
app.include_router(auth_router)
app.include_router(analytics_router)
app.include_router(growth_router)
app.include_router(companions_router)
app.include_router(companion_ws_router)
app.include_router(user_inbox_ws_router)
app.include_router(culture_router)
app.include_router(feedback_router)
app.include_router(companion_reports_router)
app.include_router(ugc_reports_router)
app.include_router(admin_router)
app.include_router(moments_router)
app.include_router(posts_router)
app.include_router(google_auth_router)
app.include_router(apple_auth_router)
app.include_router(payment_router)
app.include_router(benefits_router)
app.include_router(devices_router)

# SPA 路由回退：所有未匹配路径返回 index.html，由 react-router 处理
# 注意：此路由必须注册在所有 API 路由之后
# @app.get("/{full_path:path}")
# async def spa_fallback(full_path: str):
#     # 不拦截 API、admin、WebSocket、文档路径
#     if full_path.startswith(("api/", "admin/", "ws/", "companions", "knowledge/", "docs", "redoc", "openapi.json")) or full_path == "admin":
#         raise HTTPException(status_code=404)
#     # 如果请求的是 dist 中存在的具体文件，直接返回
#     # 安全：阻止路径遍历（确保解析后的路径仍在 DIST_DIR 内）
#     target = (DIST_DIR / full_path).resolve()
#     if not str(target).startswith(str(DIST_DIR.resolve())):
#         raise HTTPException(status_code=404)
#     if target.exists() and target.is_file():
#         return FileResponse(str(target))
#     return FileResponse(str(DIST_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    # 支持环境变量 RELOAD=0 或命令行 --no-reload
    reload = os.environ.get("RELOAD", "1").strip() not in ("0", "false", "no")
    if "--no-reload" in sys.argv:
        reload = False
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload)
