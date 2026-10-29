"""消息策略触发执行引擎。

自动发送链路（线性先后，非包含）：
  埋点 product_event
    → ① 埋点触发配置（rules）：事件未配/0 不触发；用户×智能体×事件达 N 则停
    → ② 全局配置检测（globals）：仅当用户范围命中才限制
       · 未配置 / 未命中用户范围：不限制
       · daily_limit：当天所有事件合计上限（≤0 不限制）；指定智能体事件不占用此合计
       · interval：不同事件同时触发时的下发间隔（≤0 不限制）
       · fatigue_window + fatigue_max：周期内合计上限（任一为 0 不限制）；指定智能体事件不占用
    → ③ 文案入队 outreach_jobs
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from core.database import (
    CompanionORM,
    CompanionStrategyORM,
    MomentORM,
    MsgStrategyDispatchLogORM,
    MsgStrategyGlobalORM,
    MsgStrategyLibraryORM,
    MsgStrategyUserCompanionEventORM,
    OutreachJobORM,
    UserORM,
    get_db,
)
from core.log_util import pipeline_log
from services.msg_strategy_crud import (
    pick_best_global,
    pick_matching_rules,
)
from services.msg_strategy_meta import (
    canonicalize_product_event,
    event_keys_for_trigger,
    event_to_trigger_keys,
    expand_library_type_keys,
    normalize_triggers,
    trigger_to_type_keys,
)
from services.strategy_types import COMPANION_BOUND_EVENT_KEYS

logger = logging.getLogger(__name__)

JOB_MSG_STRATEGY = "msg_strategy"

# 指定智能体事件：只对上报 companion_id（或 moment_id 反查）下发，不随机全局 1～3
COMPANION_ID_PRIORITY_EVENTS = COMPANION_BOUND_EVENT_KEYS

# 聊天免费次数累计满 3 / 5 次：服务端自动下发；未配规则时仍用策略库文案
FREE_TURN_MILESTONE_EVENTS = {3: "dree_message_3", 5: "dree_message_5"}
LIBRARY_FALLBACK_EVENTS = frozenset(FREE_TURN_MILESTONE_EVENTS.values())


def free_turn_milestone_event(used: int) -> str:
    """累计已用免费轮次刚好等于 3 或 5 时返回对应埋点，否则空串。"""
    try:
        n = int(used)
    except (TypeError, ValueError):
        return ""
    return FREE_TURN_MILESTONE_EVENTS.get(n, "")


def is_companion_bound_event(event_key: str) -> bool:
    ek = canonicalize_product_event(event_key)
    return ek in COMPANION_BOUND_EVENT_KEYS


def companion_id_priority_applies(event_key: str, event_companion_id: str) -> bool:
    cid = (event_companion_id or "").strip()
    return bool(cid) and is_companion_bound_event(event_key)


def _companion_id_from_moment(db: Session, moment_id: Any) -> str:
    try:
        mid = int(moment_id)
    except (TypeError, ValueError):
        return ""
    if mid <= 0:
        return ""
    row = db.query(MomentORM.companion_id).filter(MomentORM.id == mid).first()
    if not row:
        return ""
    return (row[0] or "").strip()[:8]


def library_companion_pick_count(eligible_count: int, remaining: int) -> int:
    """策略库随机选取智能体数量：1～min(3, 可用数, 剩余额度)。"""
    if eligible_count <= 0 or remaining <= 0:
        return 0
    upper = min(3, eligible_count, remaining)
    return random.randint(1, upper)


def rule_want_count(
    triggers: Dict[str, int],
    chosen_trigger: str,
    event: str,
    trigger_keys: List[str],
) -> int:
    """规则 triggers[event]：该用户×智能体×事件终身上限。未填/0 → 0（不触发）。"""
    seen: set = set()
    for key in [chosen_trigger, event, *trigger_keys]:
        k = (key or "").strip()
        if not k or k in seen:
            continue
        seen.add(k)
        n = int(triggers.get(k) or 0)
        if n > 0:
            return n
    return 0


def event_keys_for_daily_quota(
    *,
    event: str,
    raw_event: str = "",
    trigger_keys: Optional[List[str]] = None,
) -> List[str]:
    """匹配日志 / 计数表 event_key、trigger_key 的候选（含 raw / canonical / 别名）。"""
    out: List[str] = []
    for key in [raw_event, event, *(trigger_keys or [])]:
        k = (key or "").strip()
        if k and k not in out:
            out.append(k)
        canon = canonicalize_product_event(k) if k else ""
        if canon and canon not in out:
            out.append(canon)
    return out


def _day_start_naive_utc() -> datetime:
    return _utcnow().replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)


def _acquire_user_event_lock(db: Session, user_id: int, event: str) -> None:
    """同一用户+事件串行化额度扣减，避免并发埋点都读到 sent=0 而超发。"""
    ek = (event or "").strip() or "_"
    # 两段 int4 advisory lock：user_id + event crc
    import zlib

    ev_key = zlib.crc32(ek.encode("utf-8")) & 0x7FFFFFFF
    try:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:uid, :ek)"),
            {"uid": int(user_id), "ek": int(ev_key)},
        )
        return
    except Exception:
        logger.debug("[msg_strategy] pg advisory lock unavailable, try redis", exc_info=True)
    try:
        from core.redis_client import get_redis_client

        r = get_redis_client()
        if not r:
            return
        lock_key = f"msg_strategy:lock:{int(user_id)}:{ek}"
        # 短锁：事务内忙等；拿不到也继续（降级），至少 PG 路径可靠
        for _ in range(40):
            if r.set(lock_key, "1", nx=True, ex=5):
                # 挂在 session.info，commit/rollback 后尽量释放
                locks = db.info.setdefault("_msg_strategy_redis_locks", [])
                locks.append(lock_key)
                return
            import time as _time

            _time.sleep(0.05)
    except Exception:
        logger.debug("[msg_strategy] redis lock failed", exc_info=True)


def _release_user_event_redis_locks(db: Session) -> None:
    locks = (getattr(db, "info", None) or {}).pop("_msg_strategy_redis_locks", None) or []
    if not locks:
        return
    try:
        from core.redis_client import get_redis_client

        r = get_redis_client()
        if not r:
            return
        for k in locks:
            try:
                r.delete(k)
            except Exception:
                pass
    except Exception:
        pass


def _active_companion(
    db: Session,
    companion_id: str,
    *,
    require_lang: str = "",
) -> Optional[CompanionORM]:
    """可用智能体；require_lang 非空时必须 companions.language 与用户语种一致。"""
    cid = (companion_id or "").strip()
    if not cid:
        return None
    comp = (
        db.query(CompanionORM)
        .filter(CompanionORM.id == cid, CompanionORM.is_deleted.isnot(True))
        .first()
    )
    if not comp or not getattr(comp, "is_active", True):
        return None
    if require_lang and not _companion_matches_user_lang(comp, require_lang):
        return None
    return comp


def _companion_matches_user_lang(comp: Optional[CompanionORM], user_lang: str) -> bool:
    """users.language（目标）与 companions.language（匹配）短码一致才可主动推送。"""
    if not comp:
        return False
    want = _normalize_locale(user_lang, fallback="")
    if not want:
        return False
    got = _normalize_locale(getattr(comp, "language", None) or "", fallback="")
    return bool(got) and got == want


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_locale(locale: str, fallback: str = "") -> str:
    loc = (locale or "").strip()
    if not loc:
        return fallback
    try:
        from services.companion_content import canonical_content_language

        code = canonical_content_language(loc)
        if code:
            return code
    except Exception:
        pass
    return loc.split("-")[0].lower()[:10] or fallback


def _user_language(user: Optional[UserORM], fallback: str = "") -> str:
    """优先 users.language；未设置返回 fallback（空则由调用方决定是否下发）。"""
    if not user:
        return fallback
    loc = (getattr(user, "language", None) or "").strip()
    if loc:
        return _normalize_locale(loc, fallback=fallback)
    return fallback


def _resolve_dispatch_language(user: Optional[UserORM], event_language: Optional[str] = None) -> str:
    """下发语种：已设置的用户语言优先；未设置回退埋点 locale，再回退 en。"""
    user_lang = _user_language(user, fallback="")
    if user_lang:
        return user_lang
    event_lang = _normalize_locale(event_language or "", fallback="")
    return event_lang or "en"


def _library_row_locale(row: MsgStrategyLibraryORM) -> str:
    return _normalize_locale(getattr(row, "language", None) or "zh", fallback="zh")


def filter_library_rows_by_locale(
    rows: List[MsgStrategyLibraryORM],
    user_locale: str,
) -> List[MsgStrategyLibraryORM]:
    """策略库语种：只保留与用户语言一致的文案；无匹配则不下发。"""
    want = _normalize_locale(user_locale, fallback="")
    if not want:
        return []
    return [r for r in rows if _library_row_locale(r) == want]


def _library_winners(
    libs: List[MsgStrategyLibraryORM],
    *,
    canonical_event: str,
    trigger_key: str,
) -> List[MsgStrategyLibraryORM]:
    """通用库：同 (language, type_key) 只留 sort_order 最高的一条。

    精准库按智能体拆行，不能合成一条，否则 A 的文案会盖住 B/C/D。
    执行时精准优先于通用，命中后不再走通用。
    """
    ordered = sorted(
        libs,
        key=lambda x: (
            0 if not _is_global_library(x) else 1,
            0 if canonicalize_product_event(x.type_key or "") == canonical_event else 1,
            0 if (x.type_key or "") == trigger_key else 1,
            -int(x.sort_order or 0),
            int(x.id or 0),
        ),
    )
    winners: List[MsgStrategyLibraryORM] = []
    seen_global: set = set()
    for lib in ordered:
        if _is_global_library(lib):
            group_key = (
                _library_row_locale(lib),
                (lib.type_key or "").strip(),
            )
            if group_key in seen_global:
                continue
            seen_global.add(group_key)
        winners.append(lib)
    return winners


def _count_sent_today(db: Session, user_id: int) -> int:
    start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    n = (
        db.query(func.count(MsgStrategyDispatchLogORM.id))
        .filter(
            MsgStrategyDispatchLogORM.user_id == user_id,
            MsgStrategyDispatchLogORM.status.in_(("queued", "sent")),
            MsgStrategyDispatchLogORM.created_at >= start.replace(tzinfo=None),
        )
        .scalar()
        or 0
    )
    return int(n)


def _count_sent_today_for_event(
    db: Session,
    user_id: int,
    event_keys: List[str],
    *,
    rule_id: Optional[int] = None,
) -> int:
    """用户当天已对该事件成功触发的次数（queued/sent 日志条数）。

    同时匹配 event_key / trigger_key，兼容 raw / canonical / 别名。
    """
    keys = [(k or "").strip() for k in (event_keys or []) if (k or "").strip()]
    if not keys:
        return 0
    start = _day_start_naive_utc()
    q = db.query(func.count(MsgStrategyDispatchLogORM.id)).filter(
        MsgStrategyDispatchLogORM.user_id == user_id,
        MsgStrategyDispatchLogORM.status.in_(("queued", "sent")),
        MsgStrategyDispatchLogORM.created_at >= start,
        or_(
            MsgStrategyDispatchLogORM.event_key.in_(keys),
            MsgStrategyDispatchLogORM.trigger_key.in_(keys),
        ),
    )
    if rule_id is not None:
        q = q.filter(MsgStrategyDispatchLogORM.rule_id == int(rule_id))
    return int(q.scalar() or 0)


def _normalize_quota_event_keys(event_keys: List[str]) -> List[str]:
    keys: List[str] = []
    for raw in event_keys or []:
        k = (raw or "").strip()
        if k and k not in keys:
            keys.append(k)
        canon = canonicalize_product_event(k) if k else ""
        if canon and canon not in keys:
            keys.append(canon)
    return keys


def _pair_sent_from_table(
    db: Session, user_id: int, companion_id: str, event_keys: List[str]
) -> int:
    cid = (companion_id or "").strip()[:8]
    keys = _normalize_quota_event_keys(event_keys)
    if not cid or not keys:
        return 0
    n = (
        db.query(func.coalesce(func.max(MsgStrategyUserCompanionEventORM.sent_count), 0))
        .filter(
            MsgStrategyUserCompanionEventORM.user_id == user_id,
            MsgStrategyUserCompanionEventORM.companion_id == cid,
            MsgStrategyUserCompanionEventORM.event_key.in_(keys),
        )
        .scalar()
        or 0
    )
    return int(n)


def _pair_sent_from_logs(
    db: Session, user_id: int, companion_id: str, event_keys: List[str]
) -> int:
    cid = (companion_id or "").strip()[:8]
    keys = _normalize_quota_event_keys(event_keys)
    if not cid or not keys:
        return 0
    n = (
        db.query(func.count(MsgStrategyDispatchLogORM.id))
        .filter(
            MsgStrategyDispatchLogORM.user_id == user_id,
            MsgStrategyDispatchLogORM.companion_id == cid,
            MsgStrategyDispatchLogORM.status.in_(("queued", "sent")),
            or_(
                MsgStrategyDispatchLogORM.event_key.in_(keys),
                MsgStrategyDispatchLogORM.trigger_key.in_(keys),
            ),
        )
        .scalar()
        or 0
    )
    return int(n)


def count_user_companion_event_sent(
    db: Session, user_id: int, companion_id: str, event_keys: List[str]
) -> int:
    """用户 × 智能体 × 事件已成功条数：计数表与历史日志取较大值。"""
    return max(
        _pair_sent_from_table(db, user_id, companion_id, event_keys),
        _pair_sent_from_logs(db, user_id, companion_id, event_keys),
    )


def companions_at_event_quota(
    db: Session,
    user_id: int,
    event_keys: List[str],
    quota: int,
) -> set:
    """该用户在该事件上已达上限的智能体。"""
    keys = _normalize_quota_event_keys(event_keys)
    cap = max(int(quota or 0), 0)
    if not keys or cap <= 0:
        return set()
    blocked: set = set()
    table_rows = (
        db.query(
            MsgStrategyUserCompanionEventORM.companion_id,
            func.max(MsgStrategyUserCompanionEventORM.sent_count),
        )
        .filter(
            MsgStrategyUserCompanionEventORM.user_id == user_id,
            MsgStrategyUserCompanionEventORM.event_key.in_(keys),
        )
        .group_by(MsgStrategyUserCompanionEventORM.companion_id)
        .all()
    )
    for cid, n in table_rows:
        if (cid or "").strip() and int(n or 0) >= cap:
            blocked.add((cid or "").strip()[:8])
    log_rows = (
        db.query(
            MsgStrategyDispatchLogORM.companion_id,
            func.count(MsgStrategyDispatchLogORM.id),
        )
        .filter(
            MsgStrategyDispatchLogORM.user_id == user_id,
            MsgStrategyDispatchLogORM.status.in_(("queued", "sent")),
            MsgStrategyDispatchLogORM.companion_id != "",
            or_(
                MsgStrategyDispatchLogORM.event_key.in_(keys),
                MsgStrategyDispatchLogORM.trigger_key.in_(keys),
            ),
        )
        .group_by(MsgStrategyDispatchLogORM.companion_id)
        .all()
    )
    for cid, n in log_rows:
        if (cid or "").strip() and int(n or 0) >= cap:
            blocked.add((cid or "").strip()[:8])
    return blocked


def bump_user_companion_event_sent(
    db: Session,
    *,
    user_id: int,
    companion_id: str,
    event_key: str,
    event_keys: Optional[List[str]] = None,
) -> int:
    """入队成功后 +1。返回最新 sent_count。"""
    cid = (companion_id or "").strip()[:8]
    canon = canonicalize_product_event(event_key) or (event_key or "").strip()
    if not user_id or not cid or not canon:
        return 0
    keys = _normalize_quota_event_keys(list(event_keys or []) + [canon, event_key])
    current = count_user_companion_event_sent(db, user_id, cid, keys)
    row = (
        db.query(MsgStrategyUserCompanionEventORM)
        .filter(
            MsgStrategyUserCompanionEventORM.user_id == user_id,
            MsgStrategyUserCompanionEventORM.companion_id == cid,
            MsgStrategyUserCompanionEventORM.event_key == canon[:128],
        )
        .first()
    )
    now = _utcnow().replace(tzinfo=None)
    next_n = current + 1
    if row:
        row.sent_count = next_n
        row.updated_at = now
    else:
        db.add(
            MsgStrategyUserCompanionEventORM(
                user_id=user_id,
                companion_id=cid,
                event_key=canon[:128],
                sent_count=next_n,
                created_at=now,
                updated_at=now,
            )
        )
    return next_n


def _fatigue_blocked(db: Session, user_id: int, g: MsgStrategyGlobalORM) -> bool:
    """周期内下发条数上限：fatigue_window_hours 内 queued/sent >= fatigue_max_unreplied 则拦截。"""
    max_n = int(g.fatigue_max_unreplied or 0)
    hours = int(g.fatigue_window_hours or 0)
    if max_n <= 0 or hours <= 0:
        return False
    since = _utcnow() - timedelta(hours=hours)
    n = (
        db.query(func.count(MsgStrategyDispatchLogORM.id))
        .filter(
            MsgStrategyDispatchLogORM.user_id == user_id,
            MsgStrategyDispatchLogORM.status.in_(("queued", "sent")),
            MsgStrategyDispatchLogORM.created_at >= since.replace(tzinfo=None),
        )
        .scalar()
        or 0
    )
    return int(n) >= max_n


def _resolve_global_gate(
    db: Session,
    user: UserORM,
    user_id: int,
    *,
    bound_event: bool = False,
) -> Dict[str, Any]:
    """全局配置：未命中则不限制。命中后 0 值字段也不限制。

    指定智能体事件（用户×智能体×事件）不吃用户级日限/疲劳，避免 A 触发后 B/C/D 被挡。
    仍可使用 interval 拉开连发间隔。
    """
    g = pick_best_global(db, user)
    empty = {
        "row": None,
        "blocked": None,
        "daily_left": None,
        "period_left": None,
        "interval_min": 0,
        "interval_max": 0,
        "sent_today": 0,
        "sent_period": 0,
    }
    if not g:
        return empty
    if bound_event:
        imin = int(g.interval_min_sec or 0)
        imax = max(imin, int(g.interval_max_sec or 0))
        return {
            **empty,
            "row": g,
            "interval_min": imin,
            "interval_max": imax,
        }

    daily_limit = int(g.daily_limit or 0)
    sent_today = _count_sent_today(db, user_id) if daily_limit > 0 else 0
    daily_left = None
    if daily_limit > 0:
        daily_left = max(0, daily_limit - sent_today)
        if daily_left <= 0:
            return {
                **empty,
                "row": g,
                "blocked": "daily_limit",
                "sent_today": sent_today,
                "daily_left": 0,
            }

    if _fatigue_blocked(db, user_id, g):
        return {**empty, "row": g, "blocked": "period_limit", "sent_today": sent_today}

    period_left = None
    sent_period = 0
    max_n = int(g.fatigue_max_unreplied or 0)
    hours = int(g.fatigue_window_hours or 0)
    if max_n > 0 and hours > 0:
        since = _utcnow() - timedelta(hours=hours)
        sent_period = int(
            db.query(func.count(MsgStrategyDispatchLogORM.id))
            .filter(
                MsgStrategyDispatchLogORM.user_id == user_id,
                MsgStrategyDispatchLogORM.status.in_(("queued", "sent")),
                MsgStrategyDispatchLogORM.created_at >= since.replace(tzinfo=None),
            )
            .scalar()
            or 0
        )
        period_left = max(0, max_n - sent_period)
        if period_left <= 0:
            return {
                **empty,
                "row": g,
                "blocked": "period_limit",
                "sent_today": sent_today,
                "sent_period": sent_period,
                "period_left": 0,
            }

    imin = int(g.interval_min_sec or 0)
    imax = max(imin, int(g.interval_max_sec or 0))
    return {
        "row": g,
        "blocked": None,
        "daily_left": daily_left,
        "period_left": period_left,
        "interval_min": imin,
        "interval_max": imax,
        "sent_today": sent_today,
        "sent_period": sent_period,
    }


def _stagger_delay_sec(
    db: Session,
    user_id: int,
    imin: int,
    imax: int,
    delay_acc: int,
) -> int:
    """不同事件同时触发时拉开间隔；未配置间隔则不额外等待。"""
    if imax <= 0:
        return delay_acc
    gap = random.randint(max(0, imin), imax)
    last = (
        db.query(OutreachJobORM.execute_at)
        .filter(
            OutreachJobORM.user_id == user_id,
            OutreachJobORM.job_type == JOB_MSG_STRATEGY,
            OutreachJobORM.status.in_(("pending", "processing")),
        )
        .order_by(OutreachJobORM.execute_at.desc())
        .first()
    )
    last_at = last[0] if last else None
    if last_at and getattr(last_at, "tzinfo", None):
        last_at = last_at.replace(tzinfo=None)
    now = _utcnow().replace(tzinfo=None)
    min_from_last = 0
    if last_at:
        min_from_last = max(0, int((last_at + timedelta(seconds=gap) - now).total_seconds()))
    if delay_acc <= 0:
        return min_from_last
    return max(delay_acc + gap, min_from_last)


def _append_library_copy(
    results: List[Dict[str, Any]],
    *,
    lib: MsgStrategyLibraryORM,
    companion_id: str,
    companion: CompanionORM,
    language: str,
    used_companions: set,
    source: str = "library",
) -> None:
    cid = (companion_id or "").strip()
    used_companions.add(cid)
    image_url = (getattr(lib, "image_url", None) or "").strip()
    if _is_global_library(lib):
        image_url = ""
    results.append(
        {
            "companion_id": cid,
            "companion_name": companion.name or "",
            "content": lib.content or "",
            "image_url": image_url,
            "language": lib.language or language,
            "library_id": lib.id,
            "companion_strategy_id": None,
            "source": source,
        }
    )


def _library_companion_ids(lib: MsgStrategyLibraryORM) -> List[str]:
    return [str(x).strip() for x in (lib.companion_ids or []) if str(x).strip()]


def _is_global_library(lib: MsgStrategyLibraryORM) -> bool:
    return not _library_companion_ids(lib)


def _list_global_eligible_companion_ids(
    db: Session,
    *,
    require_lang: str,
    used_companions: set,
    companion_owned: set,
    limit: int = 200,
) -> List[str]:
    """全局策略：从全库可用智能体中筛语种匹配、未占用的候选。"""
    q = db.query(CompanionORM).filter(
        CompanionORM.is_deleted.isnot(True),
        CompanionORM.is_active.is_(True),
    )
    rows = q.order_by(CompanionORM.id.asc()).limit(max(limit * 3, 300)).all()
    out: List[str] = []
    for comp in rows:
        cid = (comp.id or "").strip()
        if not cid or cid in used_companions or cid in companion_owned:
            continue
        if require_lang and not _companion_matches_user_lang(comp, require_lang):
            continue
        out.append(cid)
        if len(out) >= limit:
            break
    return out


def _pick_copy(
    db: Session,
    *,
    trigger_key: str,
    language: str,
    count: int,
    used_companions: Optional[set] = None,
    event_key: str = "",
    event_companion_id: str = "",
    user_locale: Optional[str] = None,
    quota_blocked: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """取文案（最多 count 条）：

    1) 智能体独立策略（companion_strategies）优先于通用/精准库：
       - language = 触发用户语言，只按用户语种匹配文案（不要求智能体资料语言相同）；
       - 通用事件：先独立，命中的智能体不再走库随机；
       - 指定智能体事件：只看该 companion 的独立文案，没有再走库文案（仍不随机 1～3）；
    2) 策略库（同一张表）：
       - type_key 匹配 + language 与用户语言对齐（无匹配不发送）；
       - 发送智能体必须 companions.language == 用户语种，否则跳过；
       - 指定智能体事件：只对埋点 companion_id 下发，话术用库 content，禁止随机 1～3；
       - 通用事件 **精准**（companion_ids 非空）优先：从指定智能体中随机 1～3；
       - 通用事件 **全局**（companion_ids 空）：无精准命中时，从全部可用智能体中随机 1～3。
    """
    library_locale = _normalize_locale(user_locale if user_locale is not None else language, fallback="")
    # 智能体语种硬筛：优先用户语种；空则跳过所有发送（避免串语种）
    companion_lang = library_locale or _normalize_locale(language, fallback="")
    type_keys = trigger_to_type_keys(trigger_key)
    event_keys = event_keys_for_trigger(trigger_key)
    canonical_event = canonicalize_product_event(event_key or trigger_key)
    ordered_type_keys: List[str] = []
    for k in [canonical_event, trigger_key, *event_keys, *type_keys]:
        kk = (k or "").strip()
        if kk and kk not in ordered_type_keys:
            ordered_type_keys.append(kk)
    library_type_keys = expand_library_type_keys(ordered_type_keys)
    results: List[Dict[str, Any]] = []
    if used_companions is None:
        used_companions = set()
    blocked = {(c or "").strip()[:8] for c in (quota_blocked or set()) if (c or "").strip()}

    bound_event = is_companion_bound_event(event_key or trigger_key or canonical_event)
    priority_cid = (event_companion_id or "").strip() if bound_event else ""
    if bound_event and not priority_cid:
        pipeline_log(
            "[msg_strategy] pick_copy skip missing_companion_id event=%s",
            event_key or canonical_event,
        )
        return results
    if priority_cid and priority_cid in blocked:
        pipeline_log(
            "[msg_strategy] pick_copy skip companion_event_limit event=%s companion=%s",
            event_key,
            priority_cid,
        )
        return results

    def _event_matches(row: CompanionStrategyORM) -> bool:
        ev = (row.trigger_event or row.strategy_type or "").strip()
        st = (row.strategy_type or "").strip()
        return ev in ordered_type_keys or st in ordered_type_keys

    want_copy_lang = _normalize_locale(language, fallback="")

    def _copy_lang_ok(row: CompanionStrategyORM) -> bool:
        if not want_copy_lang:
            return False
        return _normalize_locale(row.language or "", fallback="") == want_copy_lang

    active_independent = (
        db.query(CompanionStrategyORM)
        .filter(CompanionStrategyORM.is_active.is_(True))
        .order_by(CompanionStrategyORM.sort_order.desc(), CompanionStrategyORM.id.asc())
        .all()
    )
    matched_independent = [row for row in active_independent if _event_matches(row) and _copy_lang_ok(row)]

    # 本事件已配置独立策略的智能体 → 通用库随机时排除（指定智能体事件仍可用库文案兜底）
    companion_owned: set = set()
    for row in matched_independent:
        cid = (row.companion_id or "").strip()
        if cid and cid != priority_cid:
            companion_owned.add(cid)

    def _append_independent(row: CompanionStrategyORM) -> bool:
        if len(results) >= count:
            return False
        cid = (row.companion_id or "").strip()
        if not cid or cid in used_companions or cid in blocked:
            return False
        if bound_event and priority_cid and cid != priority_cid:
            return False
        comp = (
            db.query(CompanionORM)
            .filter(CompanionORM.id == cid, CompanionORM.is_deleted.isnot(True))
            .first()
        )
        if not comp or not getattr(comp, "is_active", True):
            return False
        used_companions.add(cid)
        results.append(
            {
                "companion_id": cid,
                "companion_name": comp.name or "",
                "content": row.content or "",
                "image_url": (getattr(row, "image_url", None) or "").strip(),
                "language": row.language or language,
                "library_id": None,
                "companion_strategy_id": row.id,
                "source": "companion",
            }
        )
        pipeline_log(
            "[msg_strategy] pick_copy independent event=%s companion=%s strategy_id=%s lang=%s",
            event_key or canonical_event,
            cid,
            row.id,
            row.language or language,
        )
        return True

    # 1) 独立策略优先于通用库
    for row in matched_independent:
        if len(results) >= count:
            break
        _append_independent(row)

    # 2) 全局策略库：type_key + 用户语言精确匹配 language（无匹配不发送）
    if len(results) < count and library_type_keys:
        all_libs = list(
            db.query(MsgStrategyLibraryORM)
            .filter(
                MsgStrategyLibraryORM.is_active.is_(True),
                MsgStrategyLibraryORM.type_key.in_(library_type_keys),
            )
            .order_by(MsgStrategyLibraryORM.sort_order.desc(), MsgStrategyLibraryORM.id.asc())
            .all()
        )
        libs = filter_library_rows_by_locale(all_libs, library_locale)
        winners = _library_winners(
            libs,
            canonical_event=canonical_event,
            trigger_key=trigger_key,
        )

        event_sender_applied = False
        if priority_cid:
            event_cid = priority_cid
            if event_cid not in used_companions:
                event_comp = _active_companion(
                    db, event_cid, require_lang=companion_lang
                )
                if event_comp:
                    lib_pool = [lib for lib in winners if (lib.content or "").strip()]
                    if not lib_pool and canonical_event:
                        fallback_libs = filter_library_rows_by_locale(
                            list(
                                db.query(MsgStrategyLibraryORM)
                                .filter(
                                    MsgStrategyLibraryORM.is_active.is_(True),
                                    MsgStrategyLibraryORM.type_key.in_(
                                        expand_library_type_keys([canonical_event])
                                    ),
                                )
                                .order_by(
                                    MsgStrategyLibraryORM.sort_order.desc(),
                                    MsgStrategyLibraryORM.id.asc(),
                                )
                                .all()
                            ),
                            library_locale,
                        )
                        lib_pool = _library_winners(
                            fallback_libs,
                            canonical_event=canonical_event,
                            trigger_key=trigger_key,
                        )
                        lib_pool = [lib for lib in lib_pool if (lib.content or "").strip()]
                    def _lib_fits_event_companion(lib: MsgStrategyLibraryORM) -> bool:
                        if _is_global_library(lib):
                            return True
                        return event_cid in _library_companion_ids(lib)

                    lib_pool = [lib for lib in lib_pool if _lib_fits_event_companion(lib)]
                    lib_pool.sort(
                        key=lambda lib: (
                            1 if _is_global_library(lib) else 0,
                            -int(lib.sort_order or 0),
                            int(lib.id or 0),
                        )
                    )
                    for lib in lib_pool:
                        _append_library_copy(
                            results,
                            lib=lib,
                            companion_id=event_cid,
                            companion=event_comp,
                            language=language,
                            used_companions=used_companions,
                            source=(
                                "library_global"
                                if _is_global_library(lib)
                                else "library_precise"
                            ),
                        )
                        event_sender_applied = True
                        pipeline_log(
                            "[msg_strategy] pick_copy event_companion event=%s companion=%s library_id=%s content_len=%s",
                            event_key,
                            event_cid,
                            lib.id,
                            len(lib.content or ""),
                        )
                        break
                else:
                    pipeline_log(
                        "[msg_strategy] pick_copy event_companion invalid_or_lang_mismatch "
                        "event=%s companion=%s user_lang=%s",
                        event_key,
                        event_cid,
                        companion_lang or "",
                    )
            elif event_cid:
                pipeline_log(
                    "[msg_strategy] pick_copy event_companion skipped event=%s companion=%s used=%s",
                    event_key,
                    event_cid,
                    event_cid in used_companions,
                )

        # 指定智能体事件禁止精准/全局随机 1～3
        if not bound_event and not event_sender_applied:
            precise_libs = [lib for lib in winners if not _is_global_library(lib)]
            global_libs = [lib for lib in winners if _is_global_library(lib)]

            def _apply_from_eligible(
                lib: MsgStrategyLibraryORM,
                eligible: List[str],
                *,
                source: str,
            ) -> None:
                if not eligible or len(results) >= count:
                    return
                pick_n = library_companion_pick_count(
                    eligible_count=len(eligible), remaining=count - len(results)
                )
                for cid in random.sample(eligible, pick_n):
                    comp = _active_companion(db, cid, require_lang=companion_lang)
                    if not comp:
                        continue
                    _append_library_copy(
                        results,
                        lib=lib,
                        companion_id=cid,
                        companion=comp,
                        language=language,
                        used_companions=used_companions,
                        source=source,
                    )
                    if len(results) >= count:
                        break

            # 2a) 精准：指定 companion_ids（命中后不再走通用库）
            precise_applied = False
            for lib in precise_libs:
                if len(results) >= count:
                    break
                eligible = []
                for cid in _library_companion_ids(lib):
                    if cid in used_companions or cid in blocked or cid in companion_owned:
                        continue
                    if _active_companion(db, cid, require_lang=companion_lang):
                        eligible.append(cid)
                before = len(results)
                _apply_from_eligible(lib, eligible, source="library_precise")
                if len(results) > before:
                    precise_applied = True

            # 2b) 全局：companion_ids 为空 → 作用全部可用智能体
            if not precise_applied and len(results) < count:
                for lib in global_libs:
                    if len(results) >= count:
                        break
                    if not (lib.content or "").strip():
                        continue
                    eligible = _list_global_eligible_companion_ids(
                        db,
                        require_lang=companion_lang,
                        used_companions=used_companions | blocked,
                        companion_owned=companion_owned,
                    )
                    _apply_from_eligible(lib, eligible, source="library_global")
                    if eligible:
                        pipeline_log(
                            "[msg_strategy] pick_copy global_library library_id=%s "
                            "eligible=%s picked_total=%s",
                            lib.id,
                            len(eligible),
                            len(results),
                        )
        if len(results) == 0 and count > 0:
            inactive_rows = (
                db.query(MsgStrategyLibraryORM)
                .filter(
                    MsgStrategyLibraryORM.is_active.is_(False),
                    MsgStrategyLibraryORM.type_key.in_(library_type_keys),
                )
                .all()
            )
            pipeline_log(
                "[msg_strategy] pick_copy empty trigger=%s event=%s type_keys=%s library_keys=%s "
                "active_libs=%s inactive_libs=%s priority_companion=%s companion_owned=%s lang=%s",
                trigger_key,
                event_key or canonical_event,
                ordered_type_keys,
                library_type_keys,
                len(all_libs),
                [(r.id, r.type_key) for r in inactive_rows],
                priority_cid or "-",
                len(companion_owned),
                library_locale or language or "*",
            )
            if inactive_rows:
                pipeline_log(
                    "[msg_strategy] pick_copy hint enable library rows in admin: %s",
                    [
                        {
                            "id": r.id,
                            "type_key": r.type_key,
                            "language": r.language,
                            "content_len": len(r.content or ""),
                        }
                        for r in inactive_rows
                    ],
                )
    elif count > 0:
        pipeline_log(
            "[msg_strategy] pick_copy empty trigger=%s reason=no_type_keys lang_filter=%s",
            trigger_key,
            language or "*",
        )
    return results


def _schedule_job(
    db: Session,
    *,
    user_id: int,
    companion_id: str,
    content: str,
    language: str,
    companion_name: str,
    delay_sec: int,
    meta: dict,
    image_url: str = "",
) -> int:
    execute_at = _utcnow() + timedelta(seconds=max(0, delay_sec))
    job = OutreachJobORM(
        job_type=JOB_MSG_STRATEGY,
        user_id=user_id,
        companion_id=companion_id,
        status="pending",
        execute_at=execute_at.replace(tzinfo=None),
        payload={
            "content": content,
            "image_url": (image_url or "").strip()[:500],
            "language": language,
            "companion_name": companion_name,
            "msg_strategy": meta,
        },
    )
    db.add(job)
    db.flush()
    pipeline_log(
        "[msg_strategy] job scheduled job_id=%s user=%s companion=%s delay_sec=%s execute_at=%s event=%s",
        job.id,
        user_id,
        companion_id,
        delay_sec,
        execute_at.isoformat(),
        (meta or {}).get("event_key") or (meta or {}).get("trigger_key") or "",
    )
    return int(job.id)


def _log(
    db: Session,
    *,
    user_id: int,
    event_key: str,
    trigger_key: str,
    status: str,
    skip_reason: str = "",
    global_id: Optional[int] = None,
    rule_id: Optional[int] = None,
    library_id: Optional[int] = None,
    companion_id: str = "",
    content: str = "",
    job_id: Optional[int] = None,
    execute_at: Optional[datetime] = None,
) -> None:
    db.add(
        MsgStrategyDispatchLogORM(
            user_id=user_id,
            event_key=event_key[:128],
            trigger_key=trigger_key[:64],
            global_id=global_id,
            rule_id=rule_id,
            library_id=library_id,
            companion_id=(companion_id or "")[:8],
            content=content or "",
            status=status,
            skip_reason=(skip_reason or "")[:200],
            job_id=job_id,
            execute_at=execute_at.replace(tzinfo=None) if execute_at and execute_at.tzinfo else execute_at,
        )
    )


def dispatch_product_event(
    user_id: int,
    event_key: str,
    *,
    language: Optional[str] = None,
    companion_id: Optional[str] = None,
    moment_id: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """主入口：前端埋点 product_event 触发。

    匹配 rules / library 时使用裸 event_key（与 product_event 字段一致）。
    """
    raw_event = (event_key or "").strip()
    event = canonicalize_product_event(raw_event)
    trigger_keys = event_to_trigger_keys(event)
    if not user_id or not event or not trigger_keys:
        pipeline_log(
            "[msg_strategy] dispatch skip user=%s raw_event=%r event=%r reason=no_mapping trigger_keys=%s",
            user_id,
            raw_event,
            event,
            trigger_keys,
        )
        return {
            "ok": False,
            "reason": "no_mapping",
            "event": event,
            "raw_event": raw_event,
            "queued": 0,
        }

    event_companion_id = (companion_id or "").strip()
    event_moment_id = moment_id

    pipeline_log(
        "[msg_strategy] dispatch start user=%s raw_event=%s event=%s trigger_keys=%s "
        "event_companion_id=%s moment_id=%s dry_run=%s",
        user_id,
        raw_event,
        event,
        trigger_keys,
        event_companion_id or "-",
        event_moment_id or "-",
        dry_run,
    )

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == int(user_id)).first()
        if not user:
            pipeline_log("[msg_strategy] dispatch skip user=%s event=%s reason=user_not_found", user_id, event)
            return {"ok": False, "reason": "user_not_found", "event": event, "raw_event": raw_event, "queued": 0}

        lang = _resolve_dispatch_language(user, language)
        if not lang:
            pipeline_log(
                "[msg_strategy] dispatch skip user=%s event=%s reason=no_user_language",
                user_id,
                event,
            )
            _log(
                db, user_id=user_id, event_key=raw_event or event, trigger_key=trigger_keys[0],
                status="discarded", skip_reason="no_user_language",
            )
            db.commit()
            return {
                "ok": False,
                "reason": "no_user_language",
                "event": event,
                "raw_event": raw_event,
                "queued": 0,
            }
        pipeline_log(
            "[msg_strategy] dispatch user=%s event=%s lang=%s tags=%s ua_tags=%s",
            user_id,
            event,
            lang,
            getattr(user, "tags", None),
            getattr(user, "ua_tags", None),
        )

        if is_companion_bound_event(event) and not event_companion_id and event_moment_id:
            resolved = _companion_id_from_moment(db, event_moment_id)
            if resolved:
                event_companion_id = resolved
                pipeline_log(
                    "[msg_strategy] dispatch companion from moment user=%s event=%s moment_id=%s companion=%s",
                    user_id,
                    event,
                    event_moment_id,
                    event_companion_id,
                )
        if is_companion_bound_event(event) and not event_companion_id:
            pipeline_log(
                "[msg_strategy] dispatch skip user=%s event=%s reason=missing_companion_id moment_id=%s",
                user_id,
                event,
                event_moment_id or "-",
            )
            _log(
                db, user_id=user_id, event_key=raw_event or event, trigger_key=trigger_keys[0],
                status="discarded", skip_reason="missing_companion_id",
            )
            db.commit()
            return {
                "ok": False,
                "reason": "missing_companion_id",
                "event": event,
                "raw_event": raw_event,
                "queued": 0,
            }

        # ① 埋点触发配置：先匹配规则，未命中则结束（全局尚未介入）
        chosen_trigger = trigger_keys[0]
        matched_rules: List[Any] = []
        for tk in trigger_keys:
            rules = pick_matching_rules(db, user, tk)
            if rules:
                chosen_trigger = tk
                matched_rules = rules
                break

        library_fallback = False
        if not matched_rules:
            if event in LIBRARY_FALLBACK_EVENTS:
                library_fallback = True
                pipeline_log(
                    "[msg_strategy] dispatch no_rule fallback library user=%s event=%s trigger=%s",
                    user_id,
                    event,
                    chosen_trigger,
                )
            else:
                pipeline_log(
                    "[msg_strategy] dispatch skip user=%s event=%s reason=no_rule trigger=%s trigger_keys=%s",
                    user_id,
                    event,
                    chosen_trigger,
                    trigger_keys,
                )
                _log(
                    db, user_id=user_id, event_key=raw_event or event, trigger_key=chosen_trigger,
                    status="discarded", skip_reason="no_rule",
                )
                db.commit()
                return {"ok": False, "reason": "no_rule", "event": event, "raw_event": raw_event, "queued": 0}

        if not library_fallback:
            pipeline_log(
                "[msg_strategy] rules matched user=%s event=%s trigger=%s rule_ids=%s scopes=%s",
                user_id,
                event,
                chosen_trigger,
                [int(r.id) for r in matched_rules],
                [r.user_scope_type or "all" for r in matched_rules],
            )

        used_companions: set = set()
        all_copies: List[Dict[str, Any]] = []
        matched_rule_ids: List[int] = []
        quota_event_keys = event_keys_for_daily_quota(
            event=event, raw_event=raw_event, trigger_keys=trigger_keys
        )
        # 串行化：同一用户同一事件并发埋点不会都读到 sent=0
        _acquire_user_event_lock(db, int(user_id), event)
        try:
            hit_companion_event_limit = False
            # 每次埋点最多发 1 条；triggers[N] = 用户×智能体×事件终身上限
            per_dispatch_left = 1

            if library_fallback:
                quota_blocked = companions_at_event_quota(
                    db, user_id, quota_event_keys, 1
                )
                if (
                    event_companion_id
                    and companion_id_priority_applies(event, event_companion_id)
                    and event_companion_id in quota_blocked
                ):
                    hit_companion_event_limit = True
                    pipeline_log(
                        "[msg_strategy] library fallback skip user=%s event=%s "
                        "reason=companion_event_limit companion=%s",
                        user_id,
                        event,
                        event_companion_id,
                    )
                    _log(
                        db,
                        user_id=user_id,
                        event_key=raw_event or event,
                        trigger_key=chosen_trigger,
                        status="discarded",
                        skip_reason="companion_event_limit",
                        companion_id=event_companion_id,
                    )
                else:
                    copies = _pick_copy(
                        db,
                        trigger_key=chosen_trigger,
                        language=lang,
                        count=1,
                        used_companions=used_companions,
                        event_key=event,
                        event_companion_id=event_companion_id,
                        user_locale=lang,
                        quota_blocked=quota_blocked,
                    )
                    if not copies:
                        copies = _pick_copy(
                            db,
                            trigger_key=chosen_trigger,
                            language="",
                            count=1,
                            used_companions=used_companions,
                            event_key=event,
                            event_companion_id=event_companion_id,
                            user_locale=lang,
                            quota_blocked=quota_blocked,
                        )
                    copies = [
                        c
                        for c in copies
                        if (c.get("companion_id") or "").strip() not in quota_blocked
                    ]
                    pipeline_log(
                        "[msg_strategy] library fallback pick user=%s event=%s "
                        "picked=%s companions=%s",
                        user_id,
                        event,
                        len(copies),
                        [c.get("companion_id") for c in copies],
                    )
                    all_copies.extend(copies)

            for rule in matched_rules:
                if per_dispatch_left <= 0:
                    pipeline_log(
                        "[msg_strategy] rule stop user=%s event=%s rule_id=%s reason=per_dispatch_cap",
                        user_id,
                        event,
                        rule.id,
                    )
                    break
                triggers = normalize_triggers(rule.triggers, db=db)
                configured = rule_want_count(triggers, chosen_trigger, event, trigger_keys)
                if configured <= 0:
                    pipeline_log(
                        "[msg_strategy] rule skip user=%s event=%s rule_id=%s reason=trigger_count_zero trigger=%s",
                        user_id,
                        event,
                        rule.id,
                        chosen_trigger,
                    )
                    continue
                quota_blocked = companions_at_event_quota(
                    db, user_id, quota_event_keys, configured
                )
                if (
                    event_companion_id
                    and companion_id_priority_applies(event, event_companion_id)
                    and event_companion_id in quota_blocked
                ):
                    hit_companion_event_limit = True
                    pipeline_log(
                        "[msg_strategy] rule skip user=%s event=%s rule_id=%s "
                        "reason=companion_event_limit companion=%s quota=%s",
                        user_id,
                        event,
                        rule.id,
                        event_companion_id,
                        configured,
                    )
                    _log(
                        db,
                        user_id=user_id,
                        event_key=raw_event or event,
                        trigger_key=chosen_trigger,
                        status="discarded",
                        skip_reason="companion_event_limit",
                        rule_id=int(rule.id),
                        companion_id=event_companion_id,
                    )
                    continue
                want = min(1, per_dispatch_left)
                copies = _pick_copy(
                    db,
                    trigger_key=chosen_trigger,
                    language=lang,
                    count=want,
                    used_companions=used_companions,
                    event_key=event,
                    event_companion_id=event_companion_id,
                    user_locale=lang,
                    quota_blocked=quota_blocked,
                )
                if not copies:
                    copies = _pick_copy(
                        db,
                        trigger_key=chosen_trigger,
                        language="",
                        count=want,
                        used_companions=used_companions,
                        event_key=event,
                        event_companion_id=event_companion_id,
                        user_locale=lang,
                        quota_blocked=quota_blocked,
                    )
                copies = [
                    c
                    for c in copies
                    if (c.get("companion_id") or "").strip() not in quota_blocked
                ]
                pipeline_log(
                    "[msg_strategy] rule pick user=%s event=%s rule_id=%s trigger=%s "
                    "configured=%s blocked=%s want=%s picked=%s companions=%s",
                    user_id,
                    event,
                    rule.id,
                    chosen_trigger,
                    configured,
                    len(quota_blocked),
                    want,
                    len(copies),
                    [c.get("companion_id") for c in copies],
                )
                if not copies:
                    if quota_blocked:
                        hit_companion_event_limit = True
                    continue
                matched_rule_ids.append(int(rule.id))
                for c in copies:
                    c["rule_id"] = int(rule.id)
                all_copies.extend(copies)
                per_dispatch_left -= len(copies)

            if not all_copies:
                skip_reason = (
                    "companion_event_limit" if hit_companion_event_limit else "no_copy"
                )
                pipeline_log(
                    "[msg_strategy] dispatch skip user=%s event=%s reason=%s trigger=%s "
                    "matched_rule_ids=%s",
                    user_id,
                    event,
                    skip_reason,
                    chosen_trigger,
                    [int(r.id) for r in matched_rules],
                )
                if skip_reason != "companion_event_limit":
                    _log(
                        db, user_id=user_id, event_key=raw_event or event, trigger_key=chosen_trigger,
                        status="discarded", skip_reason=skip_reason,
                        rule_id=matched_rule_ids[0] if matched_rule_ids else None,
                    )
                db.commit()
                return {
                    "ok": False,
                    "reason": skip_reason,
                    "event": event,
                    "raw_event": raw_event,
                    "queued": 0,
                }

            # ② 全局配置：规则已命中后才检测；未配置/未命中用户范围则不限制
            gate = _resolve_global_gate(
                db,
                user,
                int(user_id),
                bound_event=is_companion_bound_event(event),
            )
            g = gate.get("row")
            gid = int(g.id) if g else None
            if gate.get("blocked"):
                reason = str(gate["blocked"])
                pipeline_log(
                    "[msg_strategy] dispatch skip user=%s event=%s reason=%s global_id=%s "
                    "sent_today=%s daily_left=%s sent_period=%s period_left=%s",
                    user_id,
                    event,
                    reason,
                    gid,
                    gate.get("sent_today"),
                    gate.get("daily_left"),
                    gate.get("sent_period"),
                    gate.get("period_left"),
                )
                _log(
                    db,
                    user_id=user_id,
                    event_key=raw_event or event,
                    trigger_key=chosen_trigger,
                    status="discarded",
                    skip_reason=reason,
                    global_id=gid,
                    rule_id=matched_rule_ids[0] if matched_rule_ids else None,
                )
                db.commit()
                return {
                    "ok": False,
                    "reason": reason,
                    "event": event,
                    "raw_event": raw_event,
                    "queued": 0,
                    "global_id": gid,
                }
            if g:
                pipeline_log(
                    "[msg_strategy] global hit user=%s event=%s global_id=%s name=%r scope=%s "
                    "daily_limit=%s period=%sh max=%s interval=%s-%ss",
                    user_id,
                    event,
                    gid,
                    g.name or "",
                    g.user_scope_type or "all",
                    int(g.daily_limit or 0),
                    int(g.fatigue_window_hours or 0),
                    int(g.fatigue_max_unreplied or 0),
                    gate["interval_min"],
                    gate["interval_max"],
                )
            else:
                pipeline_log(
                    "[msg_strategy] global skip-restrict user=%s event=%s reason=no_matching_global",
                    user_id,
                    event,
                )

            imin = int(gate["interval_min"] or 0)
            imax = int(gate["interval_max"] or 0)
            queued = []
            delay_acc = 0
            for item in all_copies:
                delay_acc = _stagger_delay_sec(db, int(user_id), imin, imax, delay_acc)
                rule_id = int(item.get("rule_id") or (matched_rule_ids[0] if matched_rule_ids else 0))
                meta = {
                    "event_key": event,
                    "raw_event": raw_event,
                    "trigger_key": chosen_trigger,
                    "global_id": gid,
                    "rule_id": rule_id,
                    "rule_ids": matched_rule_ids,
                    "library_id": item.get("library_id"),
                    "source": item.get("source"),
                }
                if dry_run:
                    queued.append({**item, "delay_sec": delay_acc, "meta": meta})
                    continue
                job_id = _schedule_job(
                    db,
                    user_id=user_id,
                    companion_id=item["companion_id"],
                    content=item["content"],
                    language=item.get("language") or lang,
                    companion_name=item.get("companion_name") or "",
                    delay_sec=delay_acc,
                    meta=meta,
                    image_url=item.get("image_url") or "",
                )
                execute_at = _utcnow() + timedelta(seconds=delay_acc)
                _log(
                    db,
                    user_id=user_id,
                    event_key=raw_event or event,
                    trigger_key=chosen_trigger,
                    status="queued",
                    global_id=gid,
                    rule_id=rule_id,
                    library_id=item.get("library_id"),
                    companion_id=item["companion_id"],
                    content=item["content"],
                    job_id=job_id,
                    execute_at=execute_at,
                )
                bump_user_companion_event_sent(
                    db,
                    user_id=user_id,
                    companion_id=item["companion_id"],
                    event_key=event,
                    event_keys=quota_event_keys,
                )
                queued.append({"job_id": job_id, "companion_id": item["companion_id"], "delay_sec": delay_acc, "rule_id": rule_id})

            db.commit()
            pipeline_log(
                "[msg_strategy] dispatch done user=%s raw_event=%s event=%s trigger=%s rules=%s "
                "queued=%s job_ids=%s dry_run=%s global_id=%s",
                user_id,
                raw_event,
                event,
                chosen_trigger,
                matched_rule_ids,
                len(queued),
                [q.get("job_id") for q in queued if q.get("job_id")],
                dry_run,
                gid,
            )
            return {
                "ok": True,
                "event": event,
                "raw_event": raw_event,
                "trigger_key": chosen_trigger,
                "global_id": gid,
                "rule_id": matched_rule_ids[0] if matched_rule_ids else None,
                "rule_ids": matched_rule_ids,
                "queued": len(queued),
                "items": queued,
                "dry_run": dry_run,
            }
        finally:
            _release_user_event_redis_locks(db)


def try_dispatch_from_attribution(
    user_id: Optional[int],
    product_event: str,
    *,
    locale: str = "",
    app_env: str = "",
    device_id: str = "",
    companion_id: str = "",
    moment_id: Optional[int] = None,
) -> None:
    """归因 product 埋点后静默触发（失败不抛）。"""
    if not user_id or not product_event:
        pipeline_log(
            "[msg_strategy] attribution hook skip user_id=%s product_event=%r device_id=%s reason=%s",
            user_id,
            product_event,
            device_id,
            "no_user_id" if not user_id else "no_product_event",
        )
        return
    event_companion_id = (companion_id or "").strip()
    pipeline_log(
        "[msg_strategy] attribution hook start user_id=%s product_event=%s locale=%s app_env=%s "
        "device_id=%s companion_id=%s moment_id=%s",
        user_id,
        product_event,
        locale or "-",
        app_env or "-",
        device_id or "-",
        event_companion_id or "-",
        moment_id or "-",
    )
    try:
        result = dispatch_product_event(
            int(user_id),
            product_event,
            language=locale or None,
            companion_id=event_companion_id or None,
            moment_id=moment_id,
        )
        pipeline_log(
            "[msg_strategy] attribution hook done user_id=%s product_event=%s ok=%s reason=%s queued=%s",
            user_id,
            product_event,
            result.get("ok"),
            result.get("reason"),
            result.get("queued"),
        )
    except Exception:
        logger.warning(
            "[msg_strategy] attribution hook failed user_id=%s product_event=%s",
            user_id,
            product_event,
            exc_info=True,
        )


def diagnose_event_config(
    user_id: int,
    event_key: str,
    *,
    language: str = "",
    companion_id: str = "",
    moment_id: Optional[int] = None,
) -> Dict[str, Any]:
    """诊断某用户+事件的三步配置是否齐全（供管理端排查 no_copy / no_rule）。"""
    raw_event = (event_key or "").strip()
    event = canonicalize_product_event(raw_event)
    trigger_keys = event_to_trigger_keys(event)
    lang = (language or "").strip()
    event_cid = (companion_id or "").strip()
    issues: List[str] = []
    checks: Dict[str, Any] = {}

    if not user_id or not event:
        return {"ok": False, "issues": ["invalid_user_or_event"], "event": event, "raw_event": raw_event}

    type_keys = list(
        dict.fromkeys(
            expand_library_type_keys(
                [event, *(trigger_keys or []), *trigger_to_type_keys(trigger_keys[0] if trigger_keys else event)]
            )
        )
    )

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == int(user_id)).first()
        if not user:
            return {"ok": False, "issues": ["user_not_found"], "event": event}

        g = pick_best_global(db, user)
        checks["global"] = (
            {
                "id": g.id,
                "name": g.name,
                "is_active": bool(g.is_active),
                "scope": g.user_scope_type,
                "daily_limit": int(g.daily_limit or 0),
            }
            if g
            else None
        )
        if not g:
            checks["global_note"] = "no_matching_global_no_restrict"

        matched_rules: List[Any] = []
        chosen_trigger = trigger_keys[0] if trigger_keys else event
        for tk in trigger_keys:
            rules = pick_matching_rules(db, user, tk)
            if rules:
                chosen_trigger = tk
                matched_rules = rules
                break
        checks["rules"] = []
        quota_keys = event_keys_for_daily_quota(
            event=event, raw_event=raw_event, trigger_keys=trigger_keys
        )
        for r in matched_rules:
            tr = normalize_triggers(r.triggers, db=db)
            configured = rule_want_count(tr, chosen_trigger, event, trigger_keys)
            blocked = (
                companions_at_event_quota(db, user_id, quota_keys, configured)
                if configured > 0
                else set()
            )
            pair_sent = (
                count_user_companion_event_sent(db, user_id, event_cid, quota_keys)
                if event_cid
                else 0
            )
            checks["rules"].append(
                {
                    "id": r.id,
                    "name": r.name,
                    "scope": r.user_scope_type,
                    "is_active": bool(r.is_active),
                    "triggers_for_event": {k: tr[k] for k in tr if k in trigger_keys or k == event},
                    "configured_pair_max": configured,
                    "companion_sent": pair_sent,
                    "companion_left": max(0, configured - pair_sent) if event_cid else None,
                    "blocked_companion_count": len(blocked),
                }
            )
            if configured <= 0:
                issues.append(f"trigger_count_zero:rule={r.id}")
            elif event_cid and pair_sent >= configured:
                issues.append(
                    f"companion_event_limit:rule={r.id}:companion={event_cid}:"
                    f"configured={configured}:sent={pair_sent}"
                )
        if not matched_rules:
            issues.append("no_rule_matched")

        active_libs = (
            db.query(MsgStrategyLibraryORM)
            .filter(
                MsgStrategyLibraryORM.is_active.is_(True),
                MsgStrategyLibraryORM.type_key.in_(type_keys),
            )
            .order_by(MsgStrategyLibraryORM.sort_order.desc(), MsgStrategyLibraryORM.id.asc())
            .all()
        )
        inactive_libs = (
            db.query(MsgStrategyLibraryORM)
            .filter(
                MsgStrategyLibraryORM.is_active.is_(False),
                MsgStrategyLibraryORM.type_key.in_(type_keys),
            )
            .order_by(MsgStrategyLibraryORM.id.desc())
            .all()
        )
        checks["library"] = {
            "type_keys_queried": type_keys,
            "active": [
                {
                    "id": r.id,
                    "type_key": r.type_key,
                    "language": r.language,
                    "content_len": len(r.content or ""),
                    "companion_count": len(r.companion_ids or []),
                }
                for r in active_libs
            ],
            "inactive": [
                {
                    "id": r.id,
                    "type_key": r.type_key,
                    "language": r.language,
                    "content_len": len(r.content or ""),
                    "companion_count": len(r.companion_ids or []),
                }
                for r in inactive_libs
            ],
        }
        resolved_lang = _resolve_dispatch_language(user, lang)
        locale_libs = filter_library_rows_by_locale(active_libs, resolved_lang)
        checks["library"]["locale"] = resolved_lang
        checks["library"]["active_for_locale"] = [
            {
                "id": r.id,
                "type_key": r.type_key,
                "language": r.language,
                "content_len": len(r.content or ""),
                "companion_count": len(r.companion_ids or []),
            }
            for r in locale_libs
        ]
        if active_libs and not locale_libs:
            langs = sorted({_library_row_locale(r) for r in active_libs})
            issues.append(f"library_no_locale_match:locale={resolved_lang}:available={langs}")
        if not active_libs:
            if inactive_libs:
                issues.append(
                    f"library_disabled:enable_ids={[r.id for r in inactive_libs if (r.content or '').strip()]}"
                )
            else:
                issues.append(f"library_missing:create_type_key={event}")

        if is_companion_bound_event(event) and not event_cid and moment_id:
            event_cid = _companion_id_from_moment(db, moment_id)
        bound = is_companion_bound_event(event)
        if event_cid:
            comp = _active_companion(db, event_cid, require_lang=resolved_lang)
            checks["companion"] = {
                "id": event_cid,
                "valid": bool(comp),
                "name": comp.name if comp else "",
                "language": (getattr(comp, "language", None) or "") if comp else "",
                "user_language": resolved_lang,
                "priority_event": bound,
                "sender_mode": "companion" if bound else "broadcast",
            }
            if not comp:
                raw_comp = _active_companion(db, event_cid)
                if raw_comp and not _companion_matches_user_lang(raw_comp, resolved_lang):
                    issues.append(
                        f"companion_lang_mismatch:{event_cid}:"
                        f"companion={getattr(raw_comp, 'language', None) or ''}:"
                        f"user={resolved_lang}"
                    )
                else:
                    issues.append(f"companion_invalid:{event_cid}")
        else:
            checks["companion"] = {
                "id": "",
                "valid": False,
                "priority_event": False,
                "sender_mode": "companion" if bound else "broadcast",
            }
            if bound:
                issues.append("companion_id_required_for_bound_event")

        dry = dispatch_product_event(
            int(user_id),
            raw_event,
            language=lang or None,
            companion_id=event_cid or None,
            moment_id=moment_id,
            dry_run=True,
        )
        checks["dry_run"] = dry
        if not dry.get("ok"):
            reason = dry.get("reason") or "unknown"
            if reason not in (
                "no_rule",
                "no_copy",
                "daily_limit",
                "period_limit",
                "companion_event_limit",
                "missing_companion_id",
            ):
                issues.append(f"dry_run_failed:{reason}")
            elif reason == "no_copy" and inactive_libs:
                pass  # already library_disabled
            elif reason not in issues:
                issues.append(reason)

    return {
        "ok": len(issues) == 0,
        "event": event,
        "raw_event": raw_event,
        "trigger_keys": trigger_keys,
        "issues": issues,
        "checks": checks,
    }
