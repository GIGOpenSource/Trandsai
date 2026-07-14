from dotenv import load_dotenv
import os
# 加载.env配置
load_dotenv()

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
from api.auth import router as auth_router
from api.companions import router as companions_router
from api.culture import router as culture_router
from api.feedback import router as feedback_router
from api.moments import router as moments_router
from api.posts import router as posts_router

from core.config import ADMIN_DIR, BASE_DIR, DIST_DIR, MEMORY_ROOT
from core.database import AgentConfigORM, ConfigGroupORM, get_db, init_db
from core.state import get_companion_manager, set_companion_manager
from services.companion_manager import CompanionManager
from services.knowledge_base import import_cultural_knowledge
from services.memory import start_embedding_download

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
logger = logging.getLogger(__name__)


def _get_cors_origins() -> list[str]:
    """优化后的CORS配置：优先从环境变量读取，支持生产域名。推荐在.env中设置CORS_ORIGINS=https://yourdomain.com,https://admin.yourdomain.com"""
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if raw:
        origins = [item.strip() for item in raw.split(",") if item.strip()]
        if origins:
            logger.info("CORS origins from env: %s", origins)
            return origins
    # 默认开发端口 + 常见生产建议（生产环境请通过环境变量覆盖，避免使用通配符*以保持安全）
    defaults = ["http://localhost:5173", "http://localhost:5174", "http://127.0.0.1:5173", "http://localhost:8000"]
    logger.info("Using default CORS origins (set CORS_ORIGINS env for production): %s", defaults)
    return defaults


async def _moment_scheduler():
    """后台任务：每小时检查一次，随机为 AI 伴侣生成朋友圈和互动评论"""
    from core.state import get_companion_manager
    from services.moments import auto_generate_ai_comments, auto_generate_moments_for_all

    loop = asyncio.get_event_loop()

    while True:
        try:
            await asyncio.sleep(3600)  # 每小时检查一次
            cm = get_companion_manager()
            if not cm:
                continue

            # 将同步阻塞调用放到线程池中，避免阻塞事件循环
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
            await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    admin_password = (os.getenv("ADMIN_PASSWORD") or "").strip()
    if not admin_password or admin_password == "admin123":
        raise RuntimeError("ADMIN_PASSWORD is missing or insecure; please set a strong password")

    os.makedirs(MEMORY_ROOT, exist_ok=True)
    init_db()
    set_companion_manager(CompanionManager(memory_root=MEMORY_ROOT))
    start_embedding_download()

    # 避免启动阶段被知识库导入阻塞，先让 API 可用
    async def _import_knowledge_in_background():
        try:
            loop = asyncio.get_event_loop()
            imported = await loop.run_in_executor(None, import_cultural_knowledge)
            if imported:
                logger.info("[Startup] 文化知识导入完成: %s 条", imported)
        except Exception as e:
            logger.warning("[Startup] 文化知识导入失败: %s", e)

    # 增强容错：知识库初始化失败不阻塞整个服务启动
    # 增强容错：知识库和伴侣加载失败不阻塞服务启动
    knowledge_task = asyncio.create_task(_import_knowledge_in_background())
    knowledge_task.add_done_callback(
        lambda t: t.exception() and logger.warning("知识库导入任务异常: %s", t.exception())
    )

    with get_db() as db:
        row = db.query(AgentConfigORM).first()
        if not row:
            default_cfg = {
                "model_provider": os.getenv("MODEL_PROVIDER", "anthropic"),
                "temperature": 0.93,
                "max_tokens": 2048,
                "system_prompt_zh": "",
                "system_prompt_en": "",
                "system_prompt_ja": "",
                "system_prompt_ko": "",
                "system_prompt_pt": "",
                "system_prompt_es": "",
                "system_prompt_id": "",
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
                    "model_provider": os.getenv("MODEL_PROVIDER", "anthropic"),
                    "anthropic_ready": bool(os.getenv("ANTHROPIC_API_KEY", "")),
                    "deepseek_ready": bool(os.getenv("DEEPSEEK_API_KEY", "")),
                    "openai_ready": bool(os.getenv("OPENAI_API_KEY", "")),
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
                    "model_provider": os.getenv("MODEL_PROVIDER", "anthropic"),
                    "temperature": 0.93,
                    "max_tokens": 2048,
                    "system_prompt_zh": "",
                    "system_prompt_en": "",
                    "system_prompt_ja": "",
                    "system_prompt_ko": "",
                    "system_prompt_pt": "",
                    "system_prompt_es": "",
                    "system_prompt_id": "",
                },
                "sort_order": 2,
            },
            {
                "key": "image_alibaba",
                "name": "阿里云百炼绘图配置",
                "description": "文生图阿里云服务商参数",
                "config_type": "image_generation",
                "enabled": 1,
                "sort_order": 3,
                "config_json": {
                    "provider": "alibaba",
                    "api_key": "sk-f6ea7e4bdd35459ba0b93dcd659b8744",
                    "model": "qwen-image-2.0-pro-2026-04-22",
                    "base_url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
                    "timeout": 120,
                    "negative_prompt": "模糊、畸形、水印、文字、低画质"
                }
            },
        ]
        for g in default_groups:
            exists = db.query(ConfigGroupORM).filter(ConfigGroupORM.key == g["key"]).first()
            if not exists:
                db.add(ConfigGroupORM(**g))

    # 启动后异步触发一次朋友圈生成，避免阻塞服务可用性
    async def _warmup_generate_moments():
        try:
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

    warmup_task = asyncio.create_task(_warmup_generate_moments())

    # 启动朋友圈定时生成后台任务
    scheduler_task = asyncio.create_task(_moment_scheduler())
    yield
    scheduler_task.cancel()
    knowledge_task.cancel()
    warmup_task.cancel()
    try:
        await asyncio.wait_for(scheduler_task, timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    try:
        await asyncio.wait_for(knowledge_task, timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    try:
        await asyncio.wait_for(warmup_task, timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass


app = FastAPI(title="trandsai", lifespan=lifespan)


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
        content={"error": "服务器内部错误", "status_code": 500},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 用户前端 SPA 静态资源（构建产物 dist/assets）
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

# 本地缓存图片目录（与 image_generation 保持一致，可通过 IMAGE_STORAGE_DIR 指定）
# _image_dir = Path(
#     os.environ.get("IMAGE_STORAGE_DIR", str(Path(BASE_DIR) / "data" / "images"))
# ).expanduser().resolve()
# _image_dir.mkdir(parents=True, exist_ok=True)
# app.mount("/data/images", StaticFiles(directory=str(_image_dir)), name="images")

from services.cos_storage import is_cos_enabled

_image_dir = Path(
    os.environ.get("IMAGE_STORAGE_DIR", str(Path(BASE_DIR) / "data" / "images"))
).expanduser().resolve()
_image_dir.mkdir(parents=True, exist_ok=True)

# 始终挂载本地静态文件目录作为兜底
# 当 COS 已配置时，_to_local_image_url 优先返回 COS URL；
# 若 COS 上传失败，仍可通过 /data/images/ 访问本地文件
app.mount("/data/images", StaticFiles(directory=str(_image_dir)), name="images")


# 管理后台根路径重定向（必须在 Mount /admin 之前注册，否则 Mount 会优先匹配 /admin）
@app.get("/admin")
async def admin_redirect():
    return RedirectResponse(url="/admin/")

# 管理后台静态资源（含 HTML/CSS/JS）
if ADMIN_DIR.exists():
    app.mount("/admin", StaticFiles(directory=str(ADMIN_DIR), html=True), name="admin")


@app.get("/")
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
app.include_router(companions_router)
app.include_router(culture_router)
app.include_router(feedback_router)
app.include_router(admin_router)
app.include_router(moments_router)
app.include_router(posts_router)


# SPA 路由回退：所有未匹配路径返回 index.html，由 react-router 处理
# 注意：此路由必须注册在所有 API 路由之后
@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    # 不拦截 API、admin、WebSocket、文档路径
    if full_path.startswith(("api/", "admin/", "ws/", "companions", "knowledge/", "docs", "redoc", "openapi.json")) or full_path == "admin":
        raise HTTPException(status_code=404)
    # 如果请求的是 dist 中存在的具体文件，直接返回
    # 安全：阻止路径遍历（确保解析后的路径仍在 DIST_DIR 内）
    target = (DIST_DIR / full_path).resolve()
    if not str(target).startswith(str(DIST_DIR.resolve())):
        raise HTTPException(status_code=404)
    if target.exists() and target.is_file():
        return FileResponse(str(target))
    return FileResponse(str(DIST_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
