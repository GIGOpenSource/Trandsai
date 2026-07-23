import os
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from dotenv import load_dotenv
from sqlalchemy import JSON, Column, DateTime, Float, Index, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
logger = logging.getLogger(__name__)

# 优化：使用绝对路径避免相对当前工作目录(cwd)导致的数据库文件找不到或新建空库的问题
base_dir = Path(__file__).parent.parent.resolve()
default_db_path = base_dir / "ai_companion.db"
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{default_db_path}"
)
logger.info("使用数据库路径: %s (绝对路径确保部署一致性)", default_db_path)

_is_sqlite = DATABASE_URL.startswith("sqlite")

if _is_sqlite:
    # SQLite：本地文件，无需连接池保活，支持多线程访问
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
else:
    # MySQL：使用连接池保活和回收
#     engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
# SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# Base = declarative_base()

    # PostgreSQL/MySQL：使用连接池保活和回收
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=5,
        max_overflow=10
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()



# ===== ORM Models =====
#表名 companions
class CompanionORM(Base):
    __tablename__ = "companions"
    id = Column(String(8), primary_key=True)
    name = Column(String(20), nullable=False)
    age = Column(Integer)
    gender = Column(String(2))
    city = Column(String(20))
    personality = Column(Text)
    background = Column(Text)
    speech_style = Column(Text)
    hobbies = Column(Text)
    values = Column(Text)
    fears = Column(Text)
    love_view = Column(Text)
    daily_routine = Column(Text)
    favorite_things = Column(Text)
    mbti = Column(String(10), default="")
    sexual_orientation = Column(String(20), default="")
    life_story = Column(Text)
    cultural_values = Column(Text)
    gender_perspective = Column(Text)
    avatar_url = Column(Text, default="")
    created_by = Column(String(64), default="")
    language = Column(String(10), default="zh")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime, nullable=True, default=None)


class UserCompanionStateORM(Base):
    """登录用户与智能体的亲密度、对话轮数（按用户隔离；未建记录时对话侧回退到 companion_states）。"""
    __tablename__ = "user_companion_states"
    __table_args__ = (Index("idx_user_companion_states_user", "user_id"),)

    user_id = Column(Integer, primary_key=True)
    companion_id = Column(String(8), primary_key=True)
    affection = Column(Float, default=0)
    turns = Column(Integer, default=0)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CompanionStateORM(Base):
    __tablename__ = "companion_states"
    companion_id = Column(String(8), primary_key=True)
    mood = Column(String(20), default="开心")
    affection = Column(Float, default=0)
    summary = Column(Text, default="")
    turns = Column(Integer, default=0)
    evolved_personality = Column(Text, default="")
    evolved_background = Column(Text, default="")
    evolved_speech_style = Column(Text, default="")


class ShortTermMessageORM(Base):
    __tablename__ = "short_term_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    companion_id = Column(String(8), nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)  # 用户ID，按用户隔离聊天记录
    role = Column(String(10), nullable=False)
    content = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FactORM(Base):
    __tablename__ = "facts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    companion_id = Column(String(8), nullable=False, index=True)
    fact = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class RelationSummaryORM(Base):
    __tablename__ = "relation_summaries"
    companion_id = Column(String(8), primary_key=True)
    summary = Column(Text, default="")
    turns_since_update = Column(Integer, default=0)


class KnowledgeEntryORM(Base):
    __tablename__ = "knowledge_entries"
    id = Column(String(8), primary_key=True)
    title = Column(String(200))
    content = Column(Text)
    category = Column(String(50))
    tags = Column(JSON)
    source = Column(String(500))
    language = Column(String(10))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentConfigORM(Base):
    __tablename__ = "agent_config"
    id = Column(Integer, primary_key=True, autoincrement=True)
    config_json = Column(JSON, default=dict)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ConfigGroupORM(Base):
    __tablename__ = "config_groups"
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, default="")
    config_type = Column(String(20), nullable=False, default="agent")  # model_service / agent
    config_json = Column(JSON, default=dict)
    enabled = Column(Integer, default=1)  # 1=启用, 0=禁用
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CompanionAgentConfigORM(Base):
    __tablename__ = "companion_agent_configs"
    companion_id = Column(String(8), primary_key=True)
    config_json = Column(JSON, default=dict)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MomentORM(Base):
    __tablename__ = "moments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    companion_id = Column(String(8), nullable=False, index=True)
    image_url = Column(String(500))
    caption = Column(Text)
    caption_lang = Column(String(10), nullable=True)  # 文案生成所用语言码（与智能体资料语种一致）
    likes_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MomentLikeORM(Base):
    __tablename__ = "moment_likes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    moment_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)  # 用户ID，用于点赞去重
    device_id = Column(String(64), nullable=False)  # 保留，为多设备区分预留
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("uniq_moment_like_user", "moment_id", "user_id", unique=True),
    )


class MomentCommentORM(Base):
    __tablename__ = "moment_comments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    moment_id = Column(Integer, nullable=False, index=True)
    companion_id = Column(String(8), nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)  # 用户ID
    user_device_id = Column(String(64), nullable=True, index=True)  # 保留，为多设备区分预留
    parent_id = Column(Integer, nullable=True, index=True)
    content = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class UserORM(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(32), nullable=False, unique=True)
    nickname = Column(String(32), default="")
    password_hash = Column(String(512), nullable=False)  # 增加长度以支持长哈希值
    gender = Column(String(10), default="")
    sexual_orientation = Column(String(20), default="")
    age = Column(Integer, nullable=True)
    region = Column(String(120), default="")
    occupation = Column(String(100), default="")
    avatar_url = Column(String(500), default="")
    token = Column(String(128), default="")  # 增加token长度
    token_expire = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    role = Column(String(20), default="user")  # 新增：admin/staff/user
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FeedbackThreadORM(Base):
    __tablename__ = "feedback_threads"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    user_name = Column(String(32), default="")
    status = Column(String(20), default="open")  # open / replied / closed
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class FeedbackMessageORM(Base):
    __tablename__ = "feedback_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(Integer, nullable=False, index=True)
    sender = Column(String(20), nullable=False)  # user / admin / system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("idx_feedback_msg", "thread_id", "created_at"),)


class PostORM(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    user_name = Column(String(32), default="匿名用户")
    avatar = Column(String(500), default="")
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    images = Column(JSON, default=list)
    likes_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    category = Column(String(50), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PostLikeORM(Base):
    __tablename__ = "post_likes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, nullable=False, index=True)
    device_id = Column(String(64), nullable=True)
    user_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_post_like_user", "post_id", "user_id"),
        Index("idx_post_like_device", "post_id", "device_id"),
        Index("uniq_post_like_user", "post_id", "user_id", unique=True),
        Index("uniq_post_like_device", "post_id", "device_id", unique=True),
    )


class PostCommentORM(Base):
    __tablename__ = "post_comments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    user_name = Column(String(32), default="匿名用户")
    device_id = Column(String(64), nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AdminTokenORM(Base):
    __tablename__ = "admin_tokens"
    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(64), nullable=False, unique=True, index=True)
    expire_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SystemNotificationORM(Base):
    __tablename__ = "system_notifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    language = Column(String(10), nullable=False, default="zh")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class PageViewORM(Base):
    __tablename__ = "page_views"
    id = Column(Integer, primary_key=True, autoincrement=True)
    page_path = Column(String(200), nullable=False, index=True)
    page_name = Column(String(100), nullable=False, default="")
    user_id = Column(Integer, nullable=True, index=True)  # 用户ID
    device_id = Column(String(64), nullable=False, index=True)  # 保留，为多设备区分预留
    language = Column(String(10), nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ButtonClickORM(Base):
    __tablename__ = "button_clicks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    button_id = Column(String(200), nullable=False, index=True)
    button_name = Column(String(100), nullable=False, default="")
    page_path = Column(String(200), nullable=False, default="", index=True)
    user_id = Column(Integer, nullable=True, index=True)  # 用户ID
    device_id = Column(String(64), nullable=False, index=True)  # 保留，为多设备区分预留
    language = Column(String(10), nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ===== Session Helpers =====

@contextmanager
def get_db() -> Generator[Any, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _ensure_column(table_name: str, column_name: str, column_def: str):
    """检查并添加缺失的数据库列（兼容已有表）"""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    cols = [c["name"] for c in inspector.get_columns(table_name)]
    if column_name not in cols:
        with engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN `{column_name}` {column_def}"))
            conn.commit()
        logger.info("[init_db] 添加列 %s.%s", table_name, column_name)


def _ensure_unique_index(table_name: str, index_name: str, columns: list[str]):
    """检查并添加唯一索引（兼容已有表）"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
    if index_name in existing_indexes:
        return

    quoted_cols = ", ".join(f"`{col}`" for col in columns)
    with engine.connect() as conn:
        conn.execute(
            text(f"CREATE UNIQUE INDEX `{index_name}` ON `{table_name}` ({quoted_cols})")
        )
        conn.commit()
    logger.info("[init_db] 添加唯一索引 %s.%s", table_name, index_name)


def _alter_column_length(table_name: str, column_name: str, new_type: str):
    """修改已存在列的长度（PostgreSQL兼容）"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    try:
        columns = inspector.get_columns(table_name)
        col = next((c for c in columns if c["name"] == column_name), None)
        if not col:
            return

        # 获取当前列类型
        current_type = str(col["type"])

        # 检查是否需要修改
        if new_type.upper() in current_type.upper():
            return

        # 使用ALTER COLUMN修改列类型
        with engine.connect() as conn:
            if _is_sqlite:
                # SQLite不支持ALTER COLUMN，跳过
                logger.info("[init_db] SQLite跳过列类型修改: %s.%s", table_name, column_name)
                return
            else:
                # PostgreSQL/MySQL
                conn.execute(text(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} TYPE {new_type}"))
                conn.commit()
        logger.info("[init_db] 修改列类型: %s.%s -> %s", table_name, column_name, new_type)
    except Exception as e:
        logger.warning("[init_db] 修改列类型失败 %s.%s: %s", table_name, column_name, e)


def init_db():
    Base.metadata.create_all(bind=engine)
    # _ensure_column("moment_likes", "user_id", "INTEGER")
    # 兼容：为已存在的 companion_states 表添加进化字段
    _ensure_column("companion_states", "evolved_personality", "TEXT")
    _ensure_column("companion_states", "evolved_background", "TEXT")
    _ensure_column("companion_states", "evolved_speech_style", "TEXT")
    # 兼容：为已存在的 companions 表添加立体人格字段
    _ensure_column("companions", "hobbies", "TEXT")
    _ensure_column("companions", "values", "TEXT")
    _ensure_column("companions", "fears", "TEXT")
    _ensure_column("companions", "love_view", "TEXT")
    _ensure_column("companions", "daily_routine", "TEXT")
    _ensure_column("companions", "favorite_things", "TEXT")
    _ensure_column("companions", "life_story", "TEXT")
    _ensure_column("companions", "sexual_orientation", "TEXT")
    _ensure_column("users", "sexual_orientation", "TEXT")
    _ensure_column("users", "avatar_url", "VARCHAR(500) DEFAULT ''")
    _ensure_column("companions", "cultural_values", "TEXT")
    _ensure_column("companions", "gender_perspective", "TEXT")
    _ensure_column("companions", "avatar_url", "TEXT")
    _ensure_column("companions", "created_by", "VARCHAR(64) DEFAULT ''")
    _ensure_column("companions", "language", "VARCHAR(10) DEFAULT 'zh'")
    _ensure_column("posts", "category", "VARCHAR(50) DEFAULT ''")
    # 兼容：moment_comments 表新增 user_device_id 字段
    _ensure_column("moment_comments", "user_device_id", "VARCHAR(64)")
    # 兼容：moment_comments 表新增 parent_id 字段（评论回复关系）
    _ensure_column("moment_comments", "parent_id", "INTEGER")
    _ensure_column("moments", "caption_lang", "VARCHAR(10)")
    # 兼容：users 表新增 token 持久化字段
    _ensure_column("users", "token", "VARCHAR(128) DEFAULT ''")
    _ensure_column("users", "token_expire", "DATETIME")
    _ensure_column("users", "age", "INTEGER")
    _ensure_column("users", "region", "VARCHAR(120) DEFAULT ''")
    _ensure_column("users", "occupation", "VARCHAR(100) DEFAULT ''")
    # 修复：扩大password_hash字段长度以支持长哈希值
    _alter_column_length("users", "password_hash", "VARCHAR(512)")
    _alter_column_length("users", "token", "VARCHAR(128)")
    # 兼容：新增统计表（仅列兼容，表本身由 Base.metadata.create_all 创建）
    _ensure_column("page_views", "language", "VARCHAR(10) DEFAULT ''")
    _ensure_column("button_clicks", "language", "VARCHAR(10) DEFAULT ''")
    # 兼容：config_groups 表新增 config_type 和 config_json 字段
    _ensure_column("config_groups", "config_type", "VARCHAR(20) DEFAULT 'agent'")
    _ensure_column("config_groups", "config_json", "TEXT DEFAULT '{}'")
    # 并发一致性：点赞去重唯一索引（只保留 user_id 索引）
    _ensure_unique_index("moment_likes", "uniq_moment_like_user", ["moment_id", "user_id"])
    _ensure_unique_index("post_likes", "uniq_post_like_user", ["post_id", "user_id"])
    _ensure_unique_index("post_likes", "uniq_post_like_device", ["post_id", "device_id"])
    # 兼容：为已存在的表添加 user_id 字段
    _ensure_column("moment_likes", "user_id", "INTEGER")
    _ensure_column("moment_comments", "user_id", "INTEGER")
    _ensure_column("page_views", "user_id", "INTEGER")
    _ensure_column("button_clicks", "user_id", "INTEGER")
    _ensure_column("short_term_messages", "user_id", "INTEGER")
    # 兼容：admin_tokens 表（已存在表则跳过）
    from sqlalchemy import inspect
    inspector = inspect(engine)
    if "admin_tokens" not in inspector.get_table_names():
        AdminTokenORM.__table__.create(bind=engine)

    # ===== Seed 默认配置（确保 batch 脚本、lifespan 和 image gen 都能正常工作） =====
    try:
        # 使用 SessionLocal 避免循环导入问题
        db = SessionLocal()
        try:
            # AgentConfigORM
            if not db.query(AgentConfigORM).first():
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
                db.commit()
                logger.info("[init_db] 已插入默认 AgentConfigORM")

            # ConfigGroupORM 默认组（包括 image_generation）
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
                    "key": "image_generation",
                    "name": "图片生成配置",
                    "description": "配置火山引擎 Ark / xAI 等图片生成服务",
                    "config_type": "image_generation",
                    "config_json": {
                        "provider": "volcano",
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3/images/generations",
                        "api_key": os.getenv("VOLCANO_API_KEY", os.getenv("XAI_API_KEY", "")),
                        "model": "doubao-seedream-5-0-260128",
                        "size": "2K",
                        "default_width": 1024,
                        "default_height": 1024,
                    },
                    "enabled": 1,
                    "sort_order": 3,
                },
            ]
            for g in default_groups:
                exists = db.query(ConfigGroupORM).filter(ConfigGroupORM.key == g["key"]).first()
                if not exists:
                    group = ConfigGroupORM(
                        key=g["key"],
                        name=g["name"],
                        description=g.get("description", ""),
                        config_type=g["config_type"],
                        config_json=g["config_json"],
                        enabled=g.get("enabled", 1),
                        sort_order=g.get("sort_order", 0),
                    )
                    db.add(group)
                    logger.info("[init_db] 已插入默认配置组: %s", g["key"])
            db.commit()
            logger.info("[init_db] 默认配置 seeding 完成")
        finally:
            db.close()
    except Exception as e:
        logger.warning(
            "[init_db] 配置 seeding 失败（非致命，可在启动后通过 /admin/config-groups 补充）: %s",
            e,
        )

    # ===== 发现页官方使用说明帖子（按标题去重；已有库升级时补写缺失条目） =====
    try:
        _db = SessionLocal()
        try:
            from services.discover_guide_seed import seed_discover_guide_posts_if_needed

            n = seed_discover_guide_posts_if_needed(_db)
            if n:
                _db.commit()
                logger.info("[init_db] 已写入发现页官方指南帖子 %s 条", n)
            else:
                _db.rollback()
        finally:
            _db.close()
    except Exception as e:
        logger.warning("[init_db] 发现页指南帖子 seeding 失败（非致命）: %s", e)


def serialize_datetime(dt):
    """将 datetime 序列化为带 UTC 时区信息的 ISO 格式字符串。
    SQLite 不保存时区信息，读取出的 datetime 为 naive 类型，
    这里统一视为 UTC 时间，确保前端能正确转换为浏览者本地时间。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def serialize_datetime_beijing(dt):
    """将 datetime 序列化为北京时间的 ISO 格式字符串（UTC+8）。"""
    if dt is None:
        return None
    from datetime import timedelta
    beijing_tz = timezone(timedelta(hours=8))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_beijing = dt.astimezone(beijing_tz)
    return dt_beijing.isoformat()
