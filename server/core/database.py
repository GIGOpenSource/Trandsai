import os
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from dotenv import load_dotenv
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env", override=True)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 环境变量未设置，请配置 PostgreSQL 连接地址")

_is_postgres = DATABASE_URL.startswith("postgresql") or DATABASE_URL.startswith("postgres")
if not _is_postgres:
    raise RuntimeError(f"仅支持 PostgreSQL 数据库，当前 DATABASE_URL: {DATABASE_URL[:30]}...")

logger.info("使用 PostgreSQL 数据库")

# PostgreSQL：连接池保活和回收
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
    description = Column(Text, default="")  # 短简介（卡片/列表），≠ background 成长经历
    speech_style = Column(Text)
    appearance = Column(Text, default="")  # 长相/外形描述
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
    avatar_style = Column(String(64), default="")  # 头像风格（生成/展示用）
    # 资料背景图：1~3 张图片 URL；空数组表示未设置
    profile_backgrounds = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_by = Column(String(64), default="")
    language = Column(String(10), default="zh")
    persona_axes = Column(Text, default="")  # 人设维度轴 JSON
    country = Column(String(8), default="")   # 国家
    region_key = Column(String(32), default="")  # 地区标识
    region_label = Column(String(64), default="")  # 地区中文名称
    # 运营触达分层：s / featured / candidate / ops；空视为 featured（兼容存量）
    tier = Column(String(20), default="featured")
    # 打上 tier=ops 前的原分层，取消责任人时恢复（不强制回 featured）
    pre_ops_tier = Column(String(20), nullable=True)
    # 推荐标签：性格 key + gender:* + ops；禁止 NULL
    tags = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_deleted = Column(Boolean, default=False)
    # 内容管理：未生效时对 C 端隐藏（与软删 is_deleted 独立）
    is_active = Column(Boolean, default=True)
    # 举报审核状态：published / under_review / removed / restored
    moderation_status = Column(String(20), default="published")
    # 多语言资料：{"en":{"personality":"..."}, "ja":{...}}
    i18n_profile = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class CompanionStrategyORM(Base):
    """【单体消息策略】某智能体专属招呼文案（按 trigger_event/语种）。

    执行时优先于【埋点消息策略库】msg_strategy_library 同类型文案。
    不是【快速回复】quick_reply_*（用户发消息后关键词回）。
    """
    __tablename__ = "companion_strategies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    companion_id = Column(String(8), nullable=False, index=True)
    strategy_type = Column(String(64), nullable=False, index=True)
    content = Column(Text, nullable=False, default="")
    image_url = Column(String(500), default="")  # 可选配图；空=纯文本
    language = Column(String(10), nullable=False, default="zh", index=True)
    trigger_event = Column(String(128), default="")  # 埋点英文事件名，供策略执行器使用
    source = Column(String(20), default="manual")  # manual / system
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_by = Column(String(64), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_companion_strategy_type_lang", "companion_id", "strategy_type", "language"),
    )


class CompanionQuickReplyORM(Base):
    """兼容旧表名占位；新逻辑用 QuickReplyLibraryORM / QuickReplyConfigORM。"""
    __tablename__ = "companion_quick_replies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    companion_id = Column(String(8), nullable=False, index=True)
    language = Column(String(10), nullable=False, default="zh", index=True)
    content = Column(Text, nullable=False, default="")
    keywords = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_by = Column(String(64), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class QuickReplyLibraryORM(Base):
    """【快回消息策略库】对齐埋点消息策略库形态的全局话术。

    字段：用户触发词 keywords、用户语言 language、快回文案 content、
    作用智能体 companion_ids、序号 sort_order、是否生效 is_active。
    触发：用户发聊天消息后命中（非埋点）。
    """
    __tablename__ = "quick_reply_library"
    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(Text, nullable=False, default="")
    language = Column(String(10), nullable=False, default="zh", index=True)
    # 关键词；空 = 兜底话术
    keywords = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # 作用智能体 ID 列表
    companion_ids = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_by = Column(String(64), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_quick_reply_lib_lang", "language"),
    )


class QuickReplyConfigORM(Base):
    """快速回复策略配置：按智能体开关。无此行 = 以策略库绑定为准；is_active=false 则关闭。"""
    __tablename__ = "quick_reply_configs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    companion_id = Column(String(8), nullable=False, unique=True, index=True)
    name = Column(String(120), default="")  # 配置备注名，可选
    is_active = Column(Boolean, default=True)
    priority = Column(Integer, default=0)
    created_by = Column(String(64), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class StrategyTypeORM(Base):
    """策略类型 = 埋点事件字典（英文 event_key + 中文名 + 上报规则等）。"""
    __tablename__ = "strategy_types"
    id = Column(Integer, primary_key=True, autoincrement=True)
    seq = Column(Integer, default=0, index=True)  # 埋点表序号
    event_key = Column(String(128), nullable=False, unique=True, index=True)  # 英文事件名
    name_zh = Column(String(200), nullable=False, default="")  # 中文事件名
    report_rule = Column(Text, default="")  # 上报规则
    remark = Column(Text, default="")  # 备注
    report_type = Column(String(64), default="PV & UV")  # 上报类型
    status = Column(String(32), default="")  # 状态
    note = Column(Text, default="")  # 备注2
    is_active = Column(Boolean, default=True)  # 是否可用于策略绑定
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MsgStrategyGlobalORM(Base):
    """消息策略 - 全局控制（疲劳 / 每日上限 / 间隔 / 优先级）。"""
    __tablename__ = "msg_strategy_globals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False, default="")
    user_scope_type = Column(String(32), default="all")  # all / ads / attr
    user_scope_value = Column(Text, default="")  # Ads标记或用户属性标记，逗号/JSON
    fatigue_window_hours = Column(Integer, default=2)
    fatigue_max_unreplied = Column(Integer, default=10)
    daily_limit = Column(Integer, default=20)
    interval_min_sec = Column(Integer, default=15)
    interval_max_sec = Column(Integer, default=45)
    priority = Column(Integer, default=0)  # 数字越大越优先
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MsgStrategyRuleORM(Base):
    """消息策略规则：各触发条件对应下发数量 + 用户范围 + 优先级。"""
    __tablename__ = "msg_strategy_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False, default="")
    user_scope_type = Column(String(32), default="all")  # all / ads / attr
    user_scope_value = Column(Text, default="")
    # {"register": 1, "filter_guide": 1, "first_message": 2, ...}
    triggers = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    priority = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MsgStrategyLibraryORM(Base):
    """消息策略库文案：语言 + 类型 + 作用范围。

    companion_ids 空 = 全局（全部智能体）；非空 = 精准（仅列表内智能体）。
    管理端：全局用 /api/admin/msg-strategy/library；精准用 strategy-center/msg-library。
    """
    __tablename__ = "msg_strategy_library"
    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(Text, nullable=False, default="")
    image_url = Column(String(500), default="")  # 可选配图；空=纯文本
    language = Column(String(10), nullable=False, default="zh", index=True)
    type_key = Column(String(64), nullable=False, default="", index=True)  # 招呼类型 key
    # [] = 全局；非空 = 精准指定智能体
    companion_ids = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_by = Column(String(64), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MsgStrategyUserCompanionEventORM(Base):
    """用户 × 智能体 × 埋点事件：已成功触发条数（终身累计）。

    规则 triggers[event]=N 的上限按本表计数；默认 1。到量后该智能体不再对该用户触发同事件。
    """
    __tablename__ = "msg_strategy_user_companion_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    companion_id = Column(String(8), nullable=False, index=True)
    event_key = Column(String(128), nullable=False, index=True)
    sent_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "companion_id",
            "event_key",
            name="uq_msg_strategy_user_companion_event",
        ),
        Index("idx_msuce_user_event", "user_id", "event_key"),
    )


class MsgStrategyDispatchLogORM(Base):
    """消息策略触发/丢弃/发送日志。"""
    __tablename__ = "msg_strategy_dispatch_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    event_key = Column(String(128), default="", index=True)
    trigger_key = Column(String(64), default="", index=True)
    global_id = Column(Integer, nullable=True)
    rule_id = Column(Integer, nullable=True)
    library_id = Column(Integer, nullable=True)
    companion_id = Column(String(8), default="", index=True)
    content = Column(Text, default="")
    status = Column(String(32), default="queued", index=True)  # queued/sent/skipped/discarded
    skip_reason = Column(String(200), default="")
    job_id = Column(Integer, nullable=True)
    execute_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_msg_strategy_log_user_created", "user_id", "created_at"),
    )


class UserCompanionStateORM(Base):
    """登录用户与智能体的亲密度、对话轮数（按用户隔离；未建记录时对话侧回退到 companion_states）。"""
    __tablename__ = "user_companion_states"
    __table_args__ = (
        Index("idx_user_companion_states_user", "user_id"),
        Index("idx_ucs_ops_admin", "ops_admin_id"),
        Index("idx_ucs_takeover_by", "takeover_by"),
    )

    user_id = Column(Integer, primary_key=True)
    companion_id = Column(String(8), primary_key=True)
    affection = Column(Float, default=0)
    turns = Column(Integer, default=0)
    relation_card_json = Column(Text, nullable=True)
    relation_card_version = Column(Integer, nullable=False, default=0)
    # IM 管理：责任运营 / 人工接管（admin users.id）
    ops_admin_id = Column(Integer, nullable=True)
    takeover_by = Column(Integer, nullable=True)
    takeover_at = Column(DateTime, nullable=True)
    # 接管空闲续期：发消息 / 会话 WS ping 刷新；超时自动释放
    takeover_active_at = Column(DateTime, nullable=True)
    # IM 管理：运营未读（用户消息条数；打开会话清零）
    ops_unread_count = Column(Integer, nullable=False, default=0)
    ops_last_read_at = Column(DateTime, nullable=True)
    # 该用户对本智能体已用的免费聊天次数（上限见 free_turns_limit 配置）
    free_turns_used = Column(Integer, nullable=False, default=0)
    # 用户第一次连上该智能体聊天 WS 的时间；有值后 first_chat 恒为 false
    first_im_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ImReplySettingsORM(Base):
    """IM 回复模式：全局一条。ai=立即 AI；human_first=先等人工，超时再交 AI。"""
    __tablename__ = "im_reply_settings"
    id = Column(Integer, primary_key=True)
    reply_mode = Column(String(20), nullable=False, default="ai")  # ai | human_first
    wait_seconds = Column(Integer, nullable=False, default=30)
    updated_by = Column(String(64), default="")
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ImHumanFirstPendingORM(Base):
    """优先人工：等待窗口内暂缓自动回复。到期且仍未接管则交 AI。"""
    __tablename__ = "im_human_first_pending"
    user_id = Column(Integer, primary_key=True)
    companion_id = Column(String(8), primary_key=True)
    due_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_im_hf_pending_due", "due_at"),
    )


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
    role = Column(String(20), nullable=False)
    content = Column(Text)
    # 运营人工消息时记录后台账号；普通对话为空
    admin_id = Column(Integer, nullable=True)
    # 对话举报审核通过后隐藏，C 端消息列表不返回
    is_hidden = Column(Boolean, default=False, index=True)
    # 空=正常发出；unpaid=超额/欠费已落库但不走 AI
    send_status = Column(String(20), default="", index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index(
            "idx_stm_user_companion_role_created",
            "user_id",
            "companion_id",
            "role",
            "created_at",
        ),
    )


class AdminImAuditLogORM(Base):
    """管理端 IM 操作审计（接管 / 发消息 / 清空 / 指定责任运营等）。"""
    __tablename__ = "admin_im_audit_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(Integer, nullable=True, index=True)
    admin_username = Column(String(64), default="")
    action = Column(String(40), nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    companion_id = Column(String(8), nullable=True, index=True)
    detail = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class FactORM(Base):
    __tablename__ = "facts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    companion_id = Column(String(8), nullable=False, index=True)
    # 按用户隔离事实；NULL 表示历史未隔离数据
    user_id = Column(Integer, nullable=True, index=True)
    fact = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class RelationSummaryORM(Base):
    __tablename__ = "relation_summaries"
    companion_id = Column(String(8), primary_key=True)
    summary = Column(Text, default="")
    turns_since_update = Column(Integer, default=0)


class UserRelationSummaryORM(Base):
    """按用户隔离的关系摘要（替代仅 companion 级的 relation_summaries）。"""
    __tablename__ = "user_relation_summaries"
    user_id = Column(Integer, primary_key=True)
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
    preview_url = Column(String(500))  # 列表预览（最佳可视裁剪）；空则前端/接口回退 image_url
    preview_pos = Column(JSONB, nullable=True)  # {"x":0~1,"y":0~1} 盖住 4:3 窗口后的滑动位置
    caption = Column(Text)
    caption_lang = Column(String(10), nullable=True)  # 文案生成所用语言码（与智能体资料语种一致）
    likes_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    view_count = Column(Integer, default=0)
    view_users = Column(Integer, default=0)
    detail_click_count = Column(Integer, default=0)
    detail_click_users = Column(Integer, default=0)
    schedule_lang = Column(String(10), nullable=True)  # 定时发布目标语种/地区（决定 IANA 时区）
    schedule_at_local = Column(String(32), nullable=True)  # 运营填写的本地时间 YYYY-MM-DD HH:MM:SS
    publish_at = Column(DateTime, nullable=True, index=True)  # 换算后的 UTC 开放时间；空=立即（看 created_at）
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MomentStatEventORM(Base):
    """朋友圈展示 / 详情点击流水。次数、人数同步写回 moments 计数字段，列表不再扫归因表。"""
    __tablename__ = "moment_stat_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    moment_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    device_id = Column(String(64), nullable=False, default="")
    event_kind = Column(String(16), nullable=False, default="view")  # view | detail_click
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_moment_stat_user_kind", "moment_id", "user_id", "event_kind"),
        Index("idx_moment_stat_device_kind", "moment_id", "device_id", "event_kind"),
    )


class SayHiCopyORM(Base):
    """Say Hi 打招呼文案库：按语种配置多条文案，用户端按 Accept-Language 随机抽一条发出。"""
    __tablename__ = "say_hi_copies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(Text, nullable=False, default="")
    language = Column(String(10), nullable=False, default="en", index=True)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    remark = Column(String(256), default="")
    created_by = Column(String(64), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    __table_args__ = (
        Index("idx_say_hi_copy_lang_active", "language", "is_active"),
    )


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
    username = Column(String(128), nullable=False, unique=True)
    nickname = Column(String(32), default="")
    password_hash = Column(String(512), nullable=False)  # 增加长度以支持长哈希值
    gender = Column(String(20), default="")
    sexual_orientation = Column(String(20), default="")
    preferred_gender = Column(String(20), default="")  # 想要交流的性别
    chat_purpose = Column(String(50), default="")      # 聊天目的：chatting/companionship/romance/communication
    age = Column(Integer, nullable=True)
    region = Column(String(120), default="")
    occupation = Column(String(100), default="")
    avatar_url = Column(String(500), default="")
    # 用户资料背景图：已审核通过的 URL 列表，最多 3 张；待审不写这里
    profile_backgrounds = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    birth = Column(Date, nullable=True)  # 出生日期
    token = Column(String(128), default="")  # 增加token长度
    token_expire = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    role = Column(String(20), default="user")  # 新增：admin/staff/user
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    app_version = Column(String(32), default="")  # 最近一次客户端 X-App-Version；未传不覆盖
    platform = Column(String(20), default='local')  # 'local' / 'google' / 'apple' / 'guest'
    provider_user_id = Column(String(255), nullable=True)  # 第三方平台的用户唯一ID
    app_key = Column(String(32), default="trandsai", index=True)  # 应用马甲：trandsai / kaya
    timezone = Column(String(64), default="")  # IANA tz，运营触达静默窗用
    language = Column(String(10), default="en")  # 用户 UI 语言短码；新建默认 en，策略下发按此匹配
    tags = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))  # 运营 tags
    ua_tags = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))  # ZeasAds 回写
    # 账号状态：active=正常 / banned=封禁 / deleted=注销冷静期 / closed=已销户
    account_status = Column(String(20), default="active", index=True)
    # 进入注销冷静期的时间；期满登录后销户并清空第三方标识
    deleted_at = Column(DateTime, nullable=True)
    # 管理端账号扩展（普通 App 用户可空）
    email = Column(String(200), default="")
    remark = Column(Text, default="")
    admin_companion_ids = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

class FeedbackThreadORM(Base):
    __tablename__ = "feedback_threads"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    user_name = Column(String(32), default="")
    status = Column(String(20), default="open")  # open / replied / closed
    welcome_sent = Column(Boolean, default=False)
    last_user_msg_at = Column(DateTime, nullable=True)
    auto_reply_after_user_at = Column(DateTime, nullable=True)  # 本轮已对哪条用户消息发过自动回复
    user_last_read_at = Column(DateTime, nullable=True)
    admin_last_read_at = Column(DateTime, nullable=True)
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
    language = Column(String(10), default="zh", index=True)
    likes_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    category = Column(String(50), default="")
    review_status = Column(String(20), default="pending", index=True)  # pending/approved/rejected
    reject_reason = Column(String(200), default="")
    reject_reason_id = Column(Integer, nullable=True)
    reviewed_by = Column(String(50), default="")
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class UserProfileReviewORM(Base):
    """用户资料审核表：通过前仅自己可见待审内容，公开资料仍用 users 表已生效字段"""
    __tablename__ = "user_profile_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    pending_data = Column(JSON, default=dict)  # 待审核字段快照
    current_data = Column(JSON, default=dict)  # 提交时的已生效资料快照
    status = Column(String(20), default="pending", index=True)  # pending/approved/rejected
    reject_reason = Column(String(200), default="")
    reject_reason_id = Column(Integer, nullable=True)
    reviewed_by = Column(String(50), default="")
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


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


class AdminRoleORM(Base):
    __tablename__ = "admin_roles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    role_key = Column(String(32), nullable=False, unique=True, index=True)
    role_name = Column(String(64), nullable=False, default="")
    description = Column(String(200), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class AdminMenuORM(Base):
    __tablename__ = "admin_menus"
    id = Column(Integer, primary_key=True, autoincrement=True)
    menu_key = Column(String(64), nullable=False, unique=True, index=True)
    menu_name = Column(String(100), nullable=False, default="")  # 中文名
    menu_name_en = Column(String(100), nullable=False, default="")  # 英文名
    path = Column(String(200), nullable=False, unique=True, index=True)
    parent_key = Column(String(64), default="")
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class AdminRoleMenuORM(Base):
    __tablename__ = "admin_role_menus"
    id = Column(Integer, primary_key=True, autoincrement=True)
    role_key = Column(String(32), nullable=False, index=True)
    menu_key = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("uniq_admin_role_menu", "role_key", "menu_key", unique=True),
    )


class SystemNotificationORM(Base):
    __tablename__ = "system_notifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    language = Column(String(10), nullable=False, default="zh")
    # NULL = 全员广播；有值 = 仅对该用户展示（如审核通过通知）
    user_id = Column(Integer, nullable=True, index=True)
    # 运营私信等：跳转聊天用
    companion_id = Column(String(8), nullable=True, index=True)
    # 评论回复通知：跳转该条朋友圈
    moment_id = Column(Integer, nullable=True, index=True)
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


class UserAttributionEventORM(Base):
    """用户增长 / 归因事件（独立表，append-only，不影响核心业务）。"""
    __tablename__ = "user_attribution_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    device_id = Column(String(64), nullable=False, index=True)
    event_type = Column(String(32), nullable=False, default="install")  # install/first_open/register/login/purchase
    install_source = Column(String(32), default="")  # organic/ads/invite/referral/unknown
    utm_source = Column(String(128), default="")
    utm_medium = Column(String(128), default="")
    utm_campaign = Column(String(128), default="")
    utm_content = Column(String(128), default="")
    utm_term = Column(String(128), default="")
    gclid = Column(String(256), default="")  # Google Click ID（从 body / play_referrer 抽出）
    gad_campaignid = Column(String(128), default="")  # Google Ads campaign id
    invite_code = Column(String(64), default="")
    referrer_user_id = Column(Integer, nullable=True)
    ad_id = Column(String(128), default="")  # IDFA / GAID（用户授权后）
    ad_id_type = Column(String(16), default="")  # idfa / gaid / none
    idfv = Column(String(64), default="")
    android_id = Column(String(64), default="")
    network = Column(String(16), default="")  # wifi / cellular / unknown
    push_enabled = Column(Boolean, nullable=True)
    login_method = Column(String(20), default="")  # email / google / apple
    app_version = Column(String(32), default="")
    os = Column(String(16), default="")  # ios / android / web
    os_version = Column(String(32), default="")
    device_model = Column(String(64), default="")
    locale = Column(String(16), default="")
    timezone = Column(String(64), default="")
    storefront_country = Column(String(8), default="")
    ip = Column(String(64), default="")
    ip_country = Column(String(8), default="")
    first_ip = Column(String(64), default="")  # 仅 install 事件写入，用于首触达
    first_country = Column(String(8), default="")
    attribution_provider = Column(String(32), default="")  # firebase/zeasads/invite/play_referrer/organic/manual
    app_env = Column(String(16), default="")  # prod / test
    app_key = Column(String(32), default="trandsai", index=True)  # 马甲：trandsai / kaya
    product_event = Column(String(64), default="")  # event_type=product 时的产品事件名
    firebase_event_name = Column(String(128), default="")  # GA4 事件名镜像
    companion_id = Column(String(8), default="", index=True)  # 产品埋点关联智能体（顶层/firebase/raw 解析）
    firebase_event_params = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    raw_payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    __table_args__ = (
        Index("idx_attr_event_type_created", "event_type", "created_at"),
        Index("idx_attr_device_event_created", "device_id", "event_type", "created_at"),
        Index("idx_attr_product_event_created", "product_event", "created_at"),
        Index("idx_attr_utm_campaign", "utm_campaign"),
        Index("idx_attr_install_source", "install_source"),
        Index("idx_attr_gclid", "gclid"),
        Index("idx_attr_gad_campaignid", "gad_campaignid"),
        Index("idx_attr_app_key", "app_key"),
    )


class StatsDauSnapshotORM(Base):
    """日活快照：按日历日 × app_key 固化，避免事后回填导致历史数漂移。

    仅冻结「已结束」的 UTC 日；当天仍实时计算。
    active_users = 去重 user_id + 当日全程匿名的去重 device_id。
    """
    __tablename__ = "stats_dau_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    stat_date = Column(Date, nullable=False, index=True)
    app_key = Column(String(32), nullable=False, default="trandsai", index=True)
    open_devices = Column(Integer, nullable=False, default=0)
    login_uids = Column(Integer, nullable=False, default=0)
    active_users = Column(Integer, nullable=False, default=0)
    new_users = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    __table_args__ = (
        UniqueConstraint("stat_date", "app_key", name="uq_stats_dau_date_app"),
        Index("idx_stats_dau_date_app", "stat_date", "app_key"),
    )


# ===== 支付 & 积分系统 =====

class UserCreditORM(Base):
    """用户积分/会员状态表"""
    __tablename__ = "user_credits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, unique=True, index=True)
    balance = Column(Integer, default=0)  # 充值积分余额
    daily_gift_remaining = Column(Integer, default=0)  # 今日剩余赠送积分
    daily_gift_total = Column(Integer, default=0)  # 当前档位每日赠送总量
    vip_type = Column(String(20), default="")  # weekly/monthly/yearly/lifetime/空
    vip_expire_at = Column(DateTime, nullable=True)  # VIP到期时间
    vip_started_at = Column(DateTime, nullable=True)  # VIP开通时间
    free_turns_used = Column(Integer, default=0)  # 账号累计已用免费轮次（注册以来）
    say_hi_daily_used = Column(Integer, default=0)  # 今日已用 Say Hi 免费次数
    say_hi_daily_date = Column(String(10), default="")  # Say Hi 日切日期 YYYY-MM-DD（UTC）
    max_agents = Column(Integer, default=0)  # 当前可持有智能体数量
    last_daily_gift_date = Column(String(10), default="")  # 上次发放每日赠送的日期 YYYY-MM-DD
    last_ip = Column(String(64), default="")  # 最近一次客户端 IP
    last_device = Column(String(512), default="")  # 最近一次设备标识 / User-Agent
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CreditTransactionORM(Base):
    """积分消费账单表"""
    __tablename__ = "credit_transactions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    event_type = Column(String(50), nullable=False)  # chat/create_agent/agent_daily/vip_gift/recharge/vip_subscribe
    amount = Column(Integer, nullable=False)  # 正=获得，负=消耗
    balance_after = Column(Integer, nullable=False)  # 操作后余额
    vip_type = Column(String(20), default="")  # 操作时的VIP类型
    description = Column(String(200), default="")  # 描述
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class VipSubscriptionORM(Base):
    """VIP订阅记录表"""
    __tablename__ = "vip_subscriptions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    plan_type = Column(String(20), nullable=False)  # weekly/monthly/yearly/lifetime
    price_usd = Column(Float, default=0)  # 价格(美元)
    store_type = Column(String(20), nullable=False)  # app_store/google_play
    store_transaction_id = Column(String(512), nullable=False, unique=True, index=True)  # 商店交易ID/Google purchase_token（去重+续期查找）
    store_receipt = Column(Text, default="")  # 原始收据JSON
    daily_credits = Column(Integer, default=0)  # 每日赠送积分
    max_agents = Column(Integer, default=0)  # 可持有智能体数量
    start_at = Column(DateTime, nullable=True)  # 开始时间
    expire_at = Column(DateTime, nullable=True)  # 到期时间
    auto_renew = Column(Boolean, default=True)  # 是否自动续费
    status = Column(String(20), default="active")  # active/expired/cancelled/refunded
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class RechargeRecordORM(Base):
    """充值记录表"""
    __tablename__ = "recharge_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    package_key = Column(String(30), nullable=False)  # pack_250/pack_550/pack_800/pack_1900
    credits = Column(Integer, nullable=False)  # 获得积分
    price_usd = Column(Float, default=0)  # 价格(美元)
    store_type = Column(String(20), nullable=False)  # app_store/google_play
    store_transaction_id = Column(String(512), nullable=False, unique=True, index=True)  # 商店交易ID/Google purchase_token（去重）
    store_receipt = Column(Text, default="")  # 原始收据JSON
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PendingOrderORM(Base):
    """预订单表（回调创建，等待前端验证确认）"""
    __tablename__ = "pending_orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String(255), nullable=False, unique=True, index=True)  # 苹果/谷歌交易ID
    original_transaction_id = Column(String(255), default="")  # 原始交易ID（续费时不变）
    product_id = Column(String(100), nullable=False)  # 商品ID（apple_product_id 或 store_product_id）
    product_type = Column(String(20), nullable=False)  # subscribe / point
    plan_type = Column(String(20), default="")  # weekly/monthly/yearly/lifetime
    package_key = Column(String(100), default="")  # 积分包key
    price_usd = Column(Float, default=0)  # 价格（美元）
    credits_amount = Column(Integer, default=0)  # 积分数量
    duration_days = Column(Integer, default=0)  # 订阅天数
    store_type = Column(String(20), nullable=False)  # app_store / google_play
    status = Column(String(20), default="pending", index=True)  # pending / completed / failed / expired
    user_id = Column(Integer, nullable=True, index=True)  # 前端验证时填充
    callback_data = Column(Text, default="")  # 原始回调数据JSON
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_pending_orders_transaction_id", "transaction_id"),
        Index("idx_pending_orders_status", "status"),
        Index("idx_pending_orders_user_id", "user_id"),
    )


class MomentCommentReviewORM(Base):
    """朋友圈评论审核表"""
    __tablename__ = "moment_comment_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(Integer, nullable=False, index=True)  # FK→moment_comments.id
    user_id = Column(Integer, nullable=False, index=True)  # 评论者ID
    status = Column(String(20), default="pending")  # pending/approved/rejected
    reject_reason = Column(String(200), default="")  # 拒绝原因
    reviewed_by = Column(String(50), default="")  # 审核人（admin/manual/auto）
    reviewed_at = Column(DateTime, nullable=True)  # 审核时间
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CompanionReportORM(Base):
    """智能体举报审核池：对话举报 + 资料（背景图）举报共用一张表 / 同一管理端列表。

    - scene=chat：举报某条对话；通过后该消息 is_hidden，C 端不展示
    - scene=companion：举报资料背景图；通过后清空 profile_backgrounds
    """
    __tablename__ = "companion_reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    reporter_user_id = Column(Integer, nullable=False, index=True)
    companion_id = Column(String(8), nullable=False, index=True)
    reason_id = Column(Integer, nullable=True, index=True)  # 理由分类 id（与 report-reasons 列表 id 对应）
    reason_code = Column(String(32), nullable=False, index=True)
    remark = Column(Text, default="")  # 用户补充说明
    content = Column(Text, default="")  # 被举报内容原文（如聊天文本）
    message_id = Column(Integer, nullable=True, index=True)  # scene=chat 时被举报的 short_term_messages.id
    scene = Column(String(20), default="companion")  # companion=背景图资料 / chat=对话
    source = Column(String(20), default="profile")  # profile / chat / moments（入口页）
    status = Column(String(20), default="pending", index=True)  # pending / approved / rejected
    priority = Column(String(4), default="P1")  # P0 / P1
    auto_hidden = Column(Boolean, default=False)  # 新逻辑不再因举报整卡隐藏智能体
    companion_status_before = Column(String(20), default="published")
    review_note = Column(Text, default="")
    reviewed_by = Column(String(64), default="")
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_companion_reports_companion_status", "companion_id", "status"),
        Index("idx_companion_reports_reporter_companion", "reporter_user_id", "companion_id"),
    )


class CompanionProfileReviewORM(Base):
    """智能体资料审核：用户上传的 profile_backgrounds 先入待审，通过后才写入 companions 公开字段。"""
    __tablename__ = "companion_profile_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    companion_id = Column(String(8), nullable=False, index=True)
    submitter_user_id = Column(Integer, nullable=True, index=True)
    pending_backgrounds = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    current_backgrounds = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status = Column(String(20), default="pending", index=True)  # pending / approved / rejected
    reject_reason = Column(String(200), default="")
    reject_reason_id = Column(Integer, nullable=True)
    reviewed_by = Column(String(64), default="")
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_companion_profile_reviews_cid_status", "companion_id", "status"),
    )


class UserReportORM(Base):
    """用户资料举报审核池。"""
    __tablename__ = "user_reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    reporter_user_id = Column(Integer, nullable=False, index=True)
    reported_user_id = Column(Integer, nullable=False, index=True)
    reason_id = Column(Integer, nullable=True, index=True)
    reason_code = Column(String(32), nullable=False, index=True)
    remark = Column(Text, default="")
    content = Column(Text, default="")
    source = Column(String(20), default="profile")  # profile / chat / moments
    status = Column(String(20), default="pending", index=True)  # pending / approved / rejected
    priority = Column(String(4), default="P1")
    auto_hidden = Column(Boolean, default=True)
    account_status_before = Column(String(20), default="active")
    review_note = Column(Text, default="")
    reviewed_by = Column(String(64), default="")
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_user_reports_reported_status", "reported_user_id", "status"),
        Index("idx_user_reports_reporter_reported", "reporter_user_id", "reported_user_id"),
    )


class PostReportORM(Base):
    """帖子举报审核池。"""
    __tablename__ = "post_reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    reporter_user_id = Column(Integer, nullable=False, index=True)
    post_id = Column(Integer, nullable=False, index=True)
    reason_id = Column(Integer, nullable=True, index=True)
    reason_code = Column(String(32), nullable=False, index=True)
    remark = Column(Text, default="")
    content = Column(Text, default="")
    source = Column(String(20), default="feed")  # feed / detail / profile
    status = Column(String(20), default="pending", index=True)  # pending / approved / rejected
    priority = Column(String(4), default="P1")
    auto_hidden = Column(Boolean, default=True)
    post_status_before = Column(String(20), default="approved")
    review_note = Column(Text, default="")
    reviewed_by = Column(String(64), default="")
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_post_reports_post_status", "post_id", "status"),
        Index("idx_post_reports_reporter_post", "reporter_user_id", "post_id"),
    )


class OutreachJobORM(Base):
    """运营触达任务：点赞/评论后延迟私聊等"""
    __tablename__ = "outreach_jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String(32), nullable=False, index=True)  # like_dm / comment_dm / ...
    user_id = Column(Integer, nullable=False, index=True)
    companion_id = Column(String(8), nullable=False, index=True)
    moment_id = Column(Integer, nullable=True, index=True)
    comment_id = Column(Integer, nullable=True, index=True)
    status = Column(String(20), default="pending", index=True)  # pending/processing/sent/cancelled/skipped
    execute_at = Column(DateTime, nullable=False, index=True)
    payload = Column(JSON, default=dict)
    skip_reason = Column(String(200), default="")
    result_text = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    sent_at = Column(DateTime, nullable=True)
    __table_args__ = (
        Index("idx_outreach_status_execute", "status", "execute_at"),
        Index("idx_outreach_user_type_status", "user_id", "job_type", "status"),
    )


class DevicePushTokenORM(Base):
    """用户设备推送 token（FCM / APNs / UniPush clientId）"""
    __tablename__ = "device_push_tokens"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    platform = Column(String(20), nullable=False, default="android")  # android / ios
    provider = Column(String(20), nullable=False, default="fcm")  # fcm / apns / unipush
    token = Column(String(512), nullable=False, index=True)
    device_id = Column(String(64), default="", index=True)
    app_version = Column(String(32), default="")
    enabled = Column(Boolean, default=True)
    last_seen_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("uniq_push_token", "provider", "token", unique=True),
    )


class ChatVersionPromptORM(Base):
    """客户端版本号 → 聊天兜底提示词（最高权重前缀）。"""
    __tablename__ = "chat_version_prompts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(32), nullable=False, unique=True, index=True)
    prompt = Column(Text, nullable=False, default="")
    is_enabled = Column(Boolean, default=False, nullable=False, index=True)
    remark = Column(String(200), default="")
    sort_order = Column(Integer, default=0)
    # 仅对注册时间落在此闭区间内的用户生效；起/止都空 = 不限注册批次
    apply_registered_start_at = Column(DateTime, nullable=True)
    apply_registered_end_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ChatVersionPromptUserORM(Base):
    """从某条版本提示词合规名单中永久移除的用户。"""
    __tablename__ = "chat_version_prompt_users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    version = Column(String(32), default="")
    removed_by = Column(String(64), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        UniqueConstraint("prompt_id", "user_id", name="uq_chat_ver_prompt_user"),
        Index("idx_chat_ver_prompt_user_prompt", "prompt_id", "user_id"),
    )


class CompanionPrivateAlbumPhotoORM(Base):
    """智能体 Our Moments 私密相册照片（运营配置）。"""
    __tablename__ = "companion_private_album_photos"
    id = Column(Integer, primary_key=True, autoincrement=True)
    companion_id = Column(String(8), nullable=False, index=True)
    image_url = Column(String(500), nullable=False, default="")
    unlock_x = Column(Integer, nullable=False, default=5)
    countdown_text = Column(String(200), default="")
    success_text = Column(String(200), default="")
    sort_order = Column(Integer, nullable=False, default=0)
    is_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    __table_args__ = (
        Index("idx_private_album_companion_sort", "companion_id", "sort_order", "id"),
    )


class CompanionPrivateAlbumProgressORM(Base):
    """用户 × 智能体 私密相册解锁进度。"""
    __tablename__ = "companion_private_album_progress"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    companion_id = Column(String(8), nullable=False, index=True)
    unlocked_photo_ids = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    current_photo_id = Column(Integer, nullable=True)
    current_unlock_x = Column(Integer, nullable=False, default=5)
    progress_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    __table_args__ = (
        UniqueConstraint("user_id", "companion_id", name="uq_private_album_user_companion"),
        Index("idx_private_album_progress_user", "user_id", "companion_id"),
    )


class UserOutreachPreferenceORM(Base):
    """用户对角色的触达偏好：block=永久屏蔽 / dislike=降权不感兴趣"""
    __tablename__ = "user_outreach_preferences"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    companion_id = Column(String(8), nullable=False, index=True)
    action = Column(String(20), nullable=False, default="dislike")  # block / dislike
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("uniq_user_companion_outreach_pref", "user_id", "companion_id", unique=True),
    )


# ===== 充值福利系统 =====

class BenefitProductORM(Base):
    """福利商品表（订阅 + 金币充值共用）"""
    __tablename__ = "benefit_products"
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_type = Column(String(20), nullable=False, index=True)  # subscribe / point
    platform = Column(String(20), default="all", index=True)  # 支付平台：apple / google / all
    app_key = Column(String(32), default="trandsai", index=True)  # 应用：trandsai / kaya
    title = Column(String(100), nullable=False)  # 商品标题，如"年度订阅"、"550金币"
    period = Column(String(20), default="")  # 订阅周期：yearly/monthly/weekly/daily/lifetime，金币为空
    price_usd = Column(Float, nullable=False)  # 实际价格
    discount_pct = Column(Integer, default=0)  # 折扣百分比（展示用，不参与计算）
    credits_amount = Column(Integer, default=0)  # 金币专用：金币数量
    daily_gift_credits = Column(Integer, default=0)  # 订阅专用：每日赠送金币数
    max_agents = Column(Integer, default=0)  # 订阅专用：agent 数量
    daily_avg_price = Column(Float, default=0)  # 订阅专用：会员日均价格（展示用）
    store_product_id = Column(String(100), default="")  # Google Play 商品 ID
    apple_product_id = Column(String(100), default="")  # App Store 商品 ID（预留）
    sort_order = Column(Integer, default=0)  # 排序权重（越小越靠前）
    is_active = Column(Boolean, default=True)  # 是否上架
    # 限时优惠：金币/订阅商品均可标记；由套餐模板引用后走独立用户接口
    is_limited_offer = Column(Boolean, default=False, index=True)
    limited_duration_sec = Column(Integer, default=0)  # 弹窗有效秒数，1–3600；0=非限时
    extra = Column(Text, default="{}")  # JSON 扩展字段（预留）
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_benefit_products_type", "product_type"),
        Index("idx_benefit_products_platform", "platform"),
        Index("idx_benefit_products_app_key", "app_key"),
    )


class AppleAppAccountTokenORM(Base):
    """Apple 收银台 UUID（appAccountToken / applicationUsername）→ user_id。"""
    __tablename__ = "apple_app_account_tokens"
    app_account_token = Column(String(36), primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_apple_app_account_tokens_user", "user_id"),
    )


class ProductPriceORM(Base):
    """商店各地区标价（管理端同步 / 用户端展示回退）。"""
    __tablename__ = "product_prices"
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, nullable=False, index=True)
    territory = Column(String(8), nullable=False, default="")
    currency = Column(String(8), default="USD")
    price = Column(Float, default=0)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("uniq_product_price_territory", "product_id", "territory", unique=True),
        Index("idx_product_prices_product", "product_id"),
    )


class UserLimitedOfferORM(Base):
    """用户限时优惠会话：付费墙关闭/支付失败后开始倒计时，服务端时间为准。"""
    __tablename__ = "user_limited_offers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    app_key = Column(String(32), nullable=False, default="trandsai", index=True)
    product_id = Column(Integer, nullable=True)
    first_paywall_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    used_at = Column(DateTime, nullable=True)
    last_scene = Column(String(20), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        UniqueConstraint("user_id", "app_key", name="uniq_user_limited_offer_app"),
    )


class BenefitProductTitleORM(Base):
    """商品多国家展示名。region_code 与模板区域一致（US/JP/CN/ALL）。"""
    __tablename__ = "benefit_product_titles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, nullable=False, index=True)  # FK → benefit_products.id
    region_code = Column(String(10), nullable=False)  # US / JP / ALL
    title = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("uniq_benefit_product_title_region", "product_id", "region_code", unique=True),
    )


class BenefitTemplateORM(Base):
    """福利模板表 — 控制商品组合 + 区域 + 用户群的可见性"""
    __tablename__ = "benefit_templates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)  # 模板名称，如"US-年度促销"
    description = Column(String(300), default="")  # 模板描述
    app_key = Column(String(32), default="trandsai", index=True)  # 应用：trandsai / kaya
    user_id_rule = Column(Text, default="")  # 用户ID筛选规则（JSON），空=不限
    priority = Column(Integer, default=0)  # 优先级（同一区域多模板时高优先级覆盖低优先级）
    is_active = Column(Boolean, default=True)  # 是否启用
    start_at = Column(DateTime, nullable=True)  # 生效开始时间，NULL=立即
    end_at = Column(DateTime, nullable=True)  # 生效结束时间，NULL=永久
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("idx_benefit_templates_app_key", "app_key"),
    )


class BenefitTemplateProductORM(Base):
    """模板-商品关联表（多对多）"""
    __tablename__ = "benefit_template_products"
    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, nullable=False, index=True)  # FK → benefit_templates.id
    product_id = Column(Integer, nullable=False, index=True)  # FK → benefit_products.id
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("uniq_template_product", "template_id", "product_id", unique=True),
    )


class BenefitTemplateRegionORM(Base):
    """模板-区域关联表（多对多）"""
    __tablename__ = "benefit_template_regions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, nullable=False, index=True)  # FK → benefit_templates.id
    region_code = Column(String(10), nullable=False)  # 区域代码：US/CN/SEA/EU/ALL
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("uniq_template_region", "template_id", "region_code", unique=True),
    )


class RecommendTagDefORM(Base):
    """推荐标签字典。主键 (key, side)，同一 key 可同时出现在 user 与 agent。"""
    __tablename__ = "recommend_tag_defs"
    key = Column(String(64), nullable=False)
    side = Column(String(8), nullable=False)  # user | agent
    group = Column(String(32), nullable=False)
    label_zh = Column(String(100), default="")
    label_en = Column(String(100), default="")
    enabled = Column(Boolean, default=True)
    source = Column(String(16), default="ops")  # seed | ua_auto | ops
    __table_args__ = (
        PrimaryKeyConstraint("key", "side", name="pk_recommend_tag_defs"),
    )


class RecommendStrategyORM(Base):
    __tablename__ = "recommend_strategies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, default="")
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=0)
    apply_to = Column(String(32), nullable=False, default="both")  # companions_all | companions_recommend | moments | both
    user_tags = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    agent_tags = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    user_match = Column(String(8), default="any")
    agent_match = Column(String(8), default="any")
    created_by = Column(String(64), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        CheckConstraint(
            "NOT (user_tags = '[]'::jsonb AND agent_tags = '[]'::jsonb)",
            name="ck_recommend_strategies_not_both_empty",
        ),
        CheckConstraint(
            "apply_to IN ('companions_all', 'companions_recommend', 'moments', 'both', 'companions')",
            name="ck_recommend_strategies_apply_to",
        ),
    )


class RecommendUaPendingORM(Base):
    """ZeasAds 回写时用户尚未注册，注册时按 product_user_id 合并。"""
    __tablename__ = "recommend_ua_pending"
    product_user_id = Column(String(128), primary_key=True)
    ua_tags = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class UaTagMapORM(Base):
    """可选：把 ua:channel:… 等映射成业务 key，运行时附加，不删原始 ua:*。"""
    __tablename__ = "ua_tag_maps"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_kind = Column(String(32), nullable=False)
    source_value = Column(String(128), nullable=False)
    tag_key = Column(String(64), nullable=False)
    __table_args__ = (
        Index("uniq_ua_tag_map", "source_kind", "source_value", "tag_key", unique=True),
    )


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
    """检查并添加缺失的数据库列（兼容已有表，PostgreSQL）"""
    # 启动性能关键点：不要用 inspect(engine)+get_columns() 做“先查再改”，
    # 这一步在远程 PostgreSQL 上可能会非常慢/卡住。
    # Postgres 支持 ADD COLUMN IF NOT EXISTS，直接 DDL 即可。
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            # 避免远程 PostgreSQL 上 DDL 被锁/网络异常导致“永久卡死”
            # 如果不支持这些参数，SET 会失败但不会中断 alter 本身。
            try:
                # statement_timeout/lock_timeout 在 PostgreSQL 中的单位是毫秒（ms）
                conn.execute(text("SET statement_timeout = 15000"))
                conn.execute(text("SET lock_timeout = 15000"))
            except Exception:
                pass

            print(
                f"[init_db] _ensure_column exec {table_name}.{column_name} ...",
                flush=True,
            )
            conn.execute(
                text(
                    f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} {column_def}"
                )
            )
            conn.commit()
            print(
                f"[init_db] _ensure_column commit {table_name}.{column_name}",
                flush=True,
            )
            print(f"[init_db] _ensure_column ok {table_name}.{column_name}", flush=True)
        logger.info("[init_db] Ensure column %s.%s done", table_name, column_name)
    except Exception as e:
        # 非致命：例如表不存在、权限不足等；会在 create_all 失败时更明显。
        logger.warning("[init_db] Ensure column %s.%s failed: %s", table_name, column_name, e)
        print(f"[init_db] _ensure_column failed {table_name}.{column_name}: {e}", flush=True)


def _ensure_gin_index(table_name: str, index_name: str, column_name: str):
    """JSONB GIN（jsonb_ops，支持 ?|）。"""
    with engine.connect() as conn:
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} USING GIN ({column_name} jsonb_ops)"
        ))
        conn.commit()
    logger.info("[init_db] 添加 GIN 索引 %s.%s", table_name, index_name)


def _ensure_check_constraint(table_name: str, name: str, expr: str):
    """已有表补 CHECK（create_all 不会给旧表加约束）。"""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)
    if table_name not in inspector.get_table_names():
        return
    try:
        existing = {c["name"] for c in inspector.get_check_constraints(table_name)}
    except Exception:
        existing = set()
    if name in existing:
        return
    try:
        with engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {table_name} ADD CONSTRAINT {name} CHECK ({expr})"))
            conn.commit()
        logger.info("[init_db] 添加 CHECK %s.%s", table_name, name)
    except Exception as e:
        logger.warning("[init_db] 添加 CHECK %s.%s 失败: %s", table_name, name, e)


def _replace_check_constraint(table_name: str, name: str, expr: str):
    """DROP + ADD，用于更新已有 CHECK 表达式。"""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)
    if table_name not in inspector.get_table_names():
        return
    try:
        with engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {name}"))
            conn.execute(text(f"ALTER TABLE {table_name} ADD CONSTRAINT {name} CHECK ({expr})"))
            conn.commit()
        logger.info("[init_db] 更新 CHECK %s.%s", table_name, name)
    except Exception as e:
        logger.warning("[init_db] 更新 CHECK %s.%s 失败: %s", table_name, name, e)


def _ensure_unique_index(table_name: str, index_name: str, columns: list[str]):
    """检查并添加唯一索引（兼容已有表）"""
    from sqlalchemy import text

    quoted_cols = ", ".join(columns)
    with engine.connect() as conn:
        conn.execute(
            text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_name} ({quoted_cols})"
            )
        )
        conn.commit()
    logger.info("[init_db] 添加唯一索引 %s.%s", table_name, index_name)


def _ensure_index(table_name: str, index_name: str, columns: list[str]):
    """检查并添加普通索引（兼容已有表）"""
    from sqlalchemy import text

    quoted_cols = ", ".join(columns)
    with engine.connect() as conn:
        conn.execute(
            text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({quoted_cols})")
        )
        conn.commit()
    logger.info("[init_db] 添加索引 %s.%s", table_name, index_name)


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

        # 使用ALTER COLUMN修改列类型（PostgreSQL）
        with engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} TYPE {new_type}"))
            conn.commit()
        logger.info("[init_db] 修改列类型: %s.%s -> %s", table_name, column_name, new_type)
    except Exception as e:
        logger.warning("[init_db] 修改列类型失败 %s.%s: %s", table_name, column_name, e)


def _ensure_float_column(table_name: str, column_name: str):
    """存量 INTEGER 亲密度列改为 DOUBLE PRECISION，否则 0.65 会被截成 0。"""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = :t AND column_name = :c"
                ),
                {"t": table_name, "c": column_name},
            ).fetchone()
            if not row:
                return
            dtype = str(row[0] or "").lower()
            if dtype not in ("integer", "bigint", "smallint"):
                return
            conn.execute(text("SET statement_timeout = 15000"))
            conn.execute(text("SET lock_timeout = 15000"))
            conn.execute(
                text(
                    f"ALTER TABLE {table_name} ALTER COLUMN {column_name} "
                    f"TYPE DOUBLE PRECISION USING {column_name}::double precision"
                )
            )
            conn.commit()
        logger.info("[init_db] %s.%s INTEGER → DOUBLE PRECISION", table_name, column_name)
    except Exception as e:
        logger.warning("[init_db] %s.%s 改为 FLOAT 失败: %s", table_name, column_name, e)


def init_db():
    import time as _t

    t0 = _t.monotonic()
    print("[init_db] start", flush=True)
    print("[init_db] Base.metadata.create_all start", flush=True)
    Base.metadata.create_all(bind=engine)
    print(f"[init_db] Base.metadata.create_all done {(_t.monotonic()-t0)*1000:.1f}ms", flush=True)
    # _ensure_column("moment_likes", "user_id", "INTEGER")
    # 兼容：为已存在的 companion_states 表添加进化字段
    print("[init_db] ensure companion_states.evolved_personality ...", flush=True)
    _ensure_column("companion_states", "evolved_personality", "TEXT")
    print("[init_db] ensure companion_states.evolved_background ...", flush=True)
    _ensure_column("companion_states", "evolved_background", "TEXT")
    print("[init_db] ensure companion_states.evolved_speech_style ...", flush=True)
    _ensure_column("companion_states", "evolved_speech_style", "TEXT")
    # 兼容：为已存在的 companions 表添加立体人格字段
    print("[init_db] ensure companions appearance/hobbies/... ...", flush=True)
    print("[init_db] ensure companions.appearance", flush=True)
    _ensure_column("companions", "appearance", "TEXT")
    print("[init_db] ensure companions.hobbies", flush=True)
    _ensure_column("companions", "hobbies", "TEXT")
    print("[init_db] ensure companions.values", flush=True)
    _ensure_column("companions", "values", "TEXT")
    print("[init_db] ensure companions.fears", flush=True)
    _ensure_column("companions", "fears", "TEXT")
    _ensure_column("companions", "love_view", "TEXT")
    _ensure_column("companions", "daily_routine", "TEXT")
    _ensure_column("companions", "favorite_things", "TEXT")
    _ensure_column("companions", "life_story", "TEXT")
    _ensure_column("companions", "sexual_orientation", "TEXT")
    _ensure_column("users", "sexual_orientation", "TEXT")
    _ensure_column("users", "avatar_url", "VARCHAR(500) DEFAULT ''")
    _ensure_column("users", "birth", "DATE")
    _ensure_column("companions", "cultural_values", "TEXT")
    _ensure_column("companions", "gender_perspective", "TEXT")
    _ensure_column("companions", "avatar_url", "TEXT")
    _ensure_column("companions", "created_by", "VARCHAR(64) DEFAULT ''")
    _ensure_column("companions", "language", "VARCHAR(10) DEFAULT 'zh'")
    _ensure_column("posts", "category", "VARCHAR(50) DEFAULT ''")
    _ensure_column("posts", "language", "VARCHAR(10) DEFAULT 'zh'")
    _ensure_column("posts", "review_status", "VARCHAR(20) DEFAULT 'approved'")
    _ensure_column("posts", "reject_reason", "VARCHAR(200) DEFAULT ''")
    _ensure_column("posts", "reject_reason_id", "INTEGER")
    _ensure_column("posts", "reviewed_by", "VARCHAR(50) DEFAULT ''")
    _ensure_column("posts", "reviewed_at", "TIMESTAMP")
    _ensure_column("user_profile_reviews", "reject_reason_id", "INTEGER")
    _ensure_column("system_notifications", "user_id", "INTEGER")
    _ensure_column("system_notifications", "companion_id", "VARCHAR(8)")
    _ensure_column("system_notifications", "moment_id", "INTEGER")
    _ensure_column("users", "timezone", "VARCHAR(64) DEFAULT ''")
    _ensure_column("users", "language", "VARCHAR(10) DEFAULT 'en'")
    _ensure_float_column("user_companion_states", "affection")
    _ensure_float_column("companion_states", "affection")
    _ensure_column("companions", "tier", "VARCHAR(20) DEFAULT 'featured'")
    _ensure_column("companions", "pre_ops_tier", "VARCHAR(20)")
    _ensure_column("companions", "tags", "JSONB NOT NULL DEFAULT '[]'::jsonb")
    _ensure_column("users", "tags", "JSONB NOT NULL DEFAULT '[]'::jsonb")
    _ensure_column("users", "ua_tags", "JSONB NOT NULL DEFAULT '[]'::jsonb")
    _ensure_gin_index("companions", "ix_companions_tags_gin", "tags")
    _alter_column_length("recommend_strategies", "apply_to", "VARCHAR(32)")
    _ensure_check_constraint(
        "recommend_strategies",
        "ck_recommend_strategies_not_both_empty",
        "NOT (user_tags = '[]'::jsonb AND agent_tags = '[]'::jsonb)",
    )
    _replace_check_constraint(
        "recommend_strategies",
        "ck_recommend_strategies_apply_to",
        "apply_to IN ('companions_all', 'companions_recommend', 'moments', 'both', 'companions')",
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("UPDATE companions SET tags = '[]'::jsonb WHERE tags IS NULL"))
            conn.execute(text("UPDATE users SET tags = '[]'::jsonb WHERE tags IS NULL"))
            conn.execute(text("UPDATE users SET ua_tags = '[]'::jsonb WHERE ua_tags IS NULL"))
            conn.commit()
    except Exception as e:
        logger.warning("[init_db] tags NULL→[] 失败: %s", e)
    _ensure_column("companions", "region_label", "VARCHAR(64) DEFAULT ''")
    _ensure_column("companions", "description", "TEXT DEFAULT ''")
    # 存量：description 空时用 personality 回填（短简介语义，勿用 background）
    try:
        from sqlalchemy import text as _text_desc
        with engine.connect() as conn:
            conn.execute(_text_desc(
                "UPDATE companions SET description = personality "
                "WHERE (description IS NULL OR description = '') "
                "AND personality IS NOT NULL AND personality <> ''"
            ))
            conn.commit()
        logger.info("[init_db] companions.description 已从 personality 回填空值")
    except Exception as e:
        logger.warning("[init_db] companions.description 回填失败: %s", e)
    # 历史帖子默认视为已通过，避免发现页被清空
    try:
        from sqlalchemy import text as _text
        with engine.connect() as conn:
            conn.execute(_text(
                "UPDATE posts SET review_status = 'approved' "
                "WHERE review_status IS NULL OR review_status = ''"
            ))
            conn.commit()
    except Exception as e:
        logger.warning("[init_db] 回填 posts.review_status 失败: %s", e)
    # 兼容：moment_comments 表新增 user_device_id 字段
    _ensure_column("moment_comments", "user_device_id", "VARCHAR(64)")
    # 兼容：moment_comments 表新增 parent_id 字段（评论回复关系）
    _ensure_column("moment_comments", "parent_id", "INTEGER")
    _ensure_column("moments", "caption_lang", "VARCHAR(10)")
    _ensure_column("moments", "preview_url", "VARCHAR(500)")
    _ensure_column("moments", "preview_pos", "JSONB")
    _ensure_column("moments", "view_count", "INTEGER DEFAULT 0")
    _ensure_column("moments", "view_users", "INTEGER DEFAULT 0")
    _ensure_column("moments", "detail_click_count", "INTEGER DEFAULT 0")
    _ensure_column("moments", "detail_click_users", "INTEGER DEFAULT 0")
    _ensure_column("moments", "schedule_lang", "VARCHAR(10)")
    _ensure_column("moments", "schedule_at_local", "VARCHAR(32)")
    _ensure_column("moments", "publish_at", "TIMESTAMP")
    _ensure_column("feedback_threads", "welcome_sent", "BOOLEAN DEFAULT FALSE")
    _ensure_column("feedback_threads", "last_user_msg_at", "TIMESTAMP")
    _ensure_column("feedback_threads", "auto_reply_after_user_at", "TIMESTAMP")
    _ensure_column("feedback_threads", "user_last_read_at", "TIMESTAMP")
    _ensure_column("feedback_threads", "admin_last_read_at", "TIMESTAMP")
    _ensure_column("companions", "is_active", "BOOLEAN DEFAULT TRUE")
    _ensure_column("companions", "moderation_status", "VARCHAR(20) DEFAULT 'published'")
    _ensure_column("companion_reports", "reason_id", "INTEGER")
    _ensure_column("companion_reports", "content", "TEXT DEFAULT ''")
    _ensure_column("companion_reports", "scene", "VARCHAR(20) DEFAULT 'companion'")
    _ensure_column("companion_reports", "message_id", "INTEGER")
    _ensure_column("short_term_messages", "is_hidden", "BOOLEAN DEFAULT FALSE")
    _ensure_column("short_term_messages", "send_status", "VARCHAR(20) DEFAULT ''")
    _ensure_column("companions", "i18n_profile", "JSONB NOT NULL DEFAULT '{}'::jsonb")
    _ensure_column("companions", "avatar_style", "VARCHAR(64) DEFAULT ''")
    _ensure_column(
        "companions",
        "profile_backgrounds",
        "JSONB NOT NULL DEFAULT '[]'::jsonb",
    )
    _ensure_column(
        "users",
        "profile_backgrounds",
        "JSONB NOT NULL DEFAULT '[]'::jsonb",
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("UPDATE companions SET i18n_profile = '{}'::jsonb WHERE i18n_profile IS NULL"))
            conn.commit()
    except Exception as e:
        logger.warning("[init_db] companions.i18n_profile NULL→{} 失败: %s", e)
    try:
        CompanionStrategyORM.__table__.create(bind=engine, checkfirst=True)
    except Exception as e:
        logger.warning("[init_db] companion_strategies 表创建失败: %s", e)
    try:
        CompanionQuickReplyORM.__table__.create(bind=engine, checkfirst=True)
    except Exception as e:
        logger.warning("[init_db] companion_quick_replies 表创建失败: %s", e)
    for _tbl, _name in (
        (QuickReplyLibraryORM, "quick_reply_library"),
        (QuickReplyConfigORM, "quick_reply_configs"),
        (ImReplySettingsORM, "im_reply_settings"),
        (ImHumanFirstPendingORM, "im_human_first_pending"),
        (StatsDauSnapshotORM, "stats_dau_snapshots"),
        (ChatVersionPromptORM, "chat_version_prompts"),
        (ChatVersionPromptUserORM, "chat_version_prompt_users"),
        (CompanionPrivateAlbumPhotoORM, "companion_private_album_photos"),
        (CompanionPrivateAlbumProgressORM, "companion_private_album_progress"),
    ):
        try:
            _tbl.__table__.create(bind=engine, checkfirst=True)
        except Exception as e:
            logger.warning("[init_db] %s 表创建失败: %s", _name, e)
    try:
        from services.quick_reply import backfill_quick_reply_configs

        with get_db() as _qr_db:
            n = backfill_quick_reply_configs(_qr_db)
        if n:
            logger.info("[init_db] quick_reply_configs backfill +%s", n)
    except Exception as e:
        logger.warning("[init_db] quick_reply_configs backfill 失败: %s", e)
    try:
        StrategyTypeORM.__table__.create(bind=engine, checkfirst=True)
    except Exception as e:
        logger.warning("[init_db] strategy_types 表创建失败: %s", e)
    for _tbl, _name in (
        (MsgStrategyGlobalORM, "msg_strategy_globals"),
        (MsgStrategyRuleORM, "msg_strategy_rules"),
        (MsgStrategyLibraryORM, "msg_strategy_library"),
        (MsgStrategyDispatchLogORM, "msg_strategy_dispatch_logs"),
        (MsgStrategyUserCompanionEventORM, "msg_strategy_user_companion_events"),
    ):
        try:
            _tbl.__table__.create(bind=engine, checkfirst=True)
        except Exception as e:
            logger.warning("[init_db] %s 表创建失败: %s", _name, e)
    # 兼容：users 表新增 token 持久化字段
    _ensure_column("users", "token", "VARCHAR(128) DEFAULT ''")
    _ensure_column("users", "token_expire", "DATETIME")
    _ensure_column("users", "age", "INTEGER")
    _ensure_column("users", "region", "VARCHAR(120) DEFAULT ''")
    _ensure_column("users", "occupation", "VARCHAR(100) DEFAULT ''")
    _ensure_column("users", "role", "VARCHAR(20) DEFAULT 'user'")
    _ensure_column("users", "account_status", "VARCHAR(20) DEFAULT 'active'")
    _ensure_column("users", "deleted_at", "TIMESTAMP")
    _ensure_column("users", "email", "VARCHAR(200) DEFAULT ''")
    _ensure_column("users", "remark", "TEXT DEFAULT ''")
    _ensure_column("users", "app_key", "VARCHAR(32) DEFAULT 'trandsai'")
    _ensure_column("users", "app_version", "VARCHAR(32) DEFAULT ''")
    _ensure_index("users", "idx_users_app_key", ["app_key"])
    _ensure_column("users", "admin_companion_ids", "JSONB NOT NULL DEFAULT '[]'::jsonb")
    _ensure_column("admin_menus", "menu_name_en", "VARCHAR(100) DEFAULT ''")
    # 修复：扩大password_hash字段长度以支持长哈希值
    _alter_column_length("users", "password_hash", "VARCHAR(512)")
    _alter_column_length("users", "token", "VARCHAR(128)")
    # 邮箱作 username：需 >32
    _alter_column_length("users", "username", "VARCHAR(128)")
    _alter_column_length("users", "gender", "VARCHAR(20)")
    # IAP：Google purchase_token / Apple original_tx 可能较长
    _alter_column_length("vip_subscriptions", "store_transaction_id", "VARCHAR(512)")
    _alter_column_length("recharge_records", "store_transaction_id", "VARCHAR(512)")
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
    # 充值福利：模板-商品/区域多对多唯一索引
    _ensure_unique_index("benefit_template_products", "uniq_template_product", ["template_id", "product_id"])
    _ensure_unique_index("benefit_template_regions", "uniq_template_region", ["template_id", "region_code"])
    # 充值福利：应用（马甲）字段，缺省 trandsai 兼容旧数据
    _ensure_column("benefit_products", "app_key", "VARCHAR(32) DEFAULT 'trandsai'")
    _ensure_column("benefit_products", "is_limited_offer", "BOOLEAN DEFAULT FALSE")
    _ensure_column("benefit_products", "limited_duration_sec", "INTEGER DEFAULT 0")
    _ensure_index("benefit_products", "idx_benefit_products_limited", ["is_limited_offer"])
    _ensure_unique_index(
        "user_limited_offers",
        "uniq_user_limited_offer_app",
        ["user_id", "app_key"],
    )
    _ensure_column("benefit_templates", "app_key", "VARCHAR(32) DEFAULT 'trandsai'")
    _ensure_index("benefit_products", "idx_benefit_products_app_key", ["app_key"])
    _ensure_index("benefit_templates", "idx_benefit_templates_app_key", ["app_key"])
    # 兼容：为已存在的表添加 user_id 字段
    _ensure_column("moment_likes", "user_id", "INTEGER")
    _ensure_column("moment_comments", "user_id", "INTEGER")
    _ensure_column("page_views", "user_id", "INTEGER")
    _ensure_column("button_clicks", "user_id", "INTEGER")
    _ensure_column("short_term_messages", "user_id", "INTEGER")
    _ensure_column("short_term_messages", "admin_id", "INTEGER")
    _ensure_column("user_companion_states", "ops_admin_id", "INTEGER")
    _ensure_column("user_companion_states", "takeover_by", "INTEGER")
    _ensure_column("user_companion_states", "takeover_at", "TIMESTAMP")
    _ensure_column("user_companion_states", "takeover_active_at", "TIMESTAMP")
    _ensure_column(
        "user_companion_states",
        "ops_unread_count",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column("user_companion_states", "ops_last_read_at", "TIMESTAMP")
    _ensure_column(
        "user_companion_states",
        "free_turns_used",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column("user_companion_states", "first_im_at", "TIMESTAMP")
    # 已有环境：同步 free_turns_limit 文案（账号累计免费次数）
    try:
        _db = SessionLocal()
        try:
            _ft = (
                _db.query(ConfigGroupORM)
                .filter(ConfigGroupORM.key == "free_turns_limit")
                .first()
            )
            _new_desc = (
                "每个账号可免费聊天的次数"
                "（注册以来累计，存 user_credits.free_turns_used）"
            )
            if _ft is not None and (_ft.description or "") != _new_desc:
                _ft.description = _new_desc
                _db.commit()
            else:
                _db.rollback()
        finally:
            _db.close()
    except Exception as e:
        logger.warning("[init_db] free_turns_limit description sync failed: %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_ucs_ops_admin ON user_companion_states (ops_admin_id)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_ucs_takeover_by ON user_companion_states (takeover_by)"
            ))
            conn.commit()
    except Exception as e:
        logger.warning("[init_db] user_companion_states IM indexes failed: %s", e)
    _ensure_column("user_credits", "last_ip", "VARCHAR(64) DEFAULT ''")
    _ensure_column("user_credits", "last_device", "VARCHAR(512) DEFAULT ''")
    _ensure_column("user_credits", "say_hi_daily_used", "INTEGER DEFAULT 0")
    _ensure_column("user_credits", "say_hi_daily_date", "VARCHAR(10) DEFAULT ''")
    _ensure_column("user_attribution_events", "gclid", "VARCHAR(256) DEFAULT ''")
    _ensure_column("user_attribution_events", "gad_campaignid", "VARCHAR(128) DEFAULT ''")
    _ensure_column("user_attribution_events", "app_env", "VARCHAR(16) DEFAULT ''")
    _ensure_column("user_attribution_events", "app_key", "VARCHAR(32) DEFAULT 'trandsai'")
    _ensure_index("user_attribution_events", "idx_attr_app_key", ["app_key"])
    _ensure_column("user_attribution_events", "product_event", "VARCHAR(64) DEFAULT ''")
    _ensure_column("user_attribution_events", "firebase_event_name", "VARCHAR(128) DEFAULT ''")
    _ensure_column("user_attribution_events", "companion_id", "VARCHAR(8) DEFAULT ''")
    _ensure_column(
        "user_attribution_events",
        "firebase_event_params",
        "JSONB NOT NULL DEFAULT '{}'::jsonb",
    )
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_attr_gclid ON user_attribution_events (gclid)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_attr_gad_campaignid ON user_attribution_events (gad_campaignid)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_attr_device_event_created "
                "ON user_attribution_events (device_id, event_type, created_at)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_attr_product_event_created "
                "ON user_attribution_events (product_event, created_at)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_attr_companion_id "
                "ON user_attribution_events (companion_id)"
            ))
            # Moment 发消息按钮：按 product_event + firebase_event_params.target_id 聚合
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_attr_moment_msg_click_target "
                "ON user_attribution_events ("
                "  product_event,"
                "  ((firebase_event_params ->> 'target_id'))"
                ") "
                "WHERE product_event IN ("
                "  'send_message_click_moment',"
                "  'test_send_message_click_moment'"
                ")"
            ))
            conn.commit()
    except Exception as e:
        logger.warning("[init_db] attribution indexes failed: %s", e)
    # 兼容：扩大 short_term_messages.role 字段长度以支持 moment_displayed
    _alter_column_length("short_term_messages", "role", "VARCHAR(20)")
    # 兼容：admin_tokens 表（已存在表则跳过）
    from sqlalchemy import inspect
    inspector = inspect(engine)
    if "admin_tokens" not in inspector.get_table_names():
        AdminTokenORM.__table__.create(bind=engine)
    if "admin_im_audit_logs" not in inspector.get_table_names():
        AdminImAuditLogORM.__table__.create(bind=engine)

    # ===== Seed 默认配置（确保 batch 脚本、lifespan 和 image gen 都能正常工作） =====
    try:
        # 使用 SessionLocal 避免循环导入问题
        db = SessionLocal()
        try:
            # AgentConfigORM
            if not db.query(AgentConfigORM).first():
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
                    "key": "image_generation",
                    "name": "图片生成配置",
                    "description": "默认 Gemini 文生图；可配置 xAI 等降级",
                    "config_type": "image_generation",
                    "config_json": {
                        "provider": "google",
                        "base_url": "https://generativelanguage.googleapis.com/v1beta",
                        "api_key": os.getenv("GOOGLE_API_KEY", os.getenv("GOOGLEAI_API_KEY", "")),
                        "model": os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
                        "default_width": 1024,
                        "default_height": 1024,
                    },
                    "enabled": 1,
                    "sort_order": 3,
                },
                {
                    "key": "vip_plans",
                    "name": "VIP与积分配置",
                    "description": "配置VIP档位、充值包；扣费/免费次数请用「聊天次数」等 system 配置组",
                    "config_type": "payment",
                    "config_json": {
                        "plans": {
                            "weekly":   {"daily_credits": 24, "max_agents": 1, "duration_days": 7},
                            "monthly":  {"daily_credits": 50, "max_agents": 3, "duration_days": 30},
                            "yearly":   {"daily_credits": 25, "max_agents": 5, "duration_days": 365},
                            "lifetime": {"daily_credits": 20, "max_agents": 2, "duration_days": 36500},
                        },
                        "recharge_packages": {
                            "pack_250":  {"credits": 250},
                            "pack_550":  {"credits": 550},
                            "pack_800":  {"credits": 800},
                            "pack_1900": {"credits": 1900},
                        },
                    },
                    "enabled": 1,
                    "sort_order": 5,
                },
                {
                    "key": "free_turns_limit",
                    "name": "聊天次数",
                    "description": "每个账号可免费聊天的次数（注册以来累计，存 user_credits.free_turns_used）",
                    "config_type": "system",
                    "config_json": {"free_turns_limit": 3},
                    "enabled": 1,
                    "sort_order": 6,
                },
                {
                    "key": "say_hi_daily_free_limit",
                    "name": "Say Hi 每日免费次数",
                    "description": "每位用户每天可免费 Say Hi 的次数（UTC 日切）。用完后按正常聊天扣免费轮次/积分。",
                    "config_type": "system",
                    "config_json": {"say_hi_daily_free_limit": 10},
                    "enabled": 1,
                    "sort_order": 8,
                },
                {
                    "key": "default_affection_floor",
                    "name": "默认亲密档位",
                    "description": "提示词用：用户亲密度未超过本值时按本值进档；超过则按用户真实亲密度。不改库内亲密度。范围 20～100。",
                    "config_type": "system",
                    "config_json": {"default_affection_floor": 80},
                    "enabled": 1,
                    "sort_order": 7,
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

    # ===== 充值福利种子数据（按表去重；已有则跳过） =====
    try:
        _db = SessionLocal()
        try:
            if not _db.query(BenefitProductORM).first():
                _seed_products = [
                    # ── 订阅类（Google store_product_id + Apple apple_product_id）──
                    BenefitProductORM(product_type="subscribe", platform="all", title="年度VIP",   period="yearly",   price_usd=83.99,  discount_pct=40, daily_gift_credits=50,  max_agents=5, daily_avg_price=0.23, store_product_id="vip_yearly",   apple_product_id="year.subscription",  sort_order=1),
                    BenefitProductORM(product_type="subscribe", platform="all", title="月度VIP",   period="monthly",  price_usd=9.99,   discount_pct=20, daily_gift_credits=30,  max_agents=3, daily_avg_price=0.33, store_product_id="vip_monthly",  apple_product_id="month.subscription", sort_order=2),
                    BenefitProductORM(product_type="subscribe", platform="all", title="周卡VIP",   period="weekly",   price_usd=2.99,   discount_pct=10, daily_gift_credits=20,  max_agents=2, daily_avg_price=0.43, store_product_id="vip_weekly",   apple_product_id="7subscription",      sort_order=3),
                    BenefitProductORM(product_type="subscribe", platform="all", title="终身VIP",   period="lifetime", price_usd=199.99, discount_pct=60, daily_gift_credits=100, max_agents=10, daily_avg_price=0.0,  store_product_id="vip_lifetime", apple_product_id="Lifetime.VIP",         sort_order=4),
                    # ── 金币充值类 ──
                    BenefitProductORM(product_type="point", platform="all", title="250金币",  credits_amount=250,  price_usd=4.99,   discount_pct=10, store_product_id="pack_250",  apple_product_id="Points4.99",  sort_order=10),
                    BenefitProductORM(product_type="point", platform="all", title="550金币",  credits_amount=550,  price_usd=9.99,   discount_pct=15, store_product_id="pack_550",  apple_product_id="Points9.99",  sort_order=11),
                    BenefitProductORM(product_type="point", platform="all", title="800金币",  credits_amount=800,  price_usd=14.99,  discount_pct=20, store_product_id="pack_800",  apple_product_id="Points14.99", sort_order=12),
                    BenefitProductORM(product_type="point", platform="all", title="1900金币", credits_amount=1900, price_usd=29.99,  discount_pct=25, store_product_id="pack_1900", apple_product_id="Points1900",  sort_order=13),
                ]
                _db.add_all(_seed_products)
                _db.flush()
                logger.info("[init_db] 已插入福利商品 %d 条", len(_seed_products))

                # 获取刚插入的商品 ID（按 sort_order 排序）
                all_products = _db.query(BenefitProductORM).order_by(BenefitProductORM.sort_order).all()
                sub_ids = [p.id for p in all_products if p.product_type == "subscribe"]
                point_ids = [p.id for p in all_products if p.product_type == "point"]

                if sub_ids and point_ids:
                    # ── 模板 1：全球通用（全订阅 + 全金币） ──
                    tpl_global = BenefitTemplateORM(name="全球通用套餐", description="面向全球用户的完整福利组合", priority=10, is_active=True)
                    _db.add(tpl_global)
                    _db.flush()
                    for pid in sub_ids + point_ids:
                        _db.add(BenefitTemplateProductORM(template_id=tpl_global.id, product_id=pid))
                    for rc in ["ALL"]:
                        _db.add(BenefitTemplateRegionORM(template_id=tpl_global.id, region_code=rc))

                    # ── 模板 2：美国专属（年度 + 终身 + 大额金币） ──
                    tpl_us = BenefitTemplateORM(name="US专属套餐", description="美国区域专属，含高价值订阅和大额金币", priority=20, is_active=True)
                    _db.add(tpl_us)
                    _db.flush()
                    us_product_ids = [sub_ids[0], sub_ids[3]] + [point_ids[2], point_ids[3]]  # 年度+终身+800+1900
                    for pid in us_product_ids:
                        _db.add(BenefitTemplateProductORM(template_id=tpl_us.id, product_id=pid))
                    _db.add(BenefitTemplateRegionORM(template_id=tpl_us.id, region_code="US"))

                    # ── 模板 3：东南亚基础包（月卡 + 小额金币） ──
                    tpl_sea = BenefitTemplateORM(name="SEA基础套餐", description="东南亚区域基础入门套餐", priority=5, is_active=True)
                    _db.add(tpl_sea)
                    _db.flush()
                    sea_product_ids = [sub_ids[1], sub_ids[2]] + [point_ids[0], point_ids[1]]  # 月卡+周卡+250+550
                    for pid in sea_product_ids:
                        _db.add(BenefitTemplateProductORM(template_id=tpl_sea.id, product_id=pid))
                    for rc in ["SEA", "TH", "VN", "ID"]:
                        _db.add(BenefitTemplateRegionORM(template_id=tpl_sea.id, region_code=rc))

                    logger.info("[init_db] 已插入福利模板 3 个（全球通用 / US专属 / SEA基础）")
                _db.commit()
            else:
                _db.rollback()
        finally:
            _db.close()
    except Exception as e:
        logger.warning("[init_db] 充值福利 seeding 失败（非致命）: %s", e)

    try:
        _db = SessionLocal()
        try:
            from services.recommend_tags import backfill_companion_tags, seed_recommend_tag_defs

            n_defs = seed_recommend_tag_defs(_db)
            n_back = backfill_companion_tags(_db)
            _db.commit()
            if n_defs or n_back:
                logger.info("[init_db] 推荐字典种子 %s，智能体 tags 回填 %s", n_defs, n_back)
        finally:
            _db.close()
    except Exception as e:
        logger.warning("[init_db] 推荐标签种子/回填失败（非致命）: %s", e)

    try:
        _db = SessionLocal()
        try:
            from services.admin_rbac import seed_admin_rbac

            created = seed_admin_rbac(_db)
            _db.commit()
            if any(created.values()):
                logger.info("[init_db] 管理端 RBAC 种子 %s", created)
        finally:
            _db.close()
    except Exception as e:
        logger.warning("[init_db] 管理端 RBAC 种子失败（非致命）: %s", e)

    _ensure_column("chat_version_prompts", "apply_registered_start_at", "TIMESTAMP")
    _ensure_column("chat_version_prompts", "apply_registered_end_at", "TIMESTAMP")
    _ensure_column("companion_strategies", "image_url", "VARCHAR(500) DEFAULT ''")
    _ensure_column("msg_strategy_library", "image_url", "VARCHAR(500) DEFAULT ''")
    _ensure_unique_index(
        "chat_version_prompt_users",
        "uq_chat_ver_prompt_user",
        ["prompt_id", "user_id"],
    )
    _ensure_unique_index(
        "companion_private_album_progress",
        "uq_private_album_user_companion",
        ["user_id", "companion_id"],
    )
    try:
        from services.version_prompt import ensure_default_version_prompt

        ensure_default_version_prompt()
    except Exception as e:
        logger.warning("[init_db] 版本提示词种子失败（非致命）: %s", e)

    try:
        _db = SessionLocal()
        try:
            from services.strategy_types import seed_strategy_types

            n = seed_strategy_types(_db)
            _db.commit()
            if n:
                logger.info("[init_db] strategy_types 种子插入 %s 条", n)
        finally:
            _db.close()
    except Exception as e:
        logger.warning("[init_db] strategy_types 种子失败（非致命）: %s", e)

    try:
        _db = SessionLocal()
        try:
            from services.msg_strategy_seed import seed_msg_strategy_defaults

            created = seed_msg_strategy_defaults(_db)
            _db.commit()
            if any(created.values()):
                logger.info("[init_db] msg_strategy 种子 %s", created)
        finally:
            _db.close()
    except Exception as e:
        logger.warning("[init_db] msg_strategy 种子失败（非致命）: %s", e)

    print("[init_db] done", flush=True)


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
