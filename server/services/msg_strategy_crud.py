"""消息策略 CRUD 序列化与用户范围匹配。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

from core.database import (
    CompanionORM,
    MsgStrategyGlobalORM,
    MsgStrategyLibraryORM,
    MsgStrategyRuleORM,
    UserORM,
)
from services.msg_strategy_meta import normalize_triggers, event_to_trigger_keys

# 全局配置新建时的推荐默认值（仅种子/展示；保存接口不限制 daily_limit 上限）
GLOBAL_CONFIG_DEFAULTS = {
    "fatigue_window_hours": 2,
    "fatigue_max_unreplied": 10,
    "daily_limit": 20,
    "interval_min_sec": 15,
    "interval_max_sec": 45,
}


def _parse_nonneg_int(value: Any) -> Optional[int]:
    """解析非负整数；无效类型/格式返回 None（不抛错）。"""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, bool):
            return None
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _int_field(value: Any, default: int) -> int:
    parsed = _parse_nonneg_int(value)
    return default if parsed is None else parsed


def global_to_dict(row: MsgStrategyGlobalORM) -> dict:
    daily = row.daily_limit
    if daily is None:
        daily = GLOBAL_CONFIG_DEFAULTS["daily_limit"]
    return {
        "id": row.id,
        "name": row.name or "",
        "user_scope_type": row.user_scope_type or "all",
        "user_scope_value": row.user_scope_value or "",
        "fatigue_window_hours": _int_field(row.fatigue_window_hours, GLOBAL_CONFIG_DEFAULTS["fatigue_window_hours"]),
        "fatigue_max_unreplied": _int_field(row.fatigue_max_unreplied, GLOBAL_CONFIG_DEFAULTS["fatigue_max_unreplied"]),
        "daily_limit": int(daily),
        "interval_min_sec": _int_field(row.interval_min_sec, GLOBAL_CONFIG_DEFAULTS["interval_min_sec"]),
        "interval_max_sec": _int_field(row.interval_max_sec, GLOBAL_CONFIG_DEFAULTS["interval_max_sec"]),
        "priority": int(row.priority or 0),
        "is_active": bool(row.is_active),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def rule_to_dict(row: MsgStrategyRuleORM) -> dict:
    return {
        "id": row.id,
        "name": row.name or "",
        "user_scope_type": row.user_scope_type or "all",
        "user_scope_value": row.user_scope_value or "",
        "triggers": dict(row.triggers or {}),
        "priority": int(row.priority or 0),
        "is_active": bool(row.is_active),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _normalize_companion_ids(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for x in raw:
        if isinstance(x, dict):
            cid = str(x.get("id") or "").strip()
        else:
            cid = str(x or "").strip()
        if cid and cid not in out:
            out.append(cid)
    return out


def load_companion_id_name_map(db: Session, companion_ids: Sequence[str]) -> Dict[str, str]:
    """一次 SQL 查出 id→name；不存在的 id 不出现在 map 里。"""
    ids = [str(x).strip() for x in companion_ids if str(x).strip()]
    if not ids:
        return {}
    rows = (
        db.query(CompanionORM.id, CompanionORM.name)
        .filter(CompanionORM.id.in_(ids))
        .all()
    )
    return {str(r.id): (r.name or "") for r in rows}


def companions_payload(ids: Sequence[str], name_map: Dict[str, str]) -> List[dict]:
    """按原 id 顺序返回 {id,name}；库中无记录时 name 为空串。"""
    return [{"id": cid, "name": name_map.get(cid, "")} for cid in ids]


def library_scope(companion_ids: Any) -> str:
    """global=作用全部智能体（companion_ids 空）；precise=指定智能体。"""
    return "precise" if _normalize_companion_ids(companion_ids) else "global"


def library_to_dict(
    row: MsgStrategyLibraryORM,
    *,
    name_map: Optional[Dict[str, str]] = None,
) -> dict:
    cids = _normalize_companion_ids(row.companion_ids)
    nm = name_map or {}
    scope = library_scope(cids)
    payload = {
        "id": row.id,
        "content": row.content or "",
        "language": row.language or "zh",
        "type_key": row.type_key or "",
        "companion_ids": cids,
        "companions": companions_payload(cids, nm),
        "scope": scope,  # global | precise
        "is_active": bool(row.is_active),
        "sort_order": int(row.sort_order or 0),
        "created_by": row.created_by or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if scope != "global":
        payload["image_url"] = (getattr(row, "image_url", None) or "").strip()
    return payload


def enrich_library_rows(db: Session, rows: Iterable[MsgStrategyLibraryORM]) -> List[dict]:
    """列表/详情：收集全部 companion_ids，一遍 SQL 补 name。"""
    row_list = list(rows)
    all_ids: List[str] = []
    seen = set()
    for row in row_list:
        for cid in _normalize_companion_ids(row.companion_ids):
            if cid not in seen:
                seen.add(cid)
                all_ids.append(cid)
    name_map = load_companion_id_name_map(db, all_ids)
    return [library_to_dict(r, name_map=name_map) for r in row_list]


def parse_scope_tags(value: str) -> List[str]:
    raw = (value or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            import json
            arr = json.loads(raw)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            pass
    return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]


def normalize_scope_value(raw: Any) -> str:
    """多选：list → 逗号串；str 原样 trim；其它转 str。落库统一 Text。"""
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple, set)):
        parts = []
        for x in raw:
            s = str(x or "").strip()
            if s and s not in parts:
                parts.append(s)
        return ",".join(parts)
    return str(raw).strip()


# 作用范围特异性：Ads > 用户属性 > 全部（同特异性内再比 priority）
_SCOPE_RANK = {"ads": 0, "attr": 1, "all": 2}


def _normalize_scope_type(raw: str) -> str:
    st = (raw or "all").strip().lower()
    if st in ("ads", "ad", "ua"):
        return "ads"
    if st in ("attr", "attribute", "user", "tags", "用户属性", "属性"):
        return "attr"
    if st in ("", "all", "全部"):
        return "all"
    return st


def _user_tag_set(user: Optional[UserORM], scope_type: str) -> set:
    """ads → ua_tags；attr → users.tags（运营属性标记）。"""
    if not user:
        return set()
    st = _normalize_scope_type(scope_type)
    if st == "ads":
        raw = list(getattr(user, "ua_tags", None) or [])
    elif st == "attr":
        raw = list(getattr(user, "tags", None) or [])
    else:
        return set()
    return {str(t).strip().lower() for t in raw if str(t).strip()}


def user_matches_scope(
    user: Optional[UserORM],
    scope_type: str,
    scope_value: str,
) -> bool:
    st = _normalize_scope_type(scope_type)
    if st == "all":
        return True
    if not user:
        return False
    tags_l = _user_tag_set(user, st)
    want = {t.lower() for t in parse_scope_tags(scope_value)}
    if not want:
        # 未填具体标记：ads=任意广告用户（有 ua_tag）；attr=任意有属性标记用户
        return bool(tags_l)
    return bool(tags_l & want)


def _pick_per_scope_winners(
    rows,
    user: Optional[UserORM],
    *,
    require_trigger: Optional[str] = None,
    trigger_aliases: Optional[List[str]] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """匹配用户的策略：每个 user_scope_type（ads/attr/all）各保留 priority 最高的一条。"""
    per_scope: Dict[str, Any] = {}
    alias_keys: List[str] = []
    if require_trigger:
        alias_keys = [require_trigger]
        for k in trigger_aliases or []:
            kk = (k or "").strip()
            if kk and kk not in alias_keys:
                alias_keys.append(kk)
    for r in rows:
        if require_trigger is not None:
            triggers = normalize_triggers(getattr(r, "triggers", None), db=db)
            if not any(int(triggers.get(k) or 0) > 0 for k in alias_keys):
                continue
        st = _normalize_scope_type(getattr(r, "user_scope_type", None) or "all")
        if not user_matches_scope(user, st, getattr(r, "user_scope_value", None) or ""):
            continue
        pri = int(getattr(r, "priority", None) or 0)
        rid = int(getattr(r, "id", None) or 0)
        prev = per_scope.get(st)
        if prev is None or pri > prev[0] or (pri == prev[0] and rid < prev[1]):
            per_scope[st] = (pri, rid, r)
    return {st: r for st, (_, _, r) in per_scope.items()}


def _pick_single_best_from_scope_winners(winners: Dict[str, Any]):
    """全局控制：ads > attr > all，同级比 priority（只返回 1 条）。"""
    best = None
    best_rank = 99
    best_pri = -10**9
    best_id = 10**18
    for st, r in winners.items():
        rank = _SCOPE_RANK.get(st, 2)
        pri = int(getattr(r, "priority", None) or 0)
        rid = int(getattr(r, "id", None) or 0)
        if (
            best is None
            or rank < best_rank
            or (rank == best_rank and pri > best_pri)
            or (rank == best_rank and pri == best_pri and rid < best_id)
        ):
            best = r
            best_rank = rank
            best_pri = pri
            best_id = rid
    return best


def _pick_best_by_scope(
    rows,
    user: Optional[UserORM],
    *,
    require_trigger: Optional[str] = None,
    db: Optional[Session] = None,
):
    """全局：各范围类型先各取一条，再按 ads>attr>all 只留最终 1 条。"""
    winners = _pick_per_scope_winners(
        rows, user, require_trigger=require_trigger, db=db
    )
    return _pick_single_best_from_scope_winners(winners)


def pick_best_global(db: Session, user: Optional[UserORM]) -> Optional[MsgStrategyGlobalORM]:
    rows = (
        db.query(MsgStrategyGlobalORM)
        .filter(MsgStrategyGlobalORM.is_active.is_(True))
        .order_by(MsgStrategyGlobalORM.priority.desc(), MsgStrategyGlobalORM.id.asc())
        .all()
    )
    return _pick_best_by_scope(rows, user, db=db)


def pick_best_rule(
    db: Session,
    user: Optional[UserORM],
    trigger_key: str,
) -> Optional[MsgStrategyRuleORM]:
    """兼容：等价于 pick_matching_rules 中 priority 最高的一条（按 ads>attr>all）。"""
    matched = pick_matching_rules(db, user, trigger_key)
    if not matched:
        return None
    winners = { _normalize_scope_type(r.user_scope_type or "all"): r for r in matched }
    return _pick_single_best_from_scope_winners(winners)


def pick_matching_rules(
    db: Session,
    user: Optional[UserORM],
    trigger_key: str,
) -> List[MsgStrategyRuleORM]:
    """命中当前 trigger 的规则：ads / attr / all 各范围类型各 1 条（该类型内 priority 最高），
    范围包含用户则激活；多条并行（不再 ads 覆盖 attr/all）。
    """
    rows = (
        db.query(MsgStrategyRuleORM)
        .filter(MsgStrategyRuleORM.is_active.is_(True))
        .order_by(MsgStrategyRuleORM.priority.desc(), MsgStrategyRuleORM.id.asc())
        .all()
    )
    winners = _pick_per_scope_winners(
        rows,
        user,
        require_trigger=trigger_key,
        trigger_aliases=event_to_trigger_keys(trigger_key),
        db=db,
    )
    order = ("ads", "attr", "all")
    return [winners[st] for st in order if st in winners]


def apply_global_fields(row: MsgStrategyGlobalORM, data: dict) -> None:
    if "name" in data:
        row.name = (data.get("name") or "").strip()[:120]
    if "user_scope_type" in data:
        row.user_scope_type = _normalize_scope_type(data.get("user_scope_type") or "all")
    if "user_scope_value" in data:
        row.user_scope_value = normalize_scope_value(data.get("user_scope_value"))
    if "fatigue_window_hours" in data:
        parsed = _parse_nonneg_int(data.get("fatigue_window_hours"))
        if parsed is not None:
            row.fatigue_window_hours = parsed
    if "fatigue_max_unreplied" in data:
        parsed = _parse_nonneg_int(data.get("fatigue_max_unreplied"))
        if parsed is not None:
            row.fatigue_max_unreplied = parsed
    if "daily_limit" in data:
        # 每日限制：仅要求 >=0，不设上限（20 仅为 DB/种子默认值）
        parsed = _parse_nonneg_int(data.get("daily_limit"))
        if parsed is not None:
            row.daily_limit = parsed
    if "interval_min_sec" in data:
        parsed = _parse_nonneg_int(data.get("interval_min_sec"))
        if parsed is not None:
            row.interval_min_sec = parsed
    if "interval_max_sec" in data:
        parsed = _parse_nonneg_int(data.get("interval_max_sec"))
        if parsed is not None:
            row.interval_max_sec = max(row.interval_min_sec or 0, parsed)
    if "priority" in data:
        row.priority = int(data.get("priority") or 0)
    if "is_active" in data:
        row.is_active = bool(data.get("is_active"))


def apply_rule_fields(row: MsgStrategyRuleORM, data: dict, db: Optional[Session] = None) -> None:
    if "name" in data:
        row.name = (data.get("name") or "").strip()[:120]
    if "user_scope_type" in data:
        row.user_scope_type = _normalize_scope_type(data.get("user_scope_type") or "all")
    if "user_scope_value" in data:
        row.user_scope_value = normalize_scope_value(data.get("user_scope_value"))
    if "triggers" in data:
        row.triggers = normalize_triggers(data.get("triggers"), db=db)
    if "priority" in data:
        row.priority = int(data.get("priority") or 0)
    if "is_active" in data:
        row.is_active = bool(data.get("is_active"))


def apply_library_fields(
    row: MsgStrategyLibraryORM,
    data: dict,
    *,
    force_global: bool = False,
) -> None:
    """force_global=True：/api/admin/msg-strategy/library 全局库，忽略 companion_ids。"""
    if "content" in data:
        row.content = (data.get("content") or "").strip()
    if "image_url" in data and not force_global:
        from services.strategy_image import normalize_strategy_image_url

        row.image_url = normalize_strategy_image_url(data.get("image_url"))
    if "language" in data:
        row.language = (data.get("language") or "zh").strip()[:10]
    if "type_key" in data:
        row.type_key = (data.get("type_key") or "").strip()[:64]
    if force_global:
        row.companion_ids = []
    elif "companion_ids" in data or "companions" in data:
        # 精准库（策略中心）仍可写指定智能体
        raw = data.get("companion_ids")
        if raw is None:
            raw = data.get("companions")
        row.companion_ids = _normalize_companion_ids(raw)
    if "sort_order" in data:
        row.sort_order = int(data.get("sort_order") or 0)
    if "is_active" in data:
        row.is_active = bool(data.get("is_active"))
    if "created_by" in data:
        row.created_by = (data.get("created_by") or "").strip()[:64]
