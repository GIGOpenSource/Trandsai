"""Swagger / OpenAPI 分组：用户端板块 + 管理后台拆分。"""
from __future__ import annotations

from typing import Optional

# 顺序即 /docs 侧栏顺序
OPENAPI_TAGS = [
    {"name": "系统", "description": "健康检查与服务信息"},
    {"name": "认证", "description": "用户登录、注册、Token 管理"},
    {"name": "Google登录", "description": "Google OAuth"},
    {"name": "Apple登录", "description": "Apple Sign In"},
    {"name": "伴侣", "description": "智能体列表、资料、消息记录与人设"},
    {"name": "聊天", "description": "WebSocket 对话、收件箱、消息翻译"},
    {"name": "朋友圈", "description": "朋友圈动态的发布、点赞、评论"},
    {"name": "社区", "description": "社区帖子的发布、互动和搜索"},
    {"name": "支付", "description": "内购、订阅、订单与会员"},
    {"name": "充值福利", "description": "福利商品与订阅权益"},
    {"name": "反馈", "description": "用户端 Feedback 客服会话"},
    {"name": "智能体举报", "description": "用户举报智能体"},
    {"name": "UGC举报", "description": "用户举报帖子/资料"},
    {"name": "数据分析", "description": "页面浏览和用户行为统计"},
    {"name": "增长归因", "description": "广告归因上报"},
    {"name": "知识库", "description": "文化知识库搜索与查询"},
    {"name": "文化数据", "description": "姓名和城市等文化数据生成"},
    {"name": "推送", "description": "设备推送 Token 绑定"},
    {"name": "管理后台 · 登录权限", "description": "管理员登录、账号、角色、菜单"},
    {"name": "管理后台 · 智能体", "description": "智能体 CRUD、导入导出、批量生成、生图"},
    {"name": "管理后台 · 用户", "description": "用户列表、封禁、账号状态"},
    {"name": "管理后台 · 内容管理", "description": "内容管理：资料、策略编辑、朋友圈、帖子"},
    {"name": "管理后台 · 消息策略", "description": "策略中心、埋点库、快回、Say Hi、版本提示词"},
    {"name": "管理后台 · 朋友圈", "description": "后台朋友圈批量生成与配图"},
    {"name": "管理后台 · IM 会话", "description": "人工接管、会话消息、运营回复"},
    {"name": "管理后台 · 审核", "description": "帖子/资料/举报审核"},
    {"name": "管理后台 · 推荐", "description": "推荐策略与标签字典"},
    {"name": "管理后台 · 增长归因", "description": "后台归因事件查询"},
    {"name": "管理后台 · 预订单", "description": "预订单列表与统计"},
    {"name": "管理后台 · 系统配置", "description": "配置组、模型、Agent 配置"},
    {"name": "管理后台 · 数据统计", "description": "DAU、留存、漏斗、营收看板"},
    {"name": "管理后台 · 数据分析", "description": "后台埋点分析导出"},
    {"name": "管理后台 · 通知", "description": "系统通知管理"},
    {"name": "管理后台 · 运营触达", "description": "触达漏斗与扫描"},
    {"name": "管理后台 · 客服反馈", "description": "后台回复用户 Feedback"},
    {"name": "管理后台 · 知识库", "description": "知识条目与向量索引"},
    {"name": "管理后台 · 接口日志", "description": "访问日志开关与下载"},
    {"name": "管理后台 · 媒体", "description": "后台图片代理"},
    {"name": "管理后台 · 其它", "description": "尚未单独拆出的管理接口"},
]

# 最长前缀优先
_ADMIN_PREFIX_TAGS = (
    ("/ws/admin/im", "管理后台 · IM 会话"),
    ("/api/admin/im/", "管理后台 · IM 会话"),
    ("/api/admin/content", "管理后台 · 内容管理"),
    ("/api/admin/strategy-center", "管理后台 · 消息策略"),
    ("/api/admin/msg-strategy", "管理后台 · 消息策略"),
    ("/api/admin/companion-strategies", "管理后台 · 消息策略"),
    ("/api/admin/quick-reply", "管理后台 · 消息策略"),
    ("/api/admin/say-hi", "管理后台 · 消息策略"),
    ("/api/admin/chat-version-prompts", "管理后台 · 消息策略"),
    ("/api/admin/moments", "管理后台 · 朋友圈"),
    ("/api/admin/recommend", "管理后台 · 推荐"),
    ("/api/admin/attribution", "管理后台 · 增长归因"),
    ("/api/admin/pending-orders", "管理后台 · 预订单"),
    ("/api/admin/config-groups", "管理后台 · 系统配置"),
    ("/api/admin/config", "管理后台 · 系统配置"),
    ("/api/admin/agent-config", "管理后台 · 系统配置"),
    ("/api/admin/stats/", "管理后台 · 数据统计"),
    ("/api/admin/analytics", "管理后台 · 数据分析"),
    ("/api/admin/audit", "管理后台 · 审核"),
    ("/api/admin/notifications", "管理后台 · 通知"),
    ("/api/admin/outreach", "管理后台 · 运营触达"),
    ("/api/admin/feedback", "管理后台 · 客服反馈"),
    ("/api/admin/knowledge", "管理后台 · 知识库"),
    ("/api/admin/embedding-status", "管理后台 · 知识库"),
    ("/api/admin/logs", "管理后台 · 接口日志"),
    ("/api/admin/media", "管理后台 · 媒体"),
    ("/api/admin/admin-users", "管理后台 · 登录权限"),
    ("/api/admin/roles", "管理后台 · 登录权限"),
    ("/api/admin/menus", "管理后台 · 登录权限"),
    ("/api/admin/me/", "管理后台 · 登录权限"),
    ("/api/admin/login", "管理后台 · 登录权限"),
    ("/api/admin/users", "管理后台 · 用户"),
    ("/api/admin/companions", "管理后台 · 智能体"),
    ("/api/admin/generate-image", "管理后台 · 智能体"),
    ("/api/admin/reset-all", "管理后台 · 其它"),
    ("/api/admin/stats", "管理后台 · 其它"),
)

_KEEP_TAGS = {
    "认证",
    "Google登录",
    "Apple登录",
    "朋友圈",
    "社区",
    "支付",
    "充值福利",
    "反馈",
    "智能体举报",
    "UGC举报",
    "数据分析",
    "增长归因",
    "文化数据",
    "推送",
    "推荐归因",
}


def tag_for_path(path: str, current_tags: Optional[list] = None) -> str:
    """给一条路径确定唯一 Swagger 分组名。"""
    p = path or ""
    if p.startswith("/api/admin") or p.startswith("/ws/admin"):
        for prefix, name in _ADMIN_PREFIX_TAGS:
            if p.startswith(prefix):
                return name
        return "管理后台 · 其它"

    tags = [str(t) for t in (current_tags or []) if t]
    if tags and tags[0] in _KEEP_TAGS:
        return tags[0]

    if p.startswith("/ws/"):
        return "聊天"
    if p.startswith("/api/im/") or p == "/api/im/translate":
        return "聊天"
    if p.startswith("/knowledge") or p.startswith("/api/knowledge"):
        return "知识库"
    if p.startswith("/companions") or p.startswith("/api/companions") or p.startswith("/api/bcompanions"):
        return "伴侣"
    if p in ("/", "/api/health", "/health", "/admin"):
        return "系统"
    if tags and tags[0] not in ("default", "管理后台"):
        return tags[0]
    return "系统"


def apply_openapi_tags(schema: dict) -> dict:
    """重写 paths[*].tags，并写入有序 tags 元数据（含备注）。"""
    for path, item in (schema.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.startswith("x-") or not isinstance(op, dict):
                continue
            op["tags"] = [tag_for_path(path, op.get("tags"))]
    schema["tags"] = OPENAPI_TAGS
    return schema
