"""管理后台「数据统计」聚合：日活 / 留存 / 事件 / 漏斗 / 营收。

数据来源：
- page_views / button_clicks：不进日活、不进事件统计
- 日活：
  - 打开设备 open_devices = user_attribution_events.app_launch 去重设备
  - 登录 UID login_uids = 当天带 user_id 的去重用户（已登录状态：登录/聊天/充值等任意埋点）
  - 日活用户 active_users = login_uids 口径的用户 + 当日全程匿名的去重设备（设备当日若已有 user_id 则只计用户）
  - 新增 new_users = users.created_at
  - 可按 app_key（trandsai / kaya）筛马甲
  - 已结束 UTC 日写入 stats_dau_snapshots 固化；当天实时算
- users：新增用户、新/老用户判定（created_at 日历日）
- user_attribution_events.product_event：事件统计（firebase_event_name 仅三方，不参与聚合）
- short_term_messages：留存活跃（用户消息）
- 漏斗：只读 user_attribution_events（人数按 user_id，没有则按 device 去重）
- recharge_records / vip_subscriptions：营收（排除 vip_gift_*）
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Session

from core.app_key import APP_KEY_OPTIONS, DEFAULT_APP_KEY, resolve_stats_app_key
from services.funnel_display import format_funnel_count_pct, pct_of_login
from core.database import (
    ButtonClickORM,
    MomentLikeORM,
    PageViewORM,
    PostLikeORM,
    RechargeRecordORM,
    ShortTermMessageORM,
    StatsDauSnapshotORM,
    UserAttributionEventORM,
    UserORM,
    VipSubscriptionORM,
)

MAX_RANGE_DAYS = 60
DEFAULT_RANGE_DAYS = 30
RETENTION_OFFSETS: Sequence[int] = (1, 2, 3, 4, 5, 6, 7, 30)

USER_TYPE_OPTIONS: List[Dict[str, str]] = [
    {"value": "all", "label": "全部"},
    {"value": "new", "label": "新用户"},
    {"value": "old", "label": "老用户"},
]

# 产品埋点英文名 -> 中文名（漏斗 / 事件表展示）；与埋点表 Sheet1 + 归因事件对齐
EVENT_ZH: Dict[str, str] = {
    "install_attribution": "首次安装",
    "login": "登录成功",
    "register_success": "注册成功",
    "purchase_success": "支付成功",
    "app_launch": "启动APP",
    "home_page_show": "首页展示成功",
    "login_page_show": "登录页展示",
    "account_login_click": "账户登录点击",
    "google_login_click": "Google登录点击",
    "apple_login_click": "Apple登录点击",
    "newbie_guide_show": "新手引导页展示",
    "send_message_click_im": "IM页面发消息按钮点击上报",
    "send_message_icon_click": "发消息icon点击",
    "send_message_click_moment": "Moment发消息按钮点击上报",
    "message_page_click": "消息页面点击上报",
    "home_robot_tab_click": "首页机器人tab点击",
    "moments_comment_click": "朋友圈评论点击",
    "moments_detail_click": "点击朋友圈详情上报",
    "avatar_profile_click": "点击头像进入profile上报",
    "pull_refresh_report": "下拉刷新上报",
    "pull_up_load_report": "上滑加载上报",
    "chat_detail_click": "点击对话详情上报",
    "community_page_click": "点击社区页面上报",
    "mine_page_click": "点击我的页面上报",
    "agent_create_click": "点击智能体创建上报",
    "agent_create_pay_popup": "智能体创建触发付费弹窗上报",
    "free_message_pay_popup": "发消息Free后触发付费弹窗上报",
    "agent_create_button_click": "点击智能体创建页面内创建按钮上报",
    "robot_list_click": "点击机器人列表上报",
    "robot_list_pull_up": "机器人列表上滑上报",
    "robot_list_pull_refresh": "机器人列表下拉刷新上报",
    "Registration_finish": "注册引导finish",
    "registration_skip": "引导跳过（用户注册成功）",
    "avatar_upload": "上传头像完成",
    "profile_displayed_15s": "智能体Profile页面展示≥15s",
    "Moment_15s": "profile-moment≥15s",
    "no_data_of_im": "每日首次打开IM未读数据为空",
    "dree_message_0": "在线180秒 free message 未使用",
    "dree_message_3": "free message 使用满3次",
    "dree_message_5": "free message 使用满5次",
    "Report_character_Click": "打开举报弹窗",
    "Report_character_submit": "确认举报",
    "Report_character_success": "确认举报成功",
    "Report_character_reason_minor_Click": "涉及未成年人",
    "Report_character_reason_sensitive_Click": "敏感话题",
    "Report_character_reason_disturbing_Click": "令人不适的内容",
    "Report_character_reason_copyright_Click": "版权侵权",
    "Report_character_reason_other_Click": "其他",
    "feedback_page_view": "进入 Feedback 页面",
    "feedback_submit_click": "点击 Feedback 提交",
    "feedback_submit_success": "反馈提交成功",
    "feedback_submit_fail": "反馈提交失败",
    "survey_fail": "调研失败",
    "research_fail": "调研失败",
    "survey_failed": "调研失败",
}

# product_event 为空时，用历史 event_type 行补齐（旧登录/安装/注册/支付）
EVENT_TYPE_FALLBACK: Dict[str, str] = {
    "install": "install_attribution",
    "first_open": "install_attribution",
    "login": "login",
    "register": "register_success",
    "purchase": "purchase_success",
}

# 埋点表备注「上报一次 / 每日首次」：同一天同一用户（或匿名设备）PV 只计 1
UNIQUE_PER_ACTOR_DAY: frozenset = frozenset(
    {
        "install_attribution",
        "register_success",
        "Registration_finish",
        "registration_skip",
        "no_data_of_im",
    }
)

try:
    from services.companion_content import PRODUCT_EVENTS as _PRODUCT_EVENTS

    for _ev in _PRODUCT_EVENTS:
        _k = (_ev.get("event_key") or "").strip()
        _zh = (_ev.get("name_zh") or "").strip()
        if _k and _zh:
            EVENT_ZH.setdefault(_k, _zh)
except Exception:
    pass

# 漏斗只读埋点表 product_event（空 product_event 时 login/purchase 走 event_type 映射）
FUNNEL_STEPS: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("login_users", "登录用户数", ("login",)),
    ("message_users", "发消息用户数", ("send_message_click_im",)),
    ("turn3_users", "3轮人数", ("dree_message_3",)),
    ("turn5_users", "5轮人数", ("dree_message_5",)),
    ("free_exhausted_users", "免费消息使用完用户数", ("send_message_click_im",)),
    (
        "pay_popup_users",
        "付费弹窗触发用户数",
        ("free_message_pay_popup", "agent_create_pay_popup"),
    ),
    ("pay_click_users", "付费点击用户数", ("paid_click",)),
    ("recharge_users", "充值成功用户数", ("purchase_success",)),
    ("survey_fail", "调研失败打点", ()),  # 暂未使用，固定 0
]


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def resolve_date_range(
    start_date: Optional[str],
    end_date: Optional[str],
    *,
    default_days: int = DEFAULT_RANGE_DAYS,
    max_days: int = MAX_RANGE_DAYS,
) -> Tuple[date, date]:
    end_d = parse_date(end_date) or _utc_today()
    start_d = parse_date(start_date) or (end_d - timedelta(days=default_days - 1))
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    if (end_d - start_d).days + 1 > max_days:
        start_d = end_d - timedelta(days=max_days - 1)
    return start_d, end_d


def normalize_user_type(user_type: Optional[str]) -> str:
    """统一为 all | new | old；非法值抛 ValueError。"""
    raw = (user_type or "all").strip().lower()
    aliases = {
        "": "all",
        "all": "all",
        "全部": "all",
        "new": "new",
        "新用户": "new",
        "new_user": "new",
        "new_users": "new",
        "old": "old",
        "老用户": "old",
        "old_user": "old",
        "old_users": "old",
    }
    # 中文直接传入时 lower 后仍是中文
    raw_orig = (user_type or "all").strip()
    if raw_orig in aliases:
        return aliases[raw_orig]
    if raw in aliases:
        return aliases[raw]
    raise ValueError("invalid_user_type")


def resolve_event_filter(event_keyword: Optional[str]) -> Tuple[Optional[str], List[str]]:
    """解析事件筛选。

    返回 (keyword_lower_or_None, exact_event_keys)。
    - 精确匹配埋点英文名 / 中文名 → 仅 exact keys
    - 模糊匹配中英文 → keys + keyword（button 名兜底）
    """
    raw = (event_keyword or "").strip()
    if not raw:
        return None, []
    if raw in EVENT_ZH:
        return None, [raw]
    zh_exact = [k for k, zh in EVENT_ZH.items() if zh == raw]
    if zh_exact:
        return None, zh_exact
    lower = raw.lower()
    exact_keys: List[str] = []
    for key, zh in EVENT_ZH.items():
        if key.lower() == lower or lower in zh.lower() or lower in key.lower():
            exact_keys.append(key)
    seen = set()
    keys = []
    for k in exact_keys:
        if k not in seen:
            seen.add(k)
            keys.append(k)
    return lower, keys


def _strip_test_prefix(event_key: str) -> str:
    """测试服 firebase 可能带 test_；product_event 一般已是裸名，脏数据也剥掉再聚合。"""
    ek = (event_key or "").strip()
    if ek.lower().startswith("test_"):
        return ek[5:]
    return ek


def canonical_stats_product_event(
    product_event: Optional[str],
    event_type: Optional[str] = None,
) -> str:
    """事件统计主键：优先 product_event，空则用历史 event_type 映射。"""
    pe = _strip_test_prefix(product_event or "")
    if pe:
        return pe
    et = (event_type or "").strip().lower()
    return EVENT_TYPE_FALLBACK.get(et, "")


def event_matches_filter(
    event_en: str,
    event_zh: str,
    kw: Optional[str],
    exact_keys: Sequence[str],
) -> bool:
    if not kw and not exact_keys:
        return True
    if exact_keys and event_en in exact_keys:
        return True
    if kw:
        blob = f"{event_en} {event_zh}".lower()
        if kw in blob:
            return True
    return False


def event_uv(uids: Set[int], anon_device_ids: Set[str]) -> int:
    """有 user_id 按用户去重；无 user_id 的按 device 去重后相加。"""
    return len(uids) + len(anon_device_ids)


def filter_options() -> Dict[str, Any]:
    """筛选区下拉选项（日期由前端 DatePicker；此处给用户类型 + 事件 + 应用）。"""
    events = [
        {"event_key": k, "name_zh": EVENT_ZH.get(k, k)}
        for k in sorted(EVENT_ZH.keys(), key=lambda x: EVENT_ZH.get(x, x))
    ]
    try:
        from services.companion_content import PRODUCT_EVENTS

        # 埋点表顺序优先
        seen = {e["event_key"] for e in PRODUCT_EVENTS}
        events = list(PRODUCT_EVENTS) + [e for e in events if e["event_key"] not in seen]
    except Exception:
        pass
    return {
        "user_types": list(USER_TYPE_OPTIONS),
        "apps": list(APP_KEY_OPTIONS),
        "app_key": {
            "param": "app_key",
            "default": DEFAULT_APP_KEY,
            "required": False,
            "note": "不传 / 空 = trandsai（含历史未打标）；传 kaya = 只统计 kaya。日活/留存/事件/漏斗/营收均适用。",
        },
        "events": events,
        "date": {
            "param_start": "start_date",
            "param_end": "end_date",
            "format": "YYYY-MM-DD",
            "max_days": MAX_RANGE_DAYS,
            "default_days": DEFAULT_RANGE_DAYS,
        },
    }


def _app_key_coalesce_sql(col: str = "app_key") -> str:
    """空值 / 空串视为默认马甲 trandsai（兼容历史未打标数据）。"""
    return f"coalesce(nullif(trim(both from {col}), ''), '{DEFAULT_APP_KEY}')"


def _sql_app_key_and(col: str, app_key: str, *, param: str = "app_key") -> str:
    """始终按马甲过滤（统计默认 trandsai）。"""
    return f" AND {_app_key_coalesce_sql(col)} = :{param}"


def _orm_app_key_eq(column, app_key: str):
    """SQLAlchemy：空标视为 trandsai 后与 app_key 相等。"""
    return func.coalesce(func.nullif(func.trim(column), ""), DEFAULT_APP_KEY) == app_key


def _users_app_key_sql(alias: str = "u") -> str:
    return _app_key_coalesce_sql(f"{alias}.app_key")


def _day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _day_end(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)


def _day_expr(col):
    return func.to_char(col, "YYYY-MM-DD")


def _iter_days(start_d: date, end_d: date) -> List[date]:
    out: List[date] = []
    cur = start_d
    while cur <= end_d:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _load_user_created_map(db: Session, user_ids: Optional[Iterable[int]] = None) -> Dict[int, date]:
    q = db.query(UserORM.id, UserORM.created_at)
    if user_ids is not None:
        ids = [int(x) for x in user_ids if x is not None]
        if not ids:
            return {}
        q = q.filter(UserORM.id.in_(ids))
    out: Dict[int, date] = {}
    for uid, created in q.all():
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        out[int(uid)] = created.astimezone(timezone.utc).date()
    return out


def _filter_uids_by_user_type(
    uids: Set[int],
    day: date,
    user_type: str,
    created_map: Dict[int, date],
) -> Set[int]:
    try:
        ut = normalize_user_type(user_type)
    except ValueError:
        ut = "all"
    if ut == "all":
        return uids
    if ut == "new":
        return {u for u in uids if created_map.get(u) == day}
    if ut == "old":
        return {u for u in uids if created_map.get(u) is not None and created_map[u] < day}
    return uids


# ---------- 概览卡片 ----------


def compute_overview(db: Session) -> Dict[str, Any]:
    from core.database import CompanionORM, KnowledgeEntryORM, UserCompanionStateORM

    companion_count = (
        db.query(func.count(CompanionORM.id))
        .filter(CompanionORM.is_deleted == False)  # noqa: E712
        .scalar()
        or 0
    )
    active_ids = [
        r[0]
        for r in db.query(CompanionORM.id).filter(CompanionORM.is_deleted == False).all()  # noqa: E712
    ]
    if active_ids:
        states = (
            db.query(UserCompanionStateORM)
            .filter(UserCompanionStateORM.companion_id.in_(active_ids))
            .all()
        )
    else:
        states = []
    total_turns = sum(s.turns or 0 for s in states)
    total_affection = sum(s.affection or 0 for s in states)
    avg_affection = (total_affection / len(states)) if states else 0.0

    user_count = db.query(func.count(UserORM.id)).scalar() or 0

    total_pv = db.query(func.count(PageViewORM.id)).scalar() or 0
    device_cnt = db.query(func.count(func.distinct(PageViewORM.device_id))).scalar() or 0
    avg_open_count = round(float(total_pv) / device_cnt, 2) if device_cnt else 0.0

    like_uids = {
        int(uid)
        for (uid,) in db.query(MomentLikeORM.user_id).filter(MomentLikeORM.user_id.isnot(None)).distinct()
    }
    try:
        like_uids |= {
            int(uid)
            for (uid,) in db.query(PostLikeORM.user_id).filter(PostLikeORM.user_id.isnot(None)).distinct()
        }
    except Exception:
        pass

    msg_uids = {
        int(uid)
        for (uid,) in db.query(ShortTermMessageORM.user_id)
        .filter(ShortTermMessageORM.user_id.isnot(None), ShortTermMessageORM.role == "user")
        .distinct()
        .all()
    }

    kb_count = db.query(func.count(KnowledgeEntryORM.id)).scalar() or 0

    return {
        "companion_count": int(companion_count),
        "total_turns": int(total_turns),
        "avg_affection": round(float(avg_affection), 1),
        "knowledge_count": int(kb_count),
        "user_count": int(user_count),
        "avg_open_count": avg_open_count,
        "like_user_count": len(like_uids),
        "message_user_count": len(msg_uids),
        "definitions": {
            "companion_count": "未软删智能体数量",
            "total_turns": "用户-智能体累计对话轮数",
            "avg_affection": "用户-智能体平均亲密度",
            "knowledge_count": "知识库条目数",
            "user_count": "用户总数（与用户管理列表一致，含管理员/封禁/注销）",
            "avg_open_count": "总页面浏览量 / 去重设备数",
            "like_user_count": "点赞过朋友圈或帖子的去重用户数",
            "message_user_count": "发送过聊天消息的去重用户数",
        },
    }


# ---------- 日活 ----------


def _compute_dau_live_maps(
    db: Session,
    start_d: date,
    end_d: date,
    app_filter: str,
) -> Dict[str, Dict[str, int]]:
    """现算日活指标；key=YYYY-MM-DD。

    active_users = 去重 user_id
                 + 当日该设备全部事件均无 user_id 的去重 device_id
    （同设备当日一旦出现 user_id，只计用户、不计设备，避免双计）
    """
    start_dt, end_dt = _day_start(start_d), _day_end(end_d)
    params: Dict[str, Any] = {"s": start_dt, "e": end_dt, "app_key": app_filter}
    attr_app = _sql_app_key_and("app_key", app_filter)
    user_app = _sql_app_key_and("app_key", app_filter)
    # 与过滤区间一致的 UTC 日历日
    day_expr = (
        "to_char((created_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', 'YYYY-MM-DD')"
    )

    device_sql = text(
        f"""
        SELECT {day_expr} AS day,
               COUNT(DISTINCT device_id) AS devices
        FROM user_attribution_events
        WHERE created_at >= :s AND created_at <= :e
          AND coalesce(device_id, '') <> ''
          AND product_event IN ('app_launch', 'test_app_launch')
          {attr_app}
        GROUP BY 1
        """
    )
    device_map = {
        r.day: int(r.devices)
        for r in db.execute(device_sql, params).fetchall()
    }

    # 登录 UID：当天处于登录态的去重用户（有 user_id 的任意埋点：登录/聊天/充值等）
    uid_sql = text(
        f"""
        SELECT {day_expr} AS day,
               COUNT(DISTINCT user_id) AS uids
        FROM user_attribution_events
        WHERE created_at >= :s AND created_at <= :e
          AND user_id IS NOT NULL
          {attr_app}
        GROUP BY 1
        """
    )
    uid_map = {
        r.day: int(r.uids) for r in db.execute(uid_sql, params).fetchall()
    }

    # 日活 = 已登录用户 ∪ 当日全程匿名设备（人+匿名设备去重）
    active_sql = text(
        f"""
        WITH day_ev AS (
            SELECT
                {day_expr} AS day,
                user_id,
                coalesce(device_id, '') AS device_id
            FROM user_attribution_events
            WHERE created_at >= :s AND created_at <= :e
              {attr_app}
        ),
        user_part AS (
            SELECT day, COUNT(DISTINCT user_id) AS cnt
            FROM day_ev
            WHERE user_id IS NOT NULL
            GROUP BY day
        ),
        anon_part AS (
            SELECT day, COUNT(*) AS cnt
            FROM (
                SELECT day, device_id
                FROM day_ev
                WHERE device_id <> ''
                GROUP BY day, device_id
                HAVING bool_and(user_id IS NULL)
            ) a
            GROUP BY day
        )
        SELECT
            coalesce(u.day, a.day) AS day,
            coalesce(u.cnt, 0) + coalesce(a.cnt, 0) AS uids
        FROM user_part u
        FULL OUTER JOIN anon_part a ON u.day = a.day
        """
    )
    active_map = {
        r.day: int(r.uids) for r in db.execute(active_sql, params).fetchall()
    }

    new_sql = text(
        f"""
        SELECT {day_expr} AS day,
               COUNT(*) AS cnt
        FROM users
        WHERE created_at >= :s AND created_at <= :e
          AND (role IS NULL OR role = '' OR role = 'user')
          {user_app}
        GROUP BY 1
        """
    )
    new_map = {
        r.day: int(r.cnt) for r in db.execute(new_sql, params).fetchall()
    }

    out: Dict[str, Dict[str, int]] = {}
    for d in _iter_days(start_d, end_d):
        key = d.strftime("%Y-%m-%d")
        out[key] = {
            "open_devices": device_map.get(key, 0),
            "login_uids": uid_map.get(key, 0),
            "active_users": active_map.get(key, 0),
            "new_users": new_map.get(key, 0),
        }
    return out


def _persist_dau_snapshots(
    db: Session,
    app_filter: str,
    day_metrics: Dict[str, Dict[str, int]],
) -> None:
    """写入缺失的已结束日快照；并发唯一冲突用 savepoint 忽略。"""
    if not day_metrics:
        return
    from sqlalchemy.exc import IntegrityError

    now = datetime.now(timezone.utc)
    for key, m in day_metrics.items():
        try:
            d = datetime.strptime(key, "%Y-%m-%d").date()
        except ValueError:
            continue
        exists = (
            db.query(StatsDauSnapshotORM.id)
            .filter(
                StatsDauSnapshotORM.stat_date == d,
                StatsDauSnapshotORM.app_key == app_filter,
            )
            .first()
        )
        if exists:
            continue
        try:
            with db.begin_nested():
                db.add(
                    StatsDauSnapshotORM(
                        stat_date=d,
                        app_key=app_filter,
                        open_devices=int(m.get("open_devices") or 0),
                        login_uids=int(m.get("login_uids") or 0),
                        active_users=int(m.get("active_users") or 0),
                        new_users=int(m.get("new_users") or 0),
                        created_at=now,
                        updated_at=now,
                    )
                )
                db.flush()
        except IntegrityError:
            continue


def compute_dau_rows(
    db: Session,
    start_d: date,
    end_d: date,
    *,
    app_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """日活：已结束的 UTC 日读/写快照；当天实时算。

    - open_devices：当日 app_launch 去重设备
    - login_uids：当天带 user_id 的去重用户（已登录态：登录事件或其他埋点均可）
    - active_users：login_uids 用户 + 当日全程匿名的去重设备
      （同设备当日已有 user_id 则只计用户，不另计设备）
    - new_users：当日注册用户数
    - app_key：不传默认 trandsai（空标计入）；kaya 仅显式 kaya

    快照：首次访问某已结束日时按上述口径现算并写入 stats_dau_snapshots，之后固定。
    """
    app_filter = resolve_stats_app_key(app_key)
    today = _utc_today()
    days = _iter_days(start_d, end_d)
    past_days = [d for d in days if d < today]
    live_days = [d for d in days if d >= today]

    snap_map: Dict[date, StatsDauSnapshotORM] = {}
    if past_days:
        rows_db = (
            db.query(StatsDauSnapshotORM)
            .filter(
                StatsDauSnapshotORM.app_key == app_filter,
                StatsDauSnapshotORM.stat_date >= past_days[0],
                StatsDauSnapshotORM.stat_date <= past_days[-1],
            )
            .all()
        )
        snap_map = {r.stat_date: r for r in rows_db}

    missing_past = [d for d in past_days if d not in snap_map]
    live_need = list(missing_past) + live_days
    live_map: Dict[str, Dict[str, int]] = {}
    if live_need:
        live_map = _compute_dau_live_maps(
            db, min(live_need), max(live_need), app_filter
        )
        if missing_past:
            to_save = {
                d.strftime("%Y-%m-%d"): live_map.get(
                    d.strftime("%Y-%m-%d"),
                    {
                        "open_devices": 0,
                        "login_uids": 0,
                        "active_users": 0,
                        "new_users": 0,
                    },
                )
                for d in missing_past
            }
            _persist_dau_snapshots(db, app_filter, to_save)
            rows_db = (
                db.query(StatsDauSnapshotORM)
                .filter(
                    StatsDauSnapshotORM.app_key == app_filter,
                    StatsDauSnapshotORM.stat_date.in_(missing_past),
                )
                .all()
            )
            for r in rows_db:
                snap_map[r.stat_date] = r

    zero = {
        "open_devices": 0,
        "login_uids": 0,
        "active_users": 0,
        "new_users": 0,
    }
    rows: List[Dict[str, Any]] = []
    for d in days:
        key = d.strftime("%Y-%m-%d")
        if d < today and d in snap_map:
            s = snap_map[d]
            rows.append(
                {
                    "date": key,
                    "app_key": app_filter,
                    "open_devices": int(s.open_devices or 0),
                    "login_uids": int(s.login_uids or 0),
                    "active_users": int(s.active_users or 0),
                    "new_users": int(s.new_users or 0),
                }
            )
        else:
            m = live_map.get(key, zero)
            rows.append(
                {
                    "date": key,
                    "app_key": app_filter,
                    "open_devices": int(m["open_devices"]),
                    "login_uids": int(m["login_uids"]),
                    "active_users": int(m["active_users"]),
                    "new_users": int(m["new_users"]),
                }
            )
    rows.reverse()
    return rows


# ---------- 留存 ----------


def compute_retention_rows(
    db: Session,
    start_d: date,
    end_d: date,
    *,
    app_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """按注册日 cohort（user_id）的留存宽表；活跃=当日有 page_view/button_click/发消息。

    app_key 不传默认 trandsai（users 空标计入）；kaya 只看 kaya 用户。
    """
    start_dt, end_dt = _day_start(start_d), _day_end(end_d)
    app_filter = resolve_stats_app_key(app_key)
    params: Dict[str, Any] = {"s": start_dt, "e": end_dt, "app_key": app_filter}
    # 需要查到 end+30 的活跃，才能算 d30
    act_end = _day_end(end_d + timedelta(days=max(RETENTION_OFFSETS)))
    act_params: Dict[str, Any] = {"s": start_dt, "e": act_end, "app_key": app_filter}
    user_app = _sql_app_key_and("u.app_key", app_filter)

    cohort_sql = text(
        f"""
        SELECT u.id AS user_id,
               to_char(u.created_at, 'YYYY-MM-DD') AS cohort_day
        FROM users u
        WHERE u.created_at >= :s AND u.created_at <= :e
          AND (u.role IS NULL OR u.role = '' OR u.role = 'user')
          {user_app}
        """
    )
    cohorts: Dict[str, Set[int]] = {}
    for r in db.execute(cohort_sql, params).fetchall():
        cohorts.setdefault(r.cohort_day, set()).add(int(r.user_id))

    act_sql = text(
        f"""
        SELECT t.user_id, to_char(t.created_at, 'YYYY-MM-DD') AS day
        FROM (
            SELECT pv.user_id, pv.created_at FROM page_views pv
            INNER JOIN users u ON u.id = pv.user_id
            WHERE pv.created_at >= :s AND pv.created_at <= :e AND pv.user_id IS NOT NULL
              {user_app}
            UNION ALL
            SELECT bc.user_id, bc.created_at FROM button_clicks bc
            INNER JOIN users u ON u.id = bc.user_id
            WHERE bc.created_at >= :s AND bc.created_at <= :e AND bc.user_id IS NOT NULL
              {user_app}
            UNION ALL
            SELECT sm.user_id, sm.created_at FROM short_term_messages sm
            INNER JOIN users u ON u.id = sm.user_id
            WHERE sm.created_at >= :s AND sm.created_at <= :e
              AND sm.user_id IS NOT NULL AND sm.role = 'user'
              {user_app}
        ) t
        GROUP BY t.user_id, day
        """
    )
    active: Dict[int, Set[str]] = {}
    for r in db.execute(act_sql, act_params).fetchall():
        active.setdefault(int(r.user_id), set()).add(r.day)

    rows: List[Dict[str, Any]] = []
    for d in _iter_days(start_d, end_d):
        key = d.strftime("%Y-%m-%d")
        users = cohorts.get(key, set())
        n = len(users)
        entry: Dict[str, Any] = {
            "cohort_date": key,
            "app_key": app_filter,
            "new_users": n,
            "d0_count": n,
            "d0_rate": 100.0 if n else 0.0,
        }
        for off in RETENTION_OFFSETS:
            target = (d + timedelta(days=off)).strftime("%Y-%m-%d")
            cnt = sum(1 for u in users if target in active.get(u, set()))
            entry[f"d{off}_count"] = cnt
            entry[f"d{off}_rate"] = round(100.0 * cnt / n, 2) if n else 0.0
        rows.append(entry)
    rows.reverse()
    return rows


# ---------- 事件 ----------


def aggregate_product_event_rows(
    records: Iterable[Tuple[Any, Any, Any, Any, Any]],
    *,
    user_type: str = "all",
    kw: Optional[str] = None,
    exact_keys: Optional[Sequence[str]] = None,
    created_map: Optional[Dict[int, date]] = None,
    start_d: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """把归因行聚成按日 + product_event 的 PV/UV。

    records: (day, product_event, event_type, user_id, device_id)
    不去读 firebase_event_name，也不混 page_views / button_clicks。
    """
    created_map = created_map or {}
    exact_keys = list(exact_keys or [])
    agg: Dict[Tuple[str, str], Dict[str, Any]] = {}
    seen_unique: Set[Tuple[str, str, str]] = set()

    for day, product_event, event_type, uid, device_id in records:
        day_s = str(day or "").strip()
        event_en = canonical_stats_product_event(product_event, event_type)
        if not day_s or not event_en:
            continue
        event_zh = EVENT_ZH.get(event_en, event_en)
        if not event_matches_filter(event_en, event_zh, kw, exact_keys):
            continue
        if user_type != "all":
            if not uid:
                continue
            filtered = _filter_uids_by_user_type(
                {int(uid)}, parse_date(day_s) or start_d or date.today(), user_type, created_map
            )
            if not filtered:
                continue
        actor = ""
        if uid:
            actor = f"u:{int(uid)}"
        elif device_id:
            actor = f"d:{str(device_id)}"
        if event_en in UNIQUE_PER_ACTOR_DAY and actor:
            uniq_key = (day_s, event_en, actor)
            if uniq_key in seen_unique:
                continue
            seen_unique.add(uniq_key)
        key = (day_s, event_en)
        slot = agg.get(key)
        if not slot:
            slot = {
                "date": day_s,
                "event_name_en": event_en,
                "event_name_zh": event_zh,
                "pv": 0,
                "_uids": set(),
                "_devs": set(),
            }
            agg[key] = slot
        slot["pv"] += 1
        if uid:
            slot["_uids"].add(int(uid))
        elif device_id:
            slot["_devs"].add(str(device_id))

    rows: List[Dict[str, Any]] = []
    for slot in agg.values():
        uids = slot.pop("_uids")
        devs = slot.pop("_devs")
        rows.append(
            {
                "date": slot["date"],
                "event_name_zh": slot["event_name_zh"],
                "event_name_en": slot["event_name_en"],
                "pv": slot["pv"],
                "uv": event_uv(uids, devs),
            }
        )
    rows.sort(key=lambda x: (x["date"], x["pv"]), reverse=True)
    return rows


def compute_event_rows(
    db: Session,
    start_d: date,
    end_d: date,
    *,
    user_type: str = "all",
    event_keyword: Optional[str] = None,
    app_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """按「日期 + product_event」聚合 PV/UV，只读 user_attribution_events。

    筛选：
    - user_type: all / new / old（新用户=注册日=统计日）
    - event: 英文 event_key 精确匹配，或中文名/关键词模糊匹配
    - app_key: 不传默认 trandsai；kaya 仅 kaya
    """
    start_dt, end_dt = _day_start(start_d), _day_end(end_d)
    app_filter = resolve_stats_app_key(app_key)
    try:
        ut = normalize_user_type(user_type)
    except ValueError:
        ut = "all"
    kw, exact_keys = resolve_event_filter(event_keyword)

    fallback_types = list(EVENT_TYPE_FALLBACK.keys())
    prod_q = db.query(
        _day_expr(UserAttributionEventORM.created_at).label("day"),
        UserAttributionEventORM.product_event,
        UserAttributionEventORM.event_type,
        UserAttributionEventORM.user_id,
        UserAttributionEventORM.device_id,
    ).filter(
        UserAttributionEventORM.created_at >= start_dt,
        UserAttributionEventORM.created_at <= end_dt,
        _orm_app_key_eq(UserAttributionEventORM.app_key, app_filter),
        or_(
            UserAttributionEventORM.product_event != "",
            UserAttributionEventORM.event_type.in_(fallback_types),
        ),
    )
    created_map = _load_user_created_map(db)
    rows = aggregate_product_event_rows(
        prod_q.all(),
        user_type=ut,
        kw=kw,
        exact_keys=exact_keys,
        created_map=created_map,
        start_d=start_d,
    )
    for row in rows:
        row["app_key"] = app_filter
    return rows


# ---------- 漏斗 ----------


def _funnel_actor(user_id: Any, device_id: Any) -> str:
    """有 user_id 按用户去重；否则按设备。同一人当天多次只算 1。"""
    if user_id:
        return f"u:{int(user_id)}"
    did = str(device_id or "").strip()
    if did:
        return f"d:{did}"
    return ""


def _filter_funnel_actors(
    actors: Set[str],
    day: date,
    user_type: str,
    created_map: Dict[int, date],
) -> Set[str]:
    if user_type == "all":
        return actors
    uids = {int(a[2:]) for a in actors if a.startswith("u:")}
    kept = _filter_uids_by_user_type(uids, day, user_type, created_map)
    return {f"u:{u}" for u in kept}


def free_quota_exhausted_days(
    daily_counts: Iterable[Tuple[str, str, int]],
    *,
    free_limit: int,
) -> Dict[str, Set[str]]:
    """按人累计 send_message_click_im，首次达到免费次数的那一天记 1 人。

    daily_counts: (day YYYY-MM-DD, actor, count)。
    例如免费 3 次：1 号 1 条、2 号 2 条，累计到 2 号刚好 3，记在 2 号。
    一次跨过（大于免费次数）也只记 1 人，记在跨过的那天。
    """
    limit = int(free_limit or 0)
    out: Dict[str, Set[str]] = {}
    if limit <= 0:
        return out
    by_actor: Dict[str, Dict[str, int]] = {}
    for day, actor, cnt in daily_counts:
        day_s = str(day or "").strip()
        actor_s = str(actor or "").strip()
        if not day_s or not actor_s:
            continue
        bucket = by_actor.setdefault(actor_s, {})
        bucket[day_s] = bucket.get(day_s, 0) + int(cnt or 0)
    for actor, days in by_actor.items():
        running = 0
        for day_s in sorted(days):
            prev = running
            running += days[day_s]
            if prev < limit <= running:
                out.setdefault(day_s, set()).add(actor)
                break
    return out


def _product_event_name_variants(names: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for name in names:
        for variant in (name, f"test_{name}"):
            if variant not in seen:
                seen.add(variant)
                out.append(variant)
    return out


def compute_funnel_rows(
    db: Session,
    start_d: date,
    end_d: date,
    *,
    user_type: str = "all",
    app_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """漏斗按日聚合，只读 user_attribution_events。user_type=all|new|old。

    app_key 不传默认 trandsai；kaya 仅 kaya。
    """
    start_dt, end_dt = _day_start(start_d), _day_end(end_d)
    app_filter = resolve_stats_app_key(app_key)
    try:
        ut = normalize_user_type(user_type)
    except ValueError:
        ut = "all"
    created_map = _load_user_created_map(db)

    range_events = (
        "login",
        "send_message_click_im",
        "dree_message_3",
        "dree_message_5",
        "agent_create_pay_popup",
        "free_message_pay_popup",
        "paid_click",
        "purchase_success",
    )
    # 历史行：product_event 为空时，event_type=login/purchase 映射成对应事件
    legacy_types = ("login", "purchase")
    q = (
        db.query(
            _day_expr(UserAttributionEventORM.created_at).label("day"),
            UserAttributionEventORM.product_event,
            UserAttributionEventORM.event_type,
            UserAttributionEventORM.user_id,
            UserAttributionEventORM.device_id,
        )
        .filter(
            UserAttributionEventORM.created_at >= start_dt,
            UserAttributionEventORM.created_at <= end_dt,
            _orm_app_key_eq(UserAttributionEventORM.app_key, app_filter),
            or_(
                UserAttributionEventORM.product_event.in_(_product_event_name_variants(range_events)),
                and_(
                    or_(
                        UserAttributionEventORM.product_event == "",
                        UserAttributionEventORM.product_event.is_(None),
                    ),
                    UserAttributionEventORM.event_type.in_(legacy_types),
                ),
            ),
        )
    )
    by_event: Dict[str, Dict[str, Set[str]]] = {}
    for day, product_event, event_type, uid, device_id in q.all():
        event_en = canonical_stats_product_event(product_event, event_type)
        if event_en not in range_events:
            continue
        actor = _funnel_actor(uid, device_id)
        if not actor:
            continue
        by_event.setdefault(event_en, {}).setdefault(str(day), set()).add(actor)

    def _actors(event_name: str, day_key: str) -> Set[str]:
        return set(by_event.get(event_name, {}).get(day_key, set()))

    send_names = _product_event_name_variants(("send_message_click_im",))
    hist_rows = (
        db.query(
            _day_expr(UserAttributionEventORM.created_at).label("day"),
            UserAttributionEventORM.user_id,
            UserAttributionEventORM.device_id,
            func.count(UserAttributionEventORM.id),
        )
        .filter(
            UserAttributionEventORM.created_at <= end_dt,
            UserAttributionEventORM.product_event.in_(send_names),
            _orm_app_key_eq(UserAttributionEventORM.app_key, app_filter),
        )
        .group_by(
            _day_expr(UserAttributionEventORM.created_at),
            UserAttributionEventORM.user_id,
            UserAttributionEventORM.device_id,
        )
        .all()
    )
    from services.credit_service import get_cost_config

    free_limit = int((get_cost_config() or {}).get("free_turns_limit") or 0)
    exhausted = free_quota_exhausted_days(
        (
            (str(day), _funnel_actor(uid, did), int(cnt or 0))
            for day, uid, did, cnt in hist_rows
            if _funnel_actor(uid, did)
        ),
        free_limit=free_limit,
    )

    rows: List[Dict[str, Any]] = []
    for d in _iter_days(start_d, end_d):
        key = d.strftime("%Y-%m-%d")

        def _n(actors: Set[str]) -> int:
            return len(_filter_funnel_actors(actors, d, ut, created_map))

        login_n = _n(_actors("login", key))
        msg_n = _n(_actors("send_message_click_im", key))
        t3 = _n(_actors("dree_message_3", key))
        t5 = _n(_actors("dree_message_5", key))
        popup_n = _n(_actors("agent_create_pay_popup", key) | _actors("free_message_pay_popup", key))
        free_n = _n(exhausted.get(key, set()))
        pay_click_n = _n(_actors("paid_click", key))
        recharge_n = _n(_actors("purchase_success", key))
        survey_n = 0
        rows.append(
            {
                "date": key,
                "app_key": app_filter,
                "login_users": login_n,
                "login_users_display": format_funnel_count_pct(
                    login_n, login_n, is_login_col=True
                ),
                "message_users": msg_n,
                "message_users_display": format_funnel_count_pct(msg_n, login_n),
                "turn3_users": t3,
                "turn3_rate": pct_of_login(t3, login_n),
                "turn3_display": format_funnel_count_pct(t3, login_n),
                "turn5_users": t5,
                "turn5_rate": pct_of_login(t5, login_n),
                "turn5_display": format_funnel_count_pct(t5, login_n),
                "free_exhausted_users": free_n,
                "free_exhausted_display": format_funnel_count_pct(free_n, login_n),
                "pay_popup_users": popup_n,
                "pay_popup_display": format_funnel_count_pct(popup_n, login_n),
                "pay_click_users": pay_click_n,
                "pay_click_display": format_funnel_count_pct(pay_click_n, login_n),
                "recharge_users": recharge_n,
                "recharge_display": format_funnel_count_pct(recharge_n, login_n),
                "survey_fail": survey_n,
                "survey_fail_display": format_funnel_count_pct(survey_n, login_n),
            }
        )
    rows.reverse()
    return rows


# ---------- 营收 ----------


def compute_revenue_rows(
    db: Session,
    start_d: date,
    end_d: date,
    *,
    app_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    start_dt, end_dt = _day_start(start_d), _day_end(end_d)
    app_filter = resolve_stats_app_key(app_key)
    params: Dict[str, Any] = {"s": start_dt, "e": end_dt, "app_key": app_filter}
    user_app = _sql_app_key_and("u.app_key", app_filter)
    attr_app = _sql_app_key_and("e.app_key", app_filter)
    created_map = _load_user_created_map(db)

    # 充值金额 + 付费用户（按用户马甲）
    rev_sql = text(
        f"""
        SELECT day, user_id, SUM(amount) AS amount
        FROM (
            SELECT to_char(r.created_at, 'YYYY-MM-DD') AS day,
                   r.user_id, coalesce(r.price_usd,0) AS amount
            FROM recharge_records r
            INNER JOIN users u ON u.id = r.user_id
            WHERE r.created_at >= :s AND r.created_at <= :e
              AND r.package_key NOT LIKE 'vip_gift_%'
              {user_app}
            UNION ALL
            SELECT to_char(v.created_at, 'YYYY-MM-DD') AS day,
                   v.user_id, coalesce(v.price_usd,0) AS amount
            FROM vip_subscriptions v
            INNER JOIN users u ON u.id = v.user_id
            WHERE v.created_at >= :s AND v.created_at <= :e
              {user_app}
        ) t
        GROUP BY day, user_id
        """
    )
    day_pay: Dict[str, Dict[int, float]] = {}
    for r in db.execute(rev_sql, params).fetchall():
        day_pay.setdefault(r.day, {})[int(r.user_id)] = float(r.amount or 0)

    # 当日活跃用户（分母）：按用户马甲；归因表额外按事件 app_key
    active_sql = text(
        f"""
        SELECT to_char(created_at, 'YYYY-MM-DD') AS day, user_id
        FROM (
            SELECT pv.created_at, pv.user_id FROM page_views pv
            INNER JOIN users u ON u.id = pv.user_id
            WHERE pv.created_at >= :s AND pv.created_at <= :e AND pv.user_id IS NOT NULL
              {user_app}
            UNION ALL
            SELECT bc.created_at, bc.user_id FROM button_clicks bc
            INNER JOIN users u ON u.id = bc.user_id
            WHERE bc.created_at >= :s AND bc.created_at <= :e AND bc.user_id IS NOT NULL
              {user_app}
            UNION ALL
            SELECT sm.created_at, sm.user_id FROM short_term_messages sm
            INNER JOIN users u ON u.id = sm.user_id
            WHERE sm.created_at >= :s AND sm.created_at <= :e
              AND sm.user_id IS NOT NULL AND sm.role='user'
              {user_app}
            UNION ALL
            SELECT e.created_at, e.user_id FROM user_attribution_events e
            WHERE e.created_at >= :s AND e.created_at <= :e AND e.user_id IS NOT NULL
              {attr_app}
        ) t
        GROUP BY 1,2
        """
    )
    day_active: Dict[str, Set[int]] = {}
    for r in db.execute(active_sql, params).fetchall():
        day_active.setdefault(r.day, set()).add(int(r.user_id))

    rows: List[Dict[str, Any]] = []
    for d in _iter_days(start_d, end_d):
        key = d.strftime("%Y-%m-%d")
        pay_map = day_pay.get(key, {})
        active = day_active.get(key, set()) | set(pay_map.keys())
        new_active = {u for u in active if created_map.get(u) == d}
        old_active = {u for u in active if created_map.get(u) is not None and created_map[u] < d}

        def _metrics(pool: Set[int]) -> Dict[str, Any]:
            payers = {u for u in pay_map if u in pool}
            revenue = sum(pay_map[u] for u in payers)
            n_pool = len(pool)
            n_pay = len(payers)
            return {
                "users": n_pool,
                "paying_users": n_pay,
                "pay_rate": round(100.0 * n_pay / n_pool, 2) if n_pool else 0.0,
                "revenue": round(revenue, 2),
                "arppu": round(revenue / n_pay, 2) if n_pay else 0.0,
                "arpu": round(revenue / n_pool, 2) if n_pool else 0.0,
            }

        all_m = _metrics(active)
        new_m = _metrics(new_active)
        old_m = _metrics(old_active)
        rows.append(
            {
                "date": key,
                "app_key": app_filter,
                "total_revenue": all_m["revenue"],
                "all_pay_rate": all_m["pay_rate"],
                "all_paying_users": all_m["paying_users"],
                "all_users": all_m["users"],
                "all_arppu": all_m["arppu"],
                "all_arpu": all_m["arpu"],
                "new_pay_rate": new_m["pay_rate"],
                "new_paying_users": new_m["paying_users"],
                "new_users": new_m["users"],
                "new_arppu": new_m["arppu"],
                "new_arpu": new_m["arpu"],
                "old_pay_rate": old_m["pay_rate"],
                "old_paying_users": old_m["paying_users"],
                "old_users": old_m["users"],
                "old_arppu": old_m["arppu"],
                "old_arpu": old_m["arpu"],
            }
        )
    rows.reverse()
    return rows


def slice_page(items: List[Any], page: int, page_size: int) -> Tuple[List[Any], int, int, int]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 10), 200))
    total = len(items)
    start = (page - 1) * page_size
    return items[start : start + page_size], total, page, page_size
