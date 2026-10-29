"""消息策略默认种子：一条全局控制 + 一条示例规则。"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from core.database import MsgStrategyGlobalORM, MsgStrategyRuleORM

logger = logging.getLogger(__name__)

# 默认规则：直接用 strategy_types.event_key（= trigger_key）
_DEFAULT_RULE_TRIGGERS = {
    "newbie_guide_show": 1,
    "Registration_finish": 1,
    "registration_skip": 1,
    "avatar_upload": 1,
    "profile_displayed_15s": 1,
    "Moment_15s": 1,
    "send_message_click_im": 2,
    "send_message_icon_click": 2,
    "send_message_click_moment": 2,
    "no_data_of_im": 4,
}


def seed_msg_strategy_defaults(db: Session) -> dict:
    created = {"globals": 0, "rules": 0}
    if not db.query(MsgStrategyGlobalORM.id).first():
        db.add(
            MsgStrategyGlobalORM(
                name="默认全局控制",
                user_scope_type="all",
                user_scope_value="",
                fatigue_window_hours=2,
                fatigue_max_unreplied=10,
                daily_limit=20,
                interval_min_sec=15,
                interval_max_sec=45,
                priority=1,
                is_active=True,
            )
        )
        created["globals"] = 1

    if not db.query(MsgStrategyRuleORM.id).first():
        db.add(
            MsgStrategyRuleORM(
                name="默认消息策略规则",
                user_scope_type="all",
                user_scope_value="",
                triggers=dict(_DEFAULT_RULE_TRIGGERS),
                priority=1,
                is_active=True,
            )
        )
        created["rules"] = 1
    return created
