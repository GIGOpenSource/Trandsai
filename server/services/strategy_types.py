"""策略类型（= 埋点事件）字典：种子、CRUD 辅助、查询。

策略类型与埋点英文事件名一一对应；运营可在后台增删改。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from core.database import StrategyTypeORM, serialize_datetime

logger = logging.getLogger(__name__)

# 来自 Trandsai数据埋点表.xlsx（Sheet1 全字段）
DEFAULT_STRATEGY_TYPES: List[Dict[str, Any]] = [
    {"seq": 1, "event_key": "app_launch", "name_zh": "启动APP", "report_rule": "每次APP冷启动或热启动时上报", "remark": "可携带启动来源、设备信息等公共参数", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 2, "event_key": "login_page_show", "name_zh": "登录页展示", "report_rule": "登录页面成功展示时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 3, "event_key": "account_login_click", "name_zh": "账户登录点击", "report_rule": "用户点击账户登录按钮时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 4, "event_key": "google_login_click", "name_zh": "Google登录点击", "report_rule": "用户点击Google登录按钮时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 5, "event_key": "apple_login_click", "name_zh": "Apple登录点击", "report_rule": "用户点击Apple登录按钮时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 6, "event_key": "newbie_guide_show", "name_zh": "新手引导页展示", "report_rule": "新手引导页面成功展示时上报", "remark": "首次登录用户触发", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 7, "event_key": "home_page_show", "name_zh": "首页展示成功", "report_rule": "首页成功加载并展示时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 8, "event_key": "send_message_icon_click", "name_zh": "发消息icon点击", "report_rule": "用户点击发消息icon时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 9, "event_key": "home_robot_tab_click", "name_zh": "首页机器人tab点击", "report_rule": "用户点击首页机器人tab时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 10, "event_key": "moments_comment_click", "name_zh": "朋友圈评论点击", "report_rule": "用户点击朋友圈评论时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 11, "event_key": "pull_refresh_report", "name_zh": "下拉刷新上报", "report_rule": "用户下拉刷新成功时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 12, "event_key": "pull_up_load_report", "name_zh": "上滑加载上报", "report_rule": "用户上滑加载更多成功时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 13, "event_key": "message_page_click", "name_zh": "消息页面点击上报", "report_rule": "用户点击进入消息页面时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 14, "event_key": "chat_detail_click", "name_zh": "点击对话详情上报", "report_rule": "用户点击进入对话详情时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 15, "event_key": "community_page_click", "name_zh": "点击社区页面上报", "report_rule": "用户点击进入社区页面时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 16, "event_key": "mine_page_click", "name_zh": "点击我的页面上报", "report_rule": "用户点击进入我的页面时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 17, "event_key": "agent_create_click", "name_zh": "点击智能体创建上报", "report_rule": "用户点击智能体创建入口时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 18, "event_key": "agent_create_pay_popup", "name_zh": "智能体创建触发付费弹窗上报", "report_rule": "创建智能体时触发付费弹窗时上报", "remark": "漏斗相关事件", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 19, "event_key": "free_message_pay_popup", "name_zh": "发消息Free后触发付费弹窗上报", "report_rule": "免费消息使用完毕后触发付费弹窗时上报", "remark": "漏斗相关事件（免费消息使用完）", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 20, "event_key": "agent_create_button_click", "name_zh": "点击智能体创建页面内创建按钮上报", "report_rule": "在智能体创建页面内点击创建按钮时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 21, "event_key": "moments_detail_click", "name_zh": "点击朋友圈详情上报", "report_rule": "用户点击进入朋友圈详情页时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 22, "event_key": "avatar_profile_click", "name_zh": "点击头像进入profile上报", "report_rule": "用户点击头像进入个人主页（profile）时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 23, "event_key": "robot_list_click", "name_zh": "点击机器人列表上报", "report_rule": "用户点击进入机器人列表时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": "与序号9相同"},
    {"seq": 24, "event_key": "robot_list_pull_up", "name_zh": "机器人列表上滑上报", "report_rule": "用户在机器人列表上滑加载更多时上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 25, "event_key": "robot_list_pull_refresh", "name_zh": "机器人列表下拉刷新上报", "report_rule": "用户在机器人列表下拉刷新时上报", "remark": "-", "report_type": "PV & UV", "status": "0", "note": ""},
    {"seq": 26, "event_key": "Registration_finish", "name_zh": "注册引导finish", "report_rule": "引导页最后一个页面完成点击上报", "remark": "上报一次", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 27, "event_key": "registration_skip", "name_zh": "引导跳过（用户注册成功）", "report_rule": "引导跳过上报", "remark": "上报一次", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 28, "event_key": "avatar_upload", "name_zh": "上传头像完成", "report_rule": "上传头像成功上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 29, "event_key": "profile_displayed_15s", "name_zh": "智能体Profile页面展示≥15s", "report_rule": "展示时间满足上报", "remark": "每次上报", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 30, "event_key": "Moment_15s", "name_zh": "profile-moment≥15s", "report_rule": "时间满足上报", "remark": "每次上报", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 31, "event_key": "send_message_click_moment", "name_zh": "Moment发消息按钮点击上报", "report_rule": "点击上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 32, "event_key": "send_message_click_im", "name_zh": "IM页面发消息按钮点击上报", "report_rule": "点击上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 33, "event_key": "no_data_of_im", "name_zh": "每日首次打开IM未读数据为空", "report_rule": "用户当日首次进入IM且无未读消息", "remark": "每日首次打开上报一次", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 34, "event_key": "dree_message_5", "name_zh": "free message 使用满5次", "report_rule": "free message 使用满5次上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 35, "event_key": "dree_message_3", "name_zh": "free message 使用满3次", "report_rule": "free message 使用满3次上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 36, "event_key": "dree_message_0", "name_zh": "在线180秒 free message 未使用", "report_rule": "在线180秒 free message 未使用 上报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 38, "event_key": "Report_character_Click", "name_zh": "打开举报弹窗", "report_rule": "打开举报弹窗", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 39, "event_key": "Report_character_submit", "name_zh": "确认举报", "report_rule": "确认举报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 40, "event_key": "Report_character_success", "name_zh": "确认举报成功", "report_rule": "确认举报", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 41, "event_key": "Report_character_reason_minor_Click", "name_zh": "涉及未成年人", "report_rule": "涉及未-成年人", "remark": "minor_safety", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 42, "event_key": "Report_character_reason_sensitive_Click", "name_zh": "敏感话题", "report_rule": "敏感话题", "remark": "sensitive", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 43, "event_key": "Report_character_reason_disturbing_Click", "name_zh": "令人不适的内容", "report_rule": "令人不适的内容", "remark": "disturbing", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 44, "event_key": "Report_character_reason_copyright_Click", "name_zh": "版权侵权", "report_rule": "版权侵权", "remark": "copyright", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 45, "event_key": "Report_character_reason_other_Click", "name_zh": "其他", "report_rule": "其他", "remark": "other", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 46, "event_key": "feedback_page_view", "name_zh": "进入 Feedback 页面", "report_rule": "用户进入 Feedback 页面时", "remark": "页面加载成功", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 47, "event_key": "feedback_submit_click", "name_zh": "点击 Feedback 提交", "report_rule": "用户点击 Submit 时", "remark": "点击提交按钮", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 48, "event_key": "feedback_submit_success", "name_zh": "反馈提交成功", "report_rule": "反馈成功提交至后台时", "remark": "接口返回成功", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 49, "event_key": "feedback_submit_fail", "name_zh": "反馈提交失败", "report_rule": "反馈提交失败时", "remark": "接口返回失败/请求异常", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 50, "event_key": "login", "name_zh": "登录成功", "report_rule": "登录成功", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 51, "event_key": "register_success", "name_zh": "注册成功", "report_rule": "注册成功", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 52, "event_key": "purchase_success", "name_zh": "支付成功", "report_rule": "支付成功", "remark": "-", "report_type": "PV & UV", "status": "", "note": ""},
    {"seq": 53, "event_key": "paid_click", "name_zh": "付费点击", "report_rule": "付费点击", "remark": "缺失支付的场景", "report_type": "PV & UV", "status": "", "note": "缺失支付的场景"},
    {"seq": 54, "event_key": "chat_page_view", "name_zh": "消息对话页展示上报", "report_rule": "页面成功加载渲染完成", "remark": "用户进入 IM 对话页，页面渲染完毕触发", "report_type": "PV & UV", "status": "", "note": "用户进入 IM 对话页，页面渲染完毕触发"},
    {"seq": 55, "event_key": "quick_login_click", "name_zh": "快速登录按钮点击", "report_rule": "用户点击登录页快速登录按钮", "remark": "仅点击按钮时上报", "report_type": "PV & UV", "status": "", "note": "仅点击按钮时上报"},
    {"seq": 56, "event_key": "quick_login_success", "name_zh": "快速登录成功", "report_rule": "快速登录流程完成、登录接口返回成功", "remark": "快速登录专用，和全局 login 埋点区分", "report_type": "PV & UV", "status": "", "note": "快速登录专用，和全局 login 埋点区分"},
    {"seq": 57, "event_key": "quick_login_fail", "name_zh": "快速登录失败", "report_rule": "快速登录接口返回异常 / 取消登录", "remark": "失败、用户主动关闭都触发", "report_type": "PV & UV", "status": "", "note": "失败、用户主动关闭都触发"},
    {"seq": 58, "event_key": "say_hi_pay_popup", "name_zh": "Say Hi 付费弹窗", "report_rule": "对某智能体发起 Say Hi 并弹出付费墙时上报", "remark": "须带该智能体 companion_id", "report_type": "PV & UV", "status": "", "note": "指定智能体事件"},
]

# 消息策略：仅对上报的 companion_id（或 moment_id 反查）下发，禁止全局随机 1～3
COMPANION_BOUND_EVENT_KEYS = frozenset(
    {
        "moments_comment_click",
        "free_message_pay_popup",
        "moments_detail_click",
        "avatar_profile_click",
        "profile_displayed_15s",
        "Moment_15s",
        "send_message_click_moment",
        "send_message_click_im",
        "chat_detail_click",
        "send_message_icon_click",
        "chat_page_view",
        "say_hi_pay_popup",
        "dree_message_3",
        "dree_message_5",
    }
)


def event_sender_mode(event_key: str) -> str:
    """companion=指定智能体；broadcast=通用（可随机 1～3）。"""
    return "companion" if (event_key or "").strip() in COMPANION_BOUND_EVENT_KEYS else "broadcast"


def strategy_type_to_dict(row: StrategyTypeORM) -> dict:
    return {
        "id": row.id,
        "seq": int(row.seq or 0),
        "event_key": row.event_key or "",
        "name_en": row.event_key or "",  # 英文名 = event_key
        "name_zh": row.name_zh or "",
        "report_rule": row.report_rule or "",
        "remark": row.remark or "",
        "report_type": row.report_type or "PV & UV",
        "status": row.status or "",
        "note": row.note or "",
        "is_active": bool(row.is_active),
        "sender_mode": event_sender_mode(row.event_key or ""),
        "created_at": serialize_datetime(row.created_at),
        "updated_at": serialize_datetime(row.updated_at),
    }


def seed_strategy_types(db: Session) -> int:
    """幂等种子：仅插入缺失的 event_key，不覆盖已有编辑。"""
    existing = {r.event_key for r in db.query(StrategyTypeORM.event_key).all()}
    created = 0
    for item in DEFAULT_STRATEGY_TYPES:
        key = item["event_key"]
        if key in existing:
            continue
        db.add(
            StrategyTypeORM(
                seq=int(item.get("seq") or 0),
                event_key=key,
                name_zh=item.get("name_zh") or "",
                report_rule=item.get("report_rule") or "",
                remark=item.get("remark") or "",
                report_type=item.get("report_type") or "PV & UV",
                status=str(item.get("status") or ""),
                note=item.get("note") or "",
                is_active=True,
            )
        )
        created += 1
    if created:
        db.flush()
        logger.info("[seed_strategy_types] inserted %s rows", created)
    return created


def list_strategy_types(
    db: Session,
    *,
    keyword: Optional[str] = None,
    is_active: Optional[bool] = None,
    active_only: bool = False,
) -> List[StrategyTypeORM]:
    q = db.query(StrategyTypeORM)
    if active_only or is_active is True:
        q = q.filter(StrategyTypeORM.is_active.is_(True))
    elif is_active is False:
        q = q.filter(StrategyTypeORM.is_active.is_(False))
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.filter(
            (StrategyTypeORM.event_key.ilike(kw)) | (StrategyTypeORM.name_zh.ilike(kw))
        )
    return q.order_by(StrategyTypeORM.seq.asc(), StrategyTypeORM.id.asc()).all()


def get_event_keys(db: Optional[Session] = None, *, active_only: bool = True) -> set:
    """有效 event_key 集合；无 DB 时回退常量。"""
    if db is not None:
        q = db.query(StrategyTypeORM.event_key)
        if active_only:
            q = q.filter(StrategyTypeORM.is_active.is_(True))
        keys = {r[0] for r in q.all() if r[0]}
        if keys:
            return keys
    return {x["event_key"] for x in DEFAULT_STRATEGY_TYPES}


def get_event_meta(event_key: str, db: Optional[Session] = None) -> Dict[str, str]:
    key = (event_key or "").strip()
    if not key:
        return {}
    if db is not None:
        row = db.query(StrategyTypeORM).filter(StrategyTypeORM.event_key == key).first()
        if row:
            return {"event_key": row.event_key, "name_zh": row.name_zh or ""}
    for item in DEFAULT_STRATEGY_TYPES:
        if item["event_key"] == key:
            return {"event_key": key, "name_zh": item.get("name_zh") or ""}
    return {}


def validate_event_key(event_key: str, db: Optional[Session] = None) -> str:
    key = (event_key or "").strip()
    if key not in get_event_keys(db, active_only=True):
        raise ValueError("invalid_trigger_event")
    return key
