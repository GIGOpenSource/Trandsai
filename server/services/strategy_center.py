"""智能体消息策略中心：按「智能体 × 策略」拆行的列表与单行 CRUD。

三类 Tab 共用筛选，列表一行只对应一个智能体；编辑/删除/开关只动该行。
埋点库 / 快回库底层仍是 companion_ids 数组，拆行后写操作会把该智能体从共享记录中拆出。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from core.database import (
    CompanionORM,
    CompanionStrategyORM,
    MsgStrategyLibraryORM,
    QuickReplyLibraryORM,
    serialize_datetime,
)
from services.companion_content import (
    apply_companion_filters,
    content_language_codes,
    product_event_meta,
    strategy_to_dict,
)
from services.msg_strategy_crud import _normalize_companion_ids


def parse_companion_ids_from_body(data: dict) -> List[str]:
    raw = data.get("companion_ids")
    if raw is None:
        raw = data.get("companions")
    ids = _normalize_companion_ids(raw)
    single = str(data.get("companion_id") or "").strip()
    if single:
        cid = single[:8]
        if cid not in ids:
            ids.append(cid)
    return [x[:8] for x in ids if x]


def parse_trigger_keyword(raw: Any) -> List[str]:
    """策略中心快回触发词：填什么存什么，不做逗号分割。空 = 兜底。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw).strip()
    return [s] if s else []


def trigger_keyword_display(keywords: Sequence[str]) -> str:
    kws = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    if not kws:
        return ""
    if len(kws) == 1:
        return kws[0]
    return ",".join(kws)


def others_after_detach(cids: Sequence[str], companion_id: str) -> Tuple[List[str], bool]:
    """从 companion_ids 去掉当前智能体。返回 (剩余 ids, 是否找到)。"""
    target = (companion_id or "").strip()
    short = target[:8]
    others: List[str] = []
    found = False
    for raw in cids or []:
        cid = str(raw or "").strip()
        if not cid:
            continue
        if cid == target or cid[:8] == short or cid == short:
            found = True
            continue
        if cid not in others:
            others.append(cid)
    return others, found


def parse_ops_admin_ids(raw: Any) -> List[int]:
    if raw is None:
        return []
    chunks = list(raw) if isinstance(raw, (list, tuple, set)) else [raw]
    out: List[int] = []
    seen: set = set()
    for item in chunks:
        text = str(item).strip()
        if not text:
            continue
        for part in text.replace("，", ",").replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                n = int(part)
            except (TypeError, ValueError):
                continue
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


def _avatar(url: Optional[str]) -> str:
    u = (url or "").strip()
    if u and u != "__GENERATING__":
        return u
    return ""


def _lang_in(column, raw: Optional[str]):
    text = (raw or "").strip()
    if not text or text.lower() in ("none", "null", "undefined"):
        return None
    codes = content_language_codes(text)
    lowered: List[str] = []
    seen = set()
    for code in list(codes) + [text]:
        key = str(code).strip().lower()
        if key and key not in seen:
            seen.add(key)
            lowered.append(key)
    if not lowered:
        return None
    return func.lower(func.coalesce(column, "")).in_(lowered)


def _ops_name_obj(brief: Optional[dict]) -> Optional[dict]:
    if not brief:
        return None
    name = (brief.get("nickname") or brief.get("username") or "").strip()
    return {"id": brief.get("id"), "name": name}


def attach_ops_admin(db: Session, items: List[dict]) -> List[dict]:
    from services.im_admin import (
        batch_admin_user_brief,
        build_companion_ops_map,
        lookup_companion_ops_map,
    )
    if not items:
        return items
    cids = [str(it.get("companion_id") or "") for it in items]
    ops_map = build_companion_ops_map(db, [c for c in cids if c])
    aids = {aid for aid in ops_map.values() if aid is not None}
    brief = batch_admin_user_brief(db, aids)
    for it in items:
        cid = str(it.get("companion_id") or "")
        ops_id = lookup_companion_ops_map(ops_map, cid)
        it["ops_admin_id"] = ops_id
        it["ops_admin"] = (
            _ops_name_obj(brief.get(int(ops_id))) if ops_id is not None else None
        )
    return items


def companion_brief(row: CompanionORM) -> dict:
    return {
        "companion_id": row.id,
        "companion_name": row.name or "",
        "companion_avatar": _avatar(row.avatar_url),
        "companion_language": row.language or "zh",
        "companion_gender": row.gender or "",
        "companion_tier": row.tier or "featured",
    }


def apply_center_companion_filters(
    q,
    db: Session,
    *,
    companion: Optional[str] = None,
    companion_id: Optional[str] = None,
    companion_name: Optional[str] = None,
    companion_language: Optional[str] = None,
    gender: Optional[str] = None,
    tier: Optional[str] = None,
    ops_admin_ids: Optional[List[int]] = None,
    id_column=None,
):
    q = q.filter(CompanionORM.is_deleted.is_(False))
    keyword = (companion or companion_name or "").strip() or None
    q = apply_companion_filters(
        q,
        companion_id=companion_id,
        keyword=keyword,
        language=companion_language,
        tier=tier,
        gender=gender,
    )
    from services.im_admin import apply_companion_ops_admin_scope_filter
    q = apply_companion_ops_admin_scope_filter(
        q,
        db,
        ops_admin_ids=ops_admin_ids or None,
        id_column=id_column or CompanionORM.id,
    )
    return q


def apply_actor_scope(q, actor: dict, column):
    from services.im_admin import companion_scope_sql_ids
    ids = companion_scope_sql_ids(actor)
    if ids is None:
        return q
    return q.filter(column.in_(ids))


def load_companions_map(db: Session, companion_ids: Sequence[str]) -> Dict[str, CompanionORM]:
    ids = [str(x).strip()[:8] for x in companion_ids if str(x).strip()]
    if not ids:
        return {}
    rows = (
        db.query(CompanionORM)
        .filter(CompanionORM.id.in_(ids), CompanionORM.is_deleted.is_(False))
        .all()
    )
    return {str(r.id): r for r in rows}


def _event_name(event_key: str) -> str:
    key = (event_key or "").strip()
    meta = product_event_meta(key)
    return meta.get("name_zh") or key


def _independent_type_key(row: CompanionStrategyORM) -> str:
    return (row.trigger_event or row.strategy_type or "").strip()


def serialize_independent(row: CompanionStrategyORM, companion: CompanionORM) -> dict:
    d = strategy_to_dict(row, companion_name=companion.name or "")
    d.update(companion_brief(companion))
    d["application_key"] = f"independent:{row.id}"
    d["type_key"] = _independent_type_key(row)
    d["type_name"] = d.get("trigger_event_name") or _event_name(d["type_key"])
    return d


def serialize_library_app(row: MsgStrategyLibraryORM, companion: CompanionORM) -> dict:
    type_key = (row.type_key or "").strip()
    return {
        "id": row.id,
        "application_key": f"msg-library:{row.id}:{companion.id}",
        "companion_id": companion.id,
        **companion_brief(companion),
        "content": row.content or "",
        "image_url": (getattr(row, "image_url", None) or "").strip(),
        "language": row.language or "zh",
        "type_key": type_key,
        "type_name": _event_name(type_key),
        "trigger_event": type_key,
        "trigger_event_name": _event_name(type_key),
        "sort_order": int(row.sort_order or 0),
        "is_active": bool(row.is_active),
        "created_by": row.created_by or "",
        "created_at": serialize_datetime(row.created_at),
        "updated_at": serialize_datetime(row.updated_at),
    }


def serialize_quick_reply_app(row: QuickReplyLibraryORM, companion: CompanionORM) -> dict:
    kws = [str(k).strip() for k in (row.keywords or []) if str(k).strip()] if isinstance(row.keywords, list) else []
    display = trigger_keyword_display(kws)
    return {
        "id": row.id,
        "application_key": f"quick-reply:{row.id}:{companion.id}",
        "companion_id": companion.id,
        **companion_brief(companion),
        "content": row.content or "",
        "language": row.language or "zh",
        "keywords": kws,
        "trigger_keywords": kws,
        "trigger_keyword": display,
        "is_fallback": not bool(kws),
        "sort_order": int(row.sort_order or 0),
        "is_active": bool(row.is_active),
        "created_by": row.created_by or "",
        "created_at": serialize_datetime(row.created_at),
        "updated_at": serialize_datetime(row.updated_at),
    }


def _attach_config_counts(
    items: List[dict],
    count_map: Dict[str, int],
    types_map: Dict[str, List[dict]],
) -> List[dict]:
    for it in items:
        cid = str(it.get("companion_id") or "")
        it["config_count"] = int(count_map.get(cid, 0))
        it["config_types"] = list(types_map.get(cid, []))
    return items


def independent_config_maps(
    db: Session, companion_ids: Sequence[str]
) -> Tuple[Dict[str, int], Dict[str, List[dict]]]:
    ids = [str(x) for x in companion_ids if x]
    if not ids:
        return {}, {}
    count_map = {
        str(cid): int(n or 0)
        for cid, n in (
            db.query(CompanionStrategyORM.companion_id, func.count(CompanionStrategyORM.id))
            .filter(CompanionStrategyORM.companion_id.in_(ids))
            .group_by(CompanionStrategyORM.companion_id)
            .all()
        )
    }
    type_rows = (
        db.query(
            CompanionStrategyORM.companion_id,
            CompanionStrategyORM.trigger_event,
            CompanionStrategyORM.strategy_type,
            func.count(CompanionStrategyORM.id),
        )
        .filter(CompanionStrategyORM.companion_id.in_(ids))
        .group_by(
            CompanionStrategyORM.companion_id,
            CompanionStrategyORM.trigger_event,
            CompanionStrategyORM.strategy_type,
        )
        .all()
    )
    types_map: Dict[str, List[dict]] = {}
    seen: Dict[str, set] = {}
    for cid, trigger_event, strategy_type, n in type_rows:
        key = (trigger_event or strategy_type or "").strip()
        if not key:
            continue
        bucket = types_map.setdefault(str(cid), [])
        used = seen.setdefault(str(cid), set())
        if key in used:
            for item in bucket:
                if item["type_key"] == key:
                    item["count"] += int(n or 0)
                    break
            continue
        used.add(key)
        bucket.append(
            {
                "type_key": key,
                "type_name": _event_name(key),
                "count": int(n or 0),
            }
        )
    return count_map, types_map


def library_config_maps(
    db: Session, companion_ids: Sequence[str]
) -> Tuple[Dict[str, int], Dict[str, List[dict]]]:
    return _json_bind_config_maps(db, MsgStrategyLibraryORM, companion_ids)


def _json_bind_config_maps(
    db: Session,
    model,
    companion_ids: Sequence[str],
    *,
    with_types: bool = True,
) -> Tuple[Dict[str, int], Dict[str, List[dict]]]:
    want = {str(x) for x in companion_ids if x}
    if not want:
        return {}, {}
    conds = [model.companion_ids.contains([cid]) for cid in want]
    rows = db.query(model).filter(or_(*conds)).all() if conds else []
    count_map: Dict[str, int] = {cid: 0 for cid in want}
    types_map: Dict[str, Dict[str, int]] = {cid: {} for cid in want}
    for row in rows:
        for cid in _normalize_companion_ids(row.companion_ids):
            if cid not in want:
                continue
            count_map[cid] = count_map.get(cid, 0) + 1
            if with_types:
                tk = str(getattr(row, "type_key", "") or "").strip()
                if tk:
                    types_map[cid][tk] = types_map[cid].get(tk, 0) + 1
    out_types: Dict[str, List[dict]] = {}
    if with_types:
        for cid, mapping in types_map.items():
            out_types[cid] = [
                {"type_key": k, "type_name": _event_name(k), "count": n}
                for k, n in mapping.items()
            ]
    return count_map, out_types


def list_independent(
    db: Session,
    actor: dict,
    *,
    page: int,
    page_size: int,
    companion: Optional[str] = None,
    companion_id: Optional[str] = None,
    companion_name: Optional[str] = None,
    companion_language: Optional[str] = None,
    language: Optional[str] = None,
    ops_admin_ids: Optional[List[int]] = None,
    type_key: Optional[str] = None,
    gender: Optional[str] = None,
    tier: Optional[str] = None,
    is_active: Optional[bool] = None,
    keyword: Optional[str] = None,
) -> Tuple[List[dict], int]:
    q = (
        db.query(CompanionStrategyORM, CompanionORM)
        .join(CompanionORM, CompanionORM.id == CompanionStrategyORM.companion_id)
    )
    q = apply_actor_scope(q, actor, CompanionStrategyORM.companion_id)
    q = apply_center_companion_filters(
        q,
        db,
        companion=companion,
        companion_id=companion_id,
        companion_name=companion_name,
        companion_language=companion_language,
        gender=gender,
        tier=tier,
        ops_admin_ids=ops_admin_ids,
    )
    lang_clause = _lang_in(CompanionStrategyORM.language, language)
    if lang_clause is not None:
        q = q.filter(lang_clause)
    event_filter = (type_key or "").strip()
    if event_filter:
        q = q.filter(
            (CompanionStrategyORM.trigger_event == event_filter)
            | (CompanionStrategyORM.strategy_type == event_filter)
        )
    if is_active is not None:
        q = q.filter(CompanionStrategyORM.is_active == is_active)
    if keyword and keyword.strip():
        q = q.filter(CompanionStrategyORM.content.ilike(f"%{keyword.strip()}%"))
    total = q.count()
    rows = (
        q.order_by(
            CompanionStrategyORM.companion_id.asc(),
            CompanionStrategyORM.sort_order.asc(),
            CompanionStrategyORM.id.asc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [serialize_independent(s, c) for s, c in rows]
    cids = [it["companion_id"] for it in items]
    count_map, types_map = independent_config_maps(db, cids)
    _attach_config_counts(items, count_map, types_map)
    return attach_ops_admin(db, items), total


def _filtered_companion_ids(
    db: Session,
    actor: dict,
    *,
    companion: Optional[str] = None,
    companion_id: Optional[str] = None,
    companion_name: Optional[str] = None,
    companion_language: Optional[str] = None,
    gender: Optional[str] = None,
    tier: Optional[str] = None,
    ops_admin_ids: Optional[List[int]] = None,
) -> Optional[set]:
    """有智能体侧筛选或运营范围时返回允许的 id 集合；超管且无筛选返回 None。"""
    from services.im_admin import companion_scope_sql_ids
    scope_ids = companion_scope_sql_ids(actor)
    has_filter = any(
        [
            (companion or "").strip(),
            (companion_id or "").strip(),
            (companion_name or "").strip(),
            (companion_language or "").strip(),
            (gender or "").strip(),
            (tier or "").strip(),
            bool(ops_admin_ids),
            scope_ids is not None,
        ]
    )
    if not has_filter:
        return None
    q = db.query(CompanionORM.id)
    q = apply_actor_scope(q, actor, CompanionORM.id)
    q = apply_center_companion_filters(
        q,
        db,
        companion=companion,
        companion_id=companion_id,
        companion_name=companion_name,
        companion_language=companion_language,
        gender=gender,
        tier=tier,
        ops_admin_ids=ops_admin_ids,
    )
    return {str(r[0]) for r in q.all()}


def list_library_apps(
    db: Session,
    actor: dict,
    *,
    page: int,
    page_size: int,
    companion: Optional[str] = None,
    companion_id: Optional[str] = None,
    companion_name: Optional[str] = None,
    companion_language: Optional[str] = None,
    language: Optional[str] = None,
    ops_admin_ids: Optional[List[int]] = None,
    type_key: Optional[str] = None,
    gender: Optional[str] = None,
    tier: Optional[str] = None,
    is_active: Optional[bool] = None,
    keyword: Optional[str] = None,
) -> Tuple[List[dict], int]:
    q = db.query(MsgStrategyLibraryORM)
    lang_clause = _lang_in(MsgStrategyLibraryORM.language, language)
    if lang_clause is not None:
        q = q.filter(lang_clause)
    if (type_key or "").strip():
        q = q.filter(MsgStrategyLibraryORM.type_key == type_key.strip())
    if is_active is not None:
        q = q.filter(MsgStrategyLibraryORM.is_active == is_active)
    if keyword and keyword.strip():
        q = q.filter(MsgStrategyLibraryORM.content.ilike(f"%{keyword.strip()}%"))
    rows = q.order_by(
        MsgStrategyLibraryORM.sort_order.asc(),
        MsgStrategyLibraryORM.id.asc(),
    ).all()

    allowed = _filtered_companion_ids(
        db,
        actor,
        companion=companion,
        companion_id=companion_id,
        companion_name=companion_name,
        companion_language=companion_language,
        gender=gender,
        tier=tier,
        ops_admin_ids=ops_admin_ids,
    )
    all_cids: List[str] = []
    seen = set()
    for row in rows:
        for cid in _normalize_companion_ids(row.companion_ids):
            if allowed is not None and cid not in allowed:
                continue
            if cid not in seen:
                seen.add(cid)
                all_cids.append(cid)
    companion_map = load_companions_map(db, all_cids)
    pairs = []
    for row in rows:
        for cid in _normalize_companion_ids(row.companion_ids):
            if allowed is not None and cid not in allowed:
                continue
            companion = companion_map.get(cid)
            if companion is None:
                continue
            pairs.append((row, companion))
    pairs.sort(key=lambda x: (x[1].id, int(x[0].sort_order or 0), int(x[0].id)))
    total = len(pairs)
    start = (page - 1) * page_size
    page_pairs = pairs[start : start + page_size]
    items = [serialize_library_app(row, companion) for row, companion in page_pairs]
    cids = [it["companion_id"] for it in items]
    count_map, types_map = library_config_maps(db, cids)
    _attach_config_counts(items, count_map, types_map)
    return attach_ops_admin(db, items), total


def list_quick_reply_apps(
    db: Session,
    actor: dict,
    *,
    page: int,
    page_size: int,
    companion: Optional[str] = None,
    companion_id: Optional[str] = None,
    companion_name: Optional[str] = None,
    companion_language: Optional[str] = None,
    language: Optional[str] = None,
    ops_admin_ids: Optional[List[int]] = None,
    gender: Optional[str] = None,
    tier: Optional[str] = None,
    is_active: Optional[bool] = None,
    keyword: Optional[str] = None,
) -> Tuple[List[dict], int]:
    from sqlalchemy import String, cast

    q = db.query(QuickReplyLibraryORM)
    lang_clause = _lang_in(QuickReplyLibraryORM.language, language)
    if lang_clause is not None:
        q = q.filter(lang_clause)
    if is_active is not None:
        q = q.filter(QuickReplyLibraryORM.is_active == is_active)
    if keyword and keyword.strip():
        like = f"%{keyword.strip()}%"
        q = q.filter(
            or_(
                QuickReplyLibraryORM.content.ilike(like),
                cast(QuickReplyLibraryORM.keywords, String).ilike(like),
            )
        )
    rows = q.order_by(
        QuickReplyLibraryORM.sort_order.asc(),
        QuickReplyLibraryORM.id.asc(),
    ).all()

    allowed = _filtered_companion_ids(
        db,
        actor,
        companion=companion,
        companion_id=companion_id,
        companion_name=companion_name,
        companion_language=companion_language,
        gender=gender,
        tier=tier,
        ops_admin_ids=ops_admin_ids,
    )
    all_cids: List[str] = []
    seen = set()
    for row in rows:
        for cid in _normalize_companion_ids(row.companion_ids):
            if allowed is not None and cid not in allowed:
                continue
            if cid not in seen:
                seen.add(cid)
                all_cids.append(cid)
    companion_map = load_companions_map(db, all_cids)
    pairs = []
    for row in rows:
        for cid in _normalize_companion_ids(row.companion_ids):
            if allowed is not None and cid not in allowed:
                continue
            companion = companion_map.get(cid)
            if companion is None:
                continue
            pairs.append((row, companion))
    pairs.sort(key=lambda x: (x[1].id, int(x[0].sort_order or 0), int(x[0].id)))
    total = len(pairs)
    start = (page - 1) * page_size
    page_pairs = pairs[start : start + page_size]
    items = [serialize_quick_reply_app(row, companion) for row, companion in page_pairs]
    return attach_ops_admin(db, items), total


def find_independent_duplicate(
    db: Session,
    *,
    companion_id: str,
    content: str,
    language: str,
    type_key: str,
    sort_order: int,
    is_active: bool,
    exclude_id: Optional[int] = None,
) -> Optional[CompanionStrategyORM]:
    q = db.query(CompanionStrategyORM).filter(
        CompanionStrategyORM.companion_id == companion_id,
        CompanionStrategyORM.content == content,
        CompanionStrategyORM.language == language,
        CompanionStrategyORM.sort_order == int(sort_order or 0),
        CompanionStrategyORM.is_active == bool(is_active),
        or_(
            CompanionStrategyORM.trigger_event == type_key,
            CompanionStrategyORM.strategy_type == type_key,
        ),
    )
    if exclude_id is not None:
        q = q.filter(CompanionStrategyORM.id != int(exclude_id))
    return q.first()


def find_library_duplicate(
    db: Session,
    *,
    companion_id: str,
    content: str,
    language: str,
    type_key: str,
    sort_order: int,
    is_active: bool,
    exclude: Optional[Tuple[int, str]] = None,
) -> Optional[MsgStrategyLibraryORM]:
    rows = (
        db.query(MsgStrategyLibraryORM)
        .filter(
            MsgStrategyLibraryORM.content == content,
            MsgStrategyLibraryORM.language == language,
            MsgStrategyLibraryORM.type_key == type_key,
            MsgStrategyLibraryORM.sort_order == int(sort_order or 0),
            MsgStrategyLibraryORM.is_active == bool(is_active),
        )
        .all()
    )
    for row in rows:
        cids = _normalize_companion_ids(row.companion_ids)
        if companion_id not in cids:
            continue
        if exclude and int(row.id) == int(exclude[0]) and companion_id == exclude[1]:
            continue
        return row
    return None


def find_quick_reply_duplicate(
    db: Session,
    *,
    companion_id: str,
    content: str,
    language: str,
    keywords: Sequence[str],
    sort_order: int,
    exclude: Optional[Tuple[int, str]] = None,
) -> Optional[QuickReplyLibraryORM]:
    want = list(keywords or [])
    rows = (
        db.query(QuickReplyLibraryORM)
        .filter(
            QuickReplyLibraryORM.content == content,
            QuickReplyLibraryORM.language == language,
            QuickReplyLibraryORM.sort_order == int(sort_order or 0),
        )
        .all()
    )
    for row in rows:
        cids = _normalize_companion_ids(row.companion_ids)
        if companion_id not in cids:
            continue
        have = [str(k).strip() for k in (row.keywords or []) if str(k).strip()] if isinstance(row.keywords, list) else []
        if have != want:
            continue
        if exclude and int(row.id) == int(exclude[0]) and companion_id == exclude[1]:
            continue
        return row
    return None


def duplicate_names(db: Session, companion_ids: Sequence[str]) -> List[str]:
    mmap = load_companions_map(db, companion_ids)
    names: List[str] = []
    for cid in companion_ids:
        row = mmap.get(cid)
        names.append((row.name if row and row.name else cid))
    return names


def get_independent(db: Session, strategy_id: int) -> Optional[Tuple[CompanionStrategyORM, CompanionORM]]:
    row = (
        db.query(CompanionStrategyORM, CompanionORM)
        .join(CompanionORM, CompanionORM.id == CompanionStrategyORM.companion_id)
        .filter(CompanionStrategyORM.id == strategy_id)
        .first()
    )
    return row


def get_library_app(
    db: Session, item_id: int, companion_id: str
) -> Optional[Tuple[MsgStrategyLibraryORM, CompanionORM]]:
    row = db.query(MsgStrategyLibraryORM).filter(MsgStrategyLibraryORM.id == item_id).first()
    if not row:
        return None
    others, found = others_after_detach(_normalize_companion_ids(row.companion_ids), companion_id)
    if not found:
        return None
    companion = load_companions_map(db, [companion_id[:8]]).get(companion_id[:8])
    if companion is None:
        return None
    return row, companion


def get_quick_reply_app(
    db: Session, item_id: int, companion_id: str
) -> Optional[Tuple[QuickReplyLibraryORM, CompanionORM]]:
    row = db.query(QuickReplyLibraryORM).filter(QuickReplyLibraryORM.id == item_id).first()
    if not row:
        return None
    _, found = others_after_detach(_normalize_companion_ids(row.companion_ids), companion_id)
    if not found:
        return None
    companion = load_companions_map(db, [companion_id[:8]]).get(companion_id[:8])
    if companion is None:
        return None
    return row, companion


def create_independent_rows(
    db: Session,
    *,
    companion_ids: List[str],
    content: str,
    language: str,
    type_key: str,
    sort_order: int,
    is_active: bool,
    created_by: str = "",
    image_url: str = "",
) -> List[CompanionStrategyORM]:
    rows: List[CompanionStrategyORM] = []
    now = datetime.now(timezone.utc)
    for cid in companion_ids:
        row = CompanionStrategyORM(
            companion_id=cid,
            strategy_type=type_key,
            content=content,
            image_url=(image_url or "").strip()[:500],
            language=language,
            trigger_event=type_key,
            source="manual",
            sort_order=int(sort_order or 0),
            is_active=bool(is_active),
            created_by=(created_by or "")[:64],
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def create_library_row(
    db: Session,
    *,
    companion_ids: List[str],
    content: str,
    language: str,
    type_key: str,
    sort_order: int,
    is_active: bool,
    created_by: str = "",
    image_url: str = "",
) -> MsgStrategyLibraryORM:
    row = MsgStrategyLibraryORM(
        content=content,
        image_url=(image_url or "").strip()[:500],
        language=language,
        type_key=type_key,
        companion_ids=list(companion_ids),
        sort_order=int(sort_order or 0),
        is_active=bool(is_active),
        created_by=(created_by or "")[:64],
    )
    db.add(row)
    db.flush()
    return row


def create_quick_reply_row(
    db: Session,
    *,
    companion_ids: List[str],
    content: str,
    language: str,
    keywords: List[str],
    sort_order: int,
    is_active: bool,
    created_by: str = "",
) -> QuickReplyLibraryORM:
    row = QuickReplyLibraryORM(
        content=content,
        language=language,
        keywords=list(keywords),
        companion_ids=list(companion_ids),
        sort_order=int(sort_order or 0),
        is_active=bool(is_active),
        created_by=(created_by or "")[:64],
    )
    db.add(row)
    db.flush()
    from services.quick_reply import ensure_quick_reply_configs

    ensure_quick_reply_configs(db, companion_ids)
    return row


def _touch(row) -> None:
    row.updated_at = datetime.now(timezone.utc)


def split_or_update_library(
    db: Session,
    row: MsgStrategyLibraryORM,
    companion_id: str,
    *,
    delete: bool = False,
    content: Optional[str] = None,
    language: Optional[str] = None,
    type_key: Optional[str] = None,
    sort_order: Optional[int] = None,
    is_active: Optional[bool] = None,
    image_url: Optional[str] = None,
) -> Optional[MsgStrategyLibraryORM]:
    cids = _normalize_companion_ids(row.companion_ids)
    others, found = others_after_detach(cids, companion_id)
    if not found:
        return None
    cid = companion_id[:8]
    if delete:
        if not others:
            db.delete(row)
        else:
            row.companion_ids = others
            flag_modified(row, "companion_ids")
            _touch(row)
        db.flush()
        return None
    if not others:
        if content is not None:
            row.content = content
        if language is not None:
            row.language = language
        if type_key is not None:
            row.type_key = type_key
        if sort_order is not None:
            row.sort_order = int(sort_order)
        if is_active is not None:
            row.is_active = bool(is_active)
        if image_url is not None:
            row.image_url = (image_url or "").strip()[:500]
        _touch(row)
        db.flush()
        return row
    row.companion_ids = others
    flag_modified(row, "companion_ids")
    _touch(row)
    cloned = MsgStrategyLibraryORM(
        content=row.content if content is None else content,
        image_url=(row.image_url or "") if image_url is None else (image_url or "").strip()[:500],
        language=row.language if language is None else language,
        type_key=row.type_key if type_key is None else type_key,
        companion_ids=[cid],
        sort_order=int(row.sort_order or 0) if sort_order is None else int(sort_order),
        is_active=bool(row.is_active) if is_active is None else bool(is_active),
        created_by=row.created_by or "",
    )
    db.add(cloned)
    db.flush()
    return cloned


def split_or_update_quick_reply(
    db: Session,
    row: QuickReplyLibraryORM,
    companion_id: str,
    *,
    delete: bool = False,
    content: Optional[str] = None,
    language: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    sort_order: Optional[int] = None,
    is_active: Optional[bool] = None,
) -> Optional[QuickReplyLibraryORM]:
    cids = _normalize_companion_ids(row.companion_ids)
    others, found = others_after_detach(cids, companion_id)
    if not found:
        return None
    cid = companion_id[:8]
    if delete:
        if not others:
            db.delete(row)
        else:
            row.companion_ids = others
            flag_modified(row, "companion_ids")
            _touch(row)
        db.flush()
        return None
    if not others:
        if content is not None:
            row.content = content
        if language is not None:
            row.language = language
        if keywords is not None:
            row.keywords = list(keywords)
            flag_modified(row, "keywords")
        if sort_order is not None:
            row.sort_order = int(sort_order)
        if is_active is not None:
            row.is_active = bool(is_active)
        _touch(row)
        db.flush()
        return row
    row.companion_ids = others
    flag_modified(row, "companion_ids")
    _touch(row)
    have_kws = [str(k).strip() for k in (row.keywords or []) if str(k).strip()] if isinstance(row.keywords, list) else []
    cloned = QuickReplyLibraryORM(
        content=row.content if content is None else content,
        language=row.language if language is None else language,
        keywords=list(have_kws if keywords is None else keywords),
        companion_ids=[cid],
        sort_order=int(row.sort_order or 0) if sort_order is None else int(sort_order),
        is_active=bool(row.is_active) if is_active is None else bool(is_active),
        created_by=row.created_by or "",
    )
    db.add(cloned)
    db.flush()
    return cloned
