"""管理后台 RBAC 种子：角色 / 菜单 / 默认账号（幂等，可重复执行）。

重要约定：
- 默认菜单仅在 admin_menus 表为空时写入一次；运营删除后不会再被种子写回。
- 角色-菜单预设仅在「本轮新建该角色」时写入；已有角色清空勾选后不会被灌回全量。
"""
from __future__ import annotations

import logging
from typing import List, Set, Tuple

from sqlalchemy.orm import Session

from core.database import AdminMenuORM, AdminRoleMenuORM, AdminRoleORM, UserORM

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_USERNAME = "gigadmin"
DEFAULT_ADMIN_PASSWORD = "123456"

DEFAULT_ROLES: List[Tuple[str, str, str]] = [
    ("admin", "超级管理员", "全部菜单"),
    ("ops", "运营", "运营相关菜单"),
    ("operator", "操作员", "日常操作菜单"),
    ("support", "客服", "客服相关菜单"),
]

# 仅「空表首次安装」写入；之后增删改完全以数据库 / 后台为准
# (menu_key, menu_name_zh, menu_name_en, path, sort_order)
DEFAULT_MENUS: List[Tuple[str, str, str, str, int]] = [
    ("dashboard", "仪表盘", "Dashboard", "/", 10),
    ("companions", "智能体", "Companions", "/companions", 20),
    ("moments", "朋友圈", "Moments", "/moments", 30),
    ("posts", "帖子", "Posts", "/posts", 35),
    ("users", "用户管理", "Users", "/users", 40),
    ("chat-sessions", "IM管理", "IM", "/chat-sessions", 50),
    ("feedback", "意见反馈", "Feedback", "/feedback", 60),
    ("notifications", "系统通知", "Notifications", "/notifications", 70),
    ("knowledge", "知识库", "Knowledge Base", "/knowledge", 80),
    ("image-gen", "图片生成", "Image Generation", "/image-gen", 90),
    ("analytics", "数据统计", "Analytics", "/analytics", 100),
    ("audit", "审核", "Audit", "/audit", 110),
    ("settings", "系统设置", "Settings", "/settings", 120),
    ("products", "商品", "Products", "/products", 130),
    ("templates", "套餐模板", "Templates", "/templates", 140),
    ("recommend", "推荐广告", "Ads", "/recommend", 150),
    ("attribution", "归因", "Attribution", "/attribution", 160),
    ("msg-strategy", "消息策略", "Message Strategy", "/msg-strategy", 170),
    ("quick-reply", "快速回复", "Quick Reply", "/quick-reply", 180),
    ("chat-version-prompts", "版本提示词", "Version Prompts", "/chat-version-prompts", 185),
]

# 已有库回填英文名（仅空 menu_name_en 时）
MENU_NAME_EN_BY_KEY = {k: en for k, _zh, en, _path, _so in DEFAULT_MENUS}
MENU_NAME_EN_BY_KEY.update(
    {
        "system": "System",
        "menus": "Menus",
        "admin-users": "Admin Users",
        "roles": "Roles",
        "config": "Config",
        "user-permissions": "User Permissions",
        "content-management": "Content",
        "posts": "Posts",
        "msg-strategy-globals": "Strategy Globals",
        "msg-strategy-rules": "Strategy Rules",
        "msg-strategy-library": "Strategy Library",
        "payment": "Payment",
    }
)

# 仅在本轮新建该角色时写入；None = 当时全部启用菜单
ROLE_MENU_PRESETS = {
    "admin": None,
    "ops": None,
    "operator": None,
    "support": None,
}


def _hash_password(password: str) -> str:
    from api.auth import _hash_password as _hp

    return _hp(password)


def _ensure_tables() -> None:
    from core.database import _ensure_column, engine

    AdminRoleORM.__table__.create(bind=engine, checkfirst=True)
    AdminMenuORM.__table__.create(bind=engine, checkfirst=True)
    AdminRoleMenuORM.__table__.create(bind=engine, checkfirst=True)
    _ensure_column("admin_menus", "menu_name_en", "VARCHAR(100) DEFAULT ''")


def seed_admin_rbac(db: Session) -> dict:
    """写入缺失的角色、（空表时）菜单、新建角色的菜单预设、默认管理员。"""
    _ensure_tables()
    created = {"roles": 0, "menus": 0, "role_menus": 0, "admin_user": 0}
    newly_created_roles: Set[str] = set()

    for role_key, role_name, description in DEFAULT_ROLES:
        row = db.query(AdminRoleORM).filter(AdminRoleORM.role_key == role_key).first()
        if not row:
            db.add(
                AdminRoleORM(
                    role_key=role_key,
                    role_name=role_name,
                    description=description,
                    is_active=True,
                )
            )
            created["roles"] += 1
            newly_created_roles.add(role_key)

    # 只在菜单表完全为空时灌默认菜单，避免删除后又被写回
    menu_n = db.query(AdminMenuORM.id).count()
    if menu_n == 0:
        for menu_key, menu_name, menu_name_en, path, sort_order in DEFAULT_MENUS:
            db.add(
                AdminMenuORM(
                    menu_key=menu_key,
                    menu_name=menu_name,
                    menu_name_en=menu_name_en,
                    path=path,
                    parent_key="",
                    sort_order=sort_order,
                    is_active=True,
                )
            )
            created["menus"] += 1
        db.flush()
    else:
        # 轻量兼容：仅改名，不插入缺失项
        row = db.query(AdminMenuORM).filter(AdminMenuORM.menu_key == "chat-sessions").first()
        if row and (row.menu_name or "") == "会话记录":
            row.menu_name = "IM管理"
            db.flush()
        # 内容管理「帖子」：已有库补一条
        if not db.query(AdminMenuORM.id).filter(AdminMenuORM.menu_key == "posts").first():
            parent = (
                db.query(AdminMenuORM.menu_key)
                .filter(AdminMenuORM.menu_key == "content-management")
                .first()
            )
            db.add(
                AdminMenuORM(
                    menu_key="posts",
                    menu_name="帖子",
                    menu_name_en="Posts",
                    path="/posts",
                    parent_key=(parent[0] if parent else ""),
                    sort_order=35,
                    is_active=True,
                )
            )
            created["menus"] += 1
            db.flush()
        if not db.query(AdminMenuORM.id).filter(AdminMenuORM.menu_key == "quick-reply").first():
            db.add(
                AdminMenuORM(
                    menu_key="quick-reply",
                    menu_name="快速回复",
                    menu_name_en="Quick Reply",
                    path="/quick-reply",
                    parent_key="",
                    sort_order=180,
                    is_active=True,
                )
            )
            created["menus"] += 1
            db.flush()
        if not db.query(AdminMenuORM.id).filter(AdminMenuORM.menu_key == "chat-version-prompts").first():
            db.add(
                AdminMenuORM(
                    menu_key="chat-version-prompts",
                    menu_name="版本提示词",
                    menu_name_en="Version Prompts",
                    path="/chat-version-prompts",
                    parent_key="",
                    sort_order=185,
                    is_active=True,
                )
            )
            created["menus"] += 1
            db.flush()
            for role in db.query(AdminRoleORM).all():
                has = (
                    db.query(AdminRoleMenuORM.id)
                    .filter(
                        AdminRoleMenuORM.role_key == role.role_key,
                        AdminRoleMenuORM.menu_key == "chat-version-prompts",
                    )
                    .first()
                )
                if not has:
                    db.add(
                        AdminRoleMenuORM(
                            role_key=role.role_key,
                            menu_key="chat-version-prompts",
                        )
                    )
            db.flush()
        # 回填空英文名（不覆盖运营已改过的）
        for m in db.query(AdminMenuORM).all():
            if (getattr(m, "menu_name_en", None) or "").strip():
                continue
            en = MENU_NAME_EN_BY_KEY.get(m.menu_key) or ""
            if en:
                m.menu_name_en = en
                created["menus"] += 0  # 仅回填，不计新建
        db.flush()

    all_menu_keys = [
        m.menu_key
        for m in db.query(AdminMenuORM).filter(AdminMenuORM.is_active == True).all()  # noqa: E712
    ]

    # 仅给「本轮新建」的预设角色灌菜单；已有角色（含勾选被清空）绝不回填
    for role_key, preset in ROLE_MENU_PRESETS.items():
        if role_key not in newly_created_roles:
            continue
        role = db.query(AdminRoleORM).filter(AdminRoleORM.role_key == role_key).first()
        if not role:
            continue
        want = all_menu_keys if preset is None else list(preset)
        # 避免 id 序列落后导致灌库 UniqueViolation
        try:
            from sqlalchemy import text

            db.execute(
                text(
                    "SELECT setval("
                    "pg_get_serial_sequence('admin_role_menus', 'id'),"
                    "GREATEST(COALESCE((SELECT MAX(id) FROM admin_role_menus), 1), 1)"
                    ")"
                )
            )
        except Exception:
            logger.debug("sync admin_role_menus seq on seed skipped", exc_info=True)
        for mk in sorted(set(want)):
            db.add(AdminRoleMenuORM(role_key=role_key, menu_key=mk))
            created["role_menus"] += 1

    user = db.query(UserORM).filter(UserORM.username == DEFAULT_ADMIN_USERNAME).first()
    if not user:
        db.add(
            UserORM(
                username=DEFAULT_ADMIN_USERNAME,
                nickname="超级管理员",
                password_hash=_hash_password(DEFAULT_ADMIN_PASSWORD),
                role="admin",
                account_status="active",
                gender="",
                sexual_orientation="",
            )
        )
        created["admin_user"] = 1
    else:
        changed = False
        if (user.role or "user").strip().lower() in ("", "user"):
            user.role = "admin"
            changed = True
        status = (getattr(user, "account_status", None) or "active").strip() or "active"
        if status != "active":
            user.account_status = "active"
            changed = True
        stored = user.password_hash or ""
        if "$" not in stored and len(stored) != 32:
            user.password_hash = _hash_password(DEFAULT_ADMIN_PASSWORD)
            changed = True
        if changed:
            created["admin_user"] = 0

    db.flush()
    if any(created.values()):
        logger.info("[admin_rbac] seed %s", created)
    return created
