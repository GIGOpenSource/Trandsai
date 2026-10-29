"""管理端 IM：责任运营、接管状态、审计、运营 WS 广播。"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from starlette.websockets import WebSocket, WebSocketState

from core.auth import get_admin_token_payload
from core.database import (
    AdminImAuditLogORM,
    CompanionORM,
    ShortTermMessageORM,
    UserCompanionStateORM,
    UserORM,
    get_db,
    serialize_datetime,
)

logger = logging.getLogger(__name__)

# (user_id, companion_id) → 运营后台会话 WS 连接集合
_admin_im_ws: Dict[Tuple[int, str], Set[WebSocket]] = {}
_admin_im_lock = asyncio.Lock()

# admin_id → 运营 IM 收件箱 WS（列表响铃 / 未读）
_admin_im_inbox: Dict[int, Set[WebSocket]] = {}
_admin_im_inbox_lock = asyncio.Lock()


def session_key(user_id: int, companion_id: str) -> Tuple[int, str]:
    return (int(user_id), str(companion_id)[:8])


def _normalize_admin_companion_ids(raw: Any) -> List[str]:
    """解析 users.admin_companion_ids（与 admin.py _parse_companion_ids 语义一致）。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s.startswith("[") or s.startswith("{"):
            try:
                return _normalize_admin_companion_ids(json.loads(s))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        parts = [x.strip() for x in s.replace("，", ",").split(",")]
        out: List[str] = []
        for p in parts:
            if p and p not in out:
                out.append(p)
        return out
    if isinstance(raw, dict):
        cid = str(raw.get("id") or raw.get("companion_id") or "").strip()
        return [cid] if cid else []
    if isinstance(raw, (list, tuple, set)):
        out: List[str] = []
        for x in raw:
            if isinstance(x, dict):
                s = str(x.get("id") or x.get("companion_id") or "").strip()
            else:
                s = str(x).strip()
            if s and s not in out:
                out.append(s)
        return out
    return []


def resolve_admin_actor(token: str) -> Dict[str, Any]:
    """从 admin token 解析操作者。无 user_id 的环境密码登录视为超管。"""
    payload = get_admin_token_payload(token) or {}
    token_role = (payload.get("role_key") or "").strip().lower() or "admin"
    role_key = token_role
    uid = payload.get("user_id")
    try:
        admin_id = int(uid) if uid is not None else None
    except (TypeError, ValueError):
        admin_id = None
    username = (payload.get("username") or "").strip()
    companion_ids: List[str] = []
    if admin_id:
        with get_db() as db:
            u = db.query(UserORM).filter(UserORM.id == admin_id).first()
            if u:
                if not username:
                    username = u.username or ""
                db_role = (u.role or "").strip().lower()
                # 以库内角色为准
                if db_role:
                    role_key = db_role
                companion_ids = _normalize_admin_companion_ids(
                    getattr(u, "admin_companion_ids", None)
                )
    # 超管：环境密码登录（无 user_id）；或库内 role=admin（超级管理员）
    is_super = admin_id is None or role_key == "admin"
    return {
        "admin_id": admin_id,
        "username": username,
        "role_key": role_key,
        "is_super": is_super,
        "companion_ids": companion_ids,
    }


def actor_is_super_admin(actor: Dict[str, Any]) -> bool:
    """是否 IM 超管视角（可看全部会话）。仅 role=admin 或环境登录。"""
    return bool(actor.get("is_super"))


def get_companion_scope(actor: Dict[str, Any]) -> Optional[Set[str]]:
    """运营智能体白名单。None=不限制（仅超管）；非超管返回 set（可为空）。"""
    if actor_is_super_admin(actor):
        return None
    return {
        str(x).strip()
        for x in (actor.get("companion_ids") or [])
        if str(x).strip()
    }


def companion_scope_sql_ids(actor: Dict[str, Any]) -> Optional[List[str]]:
    """列表过滤用 ID（含 8 位短码变体）。None=不限制（超管）。"""
    scope = get_companion_scope(actor)
    if scope is None:
        return None
    if not scope:
        return []
    out: Set[str] = set()
    for s in scope:
        out.add(s)
        if len(s) > 8:
            out.add(s[:8])
    return list(out)


def companion_in_scope(actor: Dict[str, Any], companion_id: Optional[str]) -> bool:
    if actor_is_super_admin(actor):
        return True
    scope = get_companion_scope(actor)
    if not scope:
        return False
    cid = (companion_id or "").strip()
    if not cid:
        return False
    if cid in scope:
        return True
    short = cid[:8]
    if short in scope:
        return True
    return any(s[:8] == short or s == cid for s in scope)


def assert_companion_in_scope(
    actor: Dict[str, Any],
    companion_id: Optional[str],
    lang: str = "zh",
) -> None:
    """越权访问白名单外智能体 → 403。"""
    if companion_in_scope(actor, companion_id):
        return
    from fastapi import HTTPException
    from api.admin_common import _get_error_msg

    raise HTTPException(
        status_code=403,
        detail=_get_error_msg("companion_scope_forbidden", lang),
    )


def is_admin_account(db, user_id: int) -> bool:
    u = db.query(UserORM).filter(UserORM.id == int(user_id)).first()
    if not u:
        return False
    role = (u.role or "").strip().lower()
    return bool(role) and role != "user"


def normalize_companion_id_for_match(companion_id: Optional[str]) -> str:
    """IM / 匹配用：统一截断为 8 位（与 session_key 一致）。"""
    return str(companion_id or "").strip()[:8]


def _companion_match_keys(companion_id: str) -> Set[str]:
    """生成用于 admin_companion_ids 反查的 ID 变体集合。"""
    cid = str(companion_id or "").strip()
    if not cid:
        return set()
    keys = {cid}
    short = cid[:8]
    if short:
        keys.add(short)
    return keys


def _companion_id_in_list(companion_id: str, id_list: List[str]) -> bool:
    keys = _companion_match_keys(companion_id)
    for stored in id_list:
        s = str(stored or "").strip()
        if not s:
            continue
        if s in keys or s[:8] in keys:
            return True
        if any(k == s or k == s[:8] for k in keys):
            return True
    return False


def _remove_companion_from_id_list(companion_id: str, id_list: List[str]) -> List[str]:
    keys = _companion_match_keys(companion_id)
    out: List[str] = []
    for stored in id_list:
        s = str(stored or "").strip()
        if not s:
            continue
        if s in keys or s[:8] in keys:
            continue
        if any(s == k or s[:8] == k for k in keys):
            continue
        out.append(s)
    return out


def _canonical_companion_id(db, companion_id: str) -> str:
    """优先用 companions 表主键；不存在则保留入参截断值。"""
    cid = str(companion_id or "").strip()
    if not cid:
        return ""
    row = db.query(CompanionORM.id).filter(CompanionORM.id == cid).first()
    if row:
        return str(row[0])
    short = cid[:8]
    if short and short != cid:
        row = db.query(CompanionORM.id).filter(CompanionORM.id == short).first()
        if row:
            return str(row[0])
    return short or cid


def _iter_admin_users(db):
    rows = (
        db.query(UserORM)
        .filter(UserORM.role.isnot(None), UserORM.role != "")
        .all()
    )
    for u in rows:
        if (u.role or "").strip().lower() in ("", "user"):
            continue
        yield u


def _build_global_companion_ops_index(db) -> Dict[str, int]:
    """companion id 变体 → 责任运营 admin_id（冲突取较小 id）。"""
    index: Dict[str, int] = {}
    for admin in _iter_admin_users(db):
        for cid in _normalize_admin_companion_ids(getattr(admin, "admin_companion_ids", None)):
            for key in _companion_match_keys(cid):
                prev = index.get(key)
                if prev is None or int(admin.id) < int(prev):
                    index[key] = int(admin.id)
    return index


def resolve_companion_ops_admin(db, companion_id: Optional[str]) -> Optional[int]:
    """按智能体反查责任运营（users.admin_companion_ids）。"""
    cid = str(companion_id or "").strip()
    if not cid:
        return None
    index = _build_global_companion_ops_index(db)
    best: Optional[int] = None
    for key in _companion_match_keys(cid):
        aid = index.get(key)
        if aid is not None and (best is None or int(aid) < int(best)):
            best = int(aid)
    return best


def get_session_ops_admin(db, companion_id: Optional[str]) -> Optional[int]:
    """会话列表/权限/未读统一入口：智能体级责任人。"""
    return resolve_companion_ops_admin(db, companion_id)


def build_companion_ops_map(
    db, companion_ids: List[str]
) -> Dict[str, Optional[int]]:
    """批量解析 companion_id → ops_admin_id（键为入参原始 id 及 8 位短码）。"""
    index = _build_global_companion_ops_index(db)
    out: Dict[str, Optional[int]] = {}
    seen: Set[str] = set()
    for raw in companion_ids:
        cid = str(raw or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        best: Optional[int] = None
        for key in _companion_match_keys(cid):
            aid = index.get(key)
            if aid is not None and (best is None or int(aid) < int(best)):
                best = int(aid)
        out[cid] = best
        short = cid[:8]
        if short and short not in out:
            out[short] = best
    return out


def lookup_companion_ops_map(
    ops_map: Dict[str, Optional[int]], companion_id: Optional[str]
) -> Optional[int]:
    """从 build_companion_ops_map 结果取值，兼容 8 位/完整 id。"""
    cid = str(companion_id or "").strip()
    if not cid:
        return None
    if cid in ops_map:
        return ops_map[cid]
    short = cid[:8]
    if short in ops_map:
        return ops_map[short]
    for key in _companion_match_keys(cid):
        if key in ops_map:
            return ops_map[key]
    return None


def _expand_companion_ids_for_sql(db, companion_ids: List[str]) -> List[str]:
    """admin_companion_ids → companions 表 id 变体（供 SQL IN）。"""
    out: Set[str] = set()
    for s in companion_ids:
        s = str(s or "").strip()
        if not s:
            continue
        out.add(s)
        if len(s) > 8:
            out.add(s[:8])
        else:
            for row in (
                db.query(CompanionORM.id)
                .filter(CompanionORM.id.like(f"{s}%"))
                .limit(50)
                .all()
            ):
                out.add(str(row[0]))
    return list(out)


def list_companion_ids_for_ops_admin(db, ops_admin_id: int) -> List[str]:
    """某责任运营负责的全部智能体 ID（含 8 位变体，供 SQL IN 过滤）。"""
    u = db.query(UserORM).filter(UserORM.id == int(ops_admin_id)).first()
    if not u:
        return []
    ids = _normalize_admin_companion_ids(getattr(u, "admin_companion_ids", None))
    return _expand_companion_ids_for_sql(db, ids)


def list_companion_ids_for_ops_admins(db, ops_admin_ids: List[int]) -> List[str]:
    """多个责任运营负责的智能体 ID 并集。"""
    raw: List[str] = []
    seen: Set[str] = set()
    for aid in ops_admin_ids or []:
        try:
            n = int(aid)
        except (TypeError, ValueError):
            continue
        u = db.query(UserORM).filter(UserORM.id == n).first()
        if not u:
            continue
        for cid in _normalize_admin_companion_ids(getattr(u, "admin_companion_ids", None)):
            if cid not in seen:
                seen.add(cid)
                raw.append(cid)
    return _expand_companion_ids_for_sql(db, raw)


def list_companion_ids_with_ops_admin(db) -> List[str]:
    """所有已分配责任运营的智能体 ID。"""
    raw: List[str] = []
    seen: Set[str] = set()
    for admin in _iter_admin_users(db):
        for cid in _normalize_admin_companion_ids(getattr(admin, "admin_companion_ids", None)):
            if cid not in seen:
                seen.add(cid)
                raw.append(cid)
    return _expand_companion_ids_for_sql(db, raw)


def apply_companion_ops_admin_scope_filter(
    q,
    db,
    *,
    has_ops_admin: Optional[bool] = None,
    ops_admin_ids: Optional[List[int]] = None,
    id_column=None,
):
    """内容管理等列表：按责任运营筛选 companions。"""
    if id_column is None:
        id_column = CompanionORM.id

    scope_ids: Optional[List[str]] = None
    exclude_assigned = False
    if ops_admin_ids:
        scope_ids = list_companion_ids_for_ops_admins(db, ops_admin_ids)
    elif has_ops_admin is True:
        scope_ids = list_companion_ids_with_ops_admin(db)
    elif has_ops_admin is False:
        scope_ids = list_companion_ids_with_ops_admin(db)
        exclude_assigned = True

    if scope_ids is None:
        return q
    if exclude_assigned:
        if scope_ids:
            return q.filter(~id_column.in_(scope_ids))
        return q
    if scope_ids:
        return q.filter(id_column.in_(scope_ids))
    return q.filter(id_column == "")


OPS_TIER = "ops"
OPS_TAG = "ops"


def _lookup_companion_row(db, companion_id: str) -> Optional[CompanionORM]:
    cid = str(companion_id or "").strip()
    if not cid:
        return None
    row = db.query(CompanionORM).filter(CompanionORM.id == cid).first()
    if not row and len(cid) >= 8:
        row = db.query(CompanionORM).filter(CompanionORM.id == cid[:8]).first()
    return row


def _apply_companion_tier_cache(row: CompanionORM, tier_val: str) -> None:
    try:
        from core.state import get_companion_manager

        cm = get_companion_manager()
        if cm is not None:
            cached = cm._companions.get(str(row.id))
            if cached is not None and getattr(cached, "profile", None) is not None:
                cached.profile.tier = tier_val
    except Exception:
        logger.debug(
            "sync companion cache tier failed cid=%s", getattr(row, "id", None), exc_info=True
        )


def _sync_companion_ops_tag(row: CompanionORM, *, assigned: bool) -> bool:
    """有责任人 → tags 追加 ops；取消 → 去掉 ops（保留其它手工标签）。返回是否改动。"""
    from sqlalchemy.orm.attributes import flag_modified

    raw = list(getattr(row, "tags", None) or [])
    tags: List[str] = []
    for item in raw:
        t = str(item or "").strip()
        if t and t not in tags:
            tags.append(t)
    changed = False
    if assigned:
        if OPS_TAG not in tags:
            tags.append(OPS_TAG)
            changed = True
    else:
        if OPS_TAG in tags:
            tags = [t for t in tags if t != OPS_TAG]
            changed = True
    if not changed:
        return False
    row.tags = tags
    try:
        flag_modified(row, "tags")
    except Exception:
        pass
    return True


def sync_companion_ops_tier(db, companion_id: str, *, assigned: bool) -> None:
    """有责任人 → tier=ops + tags 含 ops；取消分配 → 去掉 ops 分层/标签，恢复原分层。"""
    row = _lookup_companion_row(db, companion_id)
    if not row:
        return
    current = (row.tier or "").strip().lower()
    if assigned:
        if current != OPS_TIER:
            if not (getattr(row, "pre_ops_tier", None) or "").strip():
                row.pre_ops_tier = (row.tier or "").strip() or None
            row.tier = OPS_TIER
            _apply_companion_tier_cache(row, OPS_TIER)
        _sync_companion_ops_tag(row, assigned=True)
        return
    # 取消分配：去掉 ops 标签；若当前 tier 为 ops 则恢复原分层
    _sync_companion_ops_tag(row, assigned=False)
    if current != OPS_TIER:
        return
    prev = (getattr(row, "pre_ops_tier", None) or "").strip()
    row.tier = prev
    row.pre_ops_tier = None
    _apply_companion_tier_cache(row, prev)


def sync_all_assigned_companions_ops_tier(db) -> Dict[str, Any]:
    """存量：已分配责任人的智能体统一 tier=ops 且 tags 含 ops。"""
    ids = list_companion_ids_with_ops_admin(db)
    rows = (
        db.query(CompanionORM)
        .filter(CompanionORM.id.in_(ids))
        .all()
        if ids
        else []
    )
    updated = 0
    for row in rows:
        before = (row.tier or "").strip().lower()
        changed = False
        if before != OPS_TIER:
            if not (getattr(row, "pre_ops_tier", None) or "").strip():
                row.pre_ops_tier = (row.tier or "").strip() or None
            row.tier = OPS_TIER
            _apply_companion_tier_cache(row, OPS_TIER)
            changed = True
        if _sync_companion_ops_tag(row, assigned=True):
            changed = True
        if changed:
            updated += 1
    if updated:
        db.flush()
    return {"assigned_companions": len(rows), "updated": updated}


def assign_companion_ops_admin(
    db,
    companion_id: str,
    ops_admin_id: Optional[int],
) -> Optional[int]:
    """互斥分配智能体责任人：写入 users.admin_companion_ids，不写 ucs.ops_admin_id。"""
    cid = _canonical_companion_id(db, companion_id)
    if not cid:
        return None

    if ops_admin_id is not None and not is_admin_account(db, int(ops_admin_id)):
        raise ValueError("not_admin_account")

    target_id = int(ops_admin_id) if ops_admin_id is not None else None

    for admin in _iter_admin_users(db):
        ids = _normalize_admin_companion_ids(getattr(admin, "admin_companion_ids", None))
        if _companion_id_in_list(cid, ids):
            ids = _remove_companion_from_id_list(cid, ids)
            admin.admin_companion_ids = ids

    if target_id is not None:
        target = db.query(UserORM).filter(UserORM.id == target_id).first()
        if not target:
            raise ValueError("admin_not_found")
        ids = _normalize_admin_companion_ids(getattr(target, "admin_companion_ids", None))
        if not _companion_id_in_list(cid, ids):
            ids.append(cid)
        target.admin_companion_ids = ids

    sync_companion_ops_tier(db, cid, assigned=target_id is not None)
    db.flush()

    # 换责：未读迁到新责任人；取消责任：迁到未分配池（超管仍可见），不再直接清光
    try:
        from services.im_ops_unread import (
            UNASSIGNED_OPS_ADMIN_ID,
            migrate_companion_unread_to_ops,
            redis_available,
        )

        if redis_available():
            target = (
                int(target_id)
                if target_id is not None
                else UNASSIGNED_OPS_ADMIN_ID
            )
            migrate_companion_unread_to_ops(cid, target)
    except Exception:
        logger.warning(
            "migrate companion unread on assign ops failed companion=%s",
            cid,
            exc_info=True,
        )

    return resolve_companion_ops_admin(db, cid)


def sync_admin_companion_ids_exclusive(
    db, admin_id: int, new_companion_ids: List[str]
) -> None:
    """admin-users 更新 companion_ids：与 IM assign 同一套互斥规则。"""
    user = db.query(UserORM).filter(UserORM.id == int(admin_id)).first()
    if not user:
        return
    old_ids = _normalize_admin_companion_ids(getattr(user, "admin_companion_ids", None))
    new_ids = _normalize_admin_companion_ids(new_companion_ids)
    old_set = set(old_ids)
    new_set = set(new_ids)

    for cid in new_ids:
        assign_companion_ops_admin(db, cid, int(admin_id))

    for cid in old_set - new_set:
        assign_companion_ops_admin(db, cid, None)

    user.admin_companion_ids = new_ids
    db.flush()


def backfill_legacy_companion_ops_from_ucs(db, *, dry_run: bool = False) -> Dict[str, Any]:
    """一次性：将 ucs.ops_admin_id 历史归属写入 users.admin_companion_ids（仅当前无归属的智能体）。"""
    from sqlalchemy import func

    index = _build_global_companion_ops_index(db)
    assigned: Set[str] = set(index.keys())

    rows = (
        db.query(
            UserCompanionStateORM.companion_id,
            UserCompanionStateORM.ops_admin_id,
            func.count().label("cnt"),
        )
        .filter(UserCompanionStateORM.ops_admin_id.isnot(None))
        .group_by(UserCompanionStateORM.companion_id, UserCompanionStateORM.ops_admin_id)
        .all()
    )
    # companion_id → {ops_admin_id: count}
    by_companion: Dict[str, Dict[int, int]] = {}
    for cid, ops_id, cnt in rows:
        ckey = str(cid or "").strip()
        if not ckey or ops_id is None:
            continue
        by_companion.setdefault(ckey, {})
        by_companion[ckey][int(ops_id)] = int(cnt or 0)

    migrated: List[Dict[str, Any]] = []
    skipped = 0
    for cid, votes in by_companion.items():
        keys = _companion_match_keys(cid)
        if any(k in assigned for k in keys):
            skipped += 1
            continue
        if not votes:
            continue
        ops_admin_id = max(votes.items(), key=lambda x: (x[1], -x[0]))[0]
        if not is_admin_account(db, ops_admin_id):
            skipped += 1
            continue
        if dry_run:
            migrated.append(
                {"companion_id": cid, "ops_admin_id": ops_admin_id, "votes": votes}
            )
            continue
        assign_companion_ops_admin(db, cid, ops_admin_id)
        for k in keys:
            assigned.add(k)
        migrated.append({"companion_id": cid, "ops_admin_id": ops_admin_id, "votes": votes})

    if not dry_run and migrated:
        db.flush()
    return {"migrated": len(migrated), "skipped": skipped, "items": migrated}


def batch_admin_user_brief(
    db, admin_ids: Set[int]
) -> Dict[int, Dict[str, Any]]:
    if not admin_ids:
        return {}
    rows = db.query(UserORM).filter(UserORM.id.in_(list(admin_ids))).all()
    out: Dict[int, Dict[str, Any]] = {}
    for u in rows:
        out[int(u.id)] = {
            "id": u.id,
            "username": u.username or "",
            "nickname": u.nickname or "",
        }
    for aid in admin_ids:
        if int(aid) not in out:
            out[int(aid)] = {"id": int(aid), "username": "", "nickname": ""}
    return out


def can_takeover_session(
    actor: Dict[str, Any],
    ops_admin_id: Optional[int],
    *,
    db=None,
    companion_id: Optional[str] = None,
) -> bool:
    """超管可接管任意会话；普通运营仅当自己是智能体责任运营时可接管。"""
    if db is not None and companion_id:
        ops_admin_id = get_session_ops_admin(db, companion_id)
    if actor.get("is_super"):
        return True
    aid = actor.get("admin_id")
    if aid is None or ops_admin_id is None:
        return False
    return int(aid) == int(ops_admin_id)


def can_assign_ops(actor: Dict[str, Any]) -> bool:
    """仅超级管理员可指定/清空责任运营。"""
    return bool(actor.get("is_super"))


def can_access_session(
    actor: Dict[str, Any],
    ops_admin_id: Optional[int],
    *,
    db=None,
    companion_id: Optional[str] = None,
) -> bool:
    """会话可见/可操作：超管全部；其它角色仅自己责任智能体下会话。"""
    if actor_is_super_admin(actor):
        return True
    if companion_id and not companion_in_scope(actor, companion_id):
        return False
    if db is not None and companion_id:
        ops_admin_id = get_session_ops_admin(db, companion_id)
    aid = actor.get("admin_id")
    if aid is None or ops_admin_id is None:
        return False
    return int(aid) == int(ops_admin_id)


def can_access_companion(
    actor: Dict[str, Any], db, companion_id: Optional[str]
) -> bool:
    return can_access_session(actor, None, db=db, companion_id=companion_id)


def can_release_session(actor: Dict[str, Any], takeover_by: Optional[int]) -> bool:
    """当前接管人或超管可手动释放。"""
    if takeover_by is None:
        return False
    if actor.get("is_super"):
        return True
    aid = actor.get("admin_id")
    if aid is None:
        return False
    return int(aid) == int(takeover_by)


# 接管空闲超时（秒）；可用环境变量覆盖
IM_TAKEOVER_IDLE_TIMEOUT_S = max(
    60,
    int(os.getenv("IM_TAKEOVER_IDLE_TIMEOUT_S", "300") or 300),
)


def _utc_now_naive() -> datetime:
    """DB DateTime 列为无时区 TIMESTAMP：统一存 UTC naive，避免与 aware 比较失效。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def takeover_idle_timeout_seconds() -> int:
    return int(IM_TAKEOVER_IDLE_TIMEOUT_S)


def touch_takeover_activity(user_id: int, companion_id: str) -> bool:
    """刷新接管活跃时间（仅真实操作：接管进入 / 发消息）。未接管返回 False。"""
    cid = (companion_id or "").strip()[:8]
    if not cid or not user_id:
        return False
    now = _utc_now_naive()
    with get_db() as db:
        row = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == int(user_id),
                UserCompanionStateORM.companion_id == cid,
                UserCompanionStateORM.takeover_by.isnot(None),
            )
            .first()
        )
        if not row:
            return False
        row.takeover_active_at = now
        db.flush()
        return True


def idle_remaining_seconds(takeover_active_at: Optional[datetime], takeover_at: Optional[datetime] = None) -> Optional[int]:
    """距空闲超时还剩多少秒；未接管返回 None。"""
    base = takeover_active_at or takeover_at
    if base is None:
        return None
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    else:
        base = base.astimezone(timezone.utc)
    deadline = base.timestamp() + takeover_idle_timeout_seconds()
    return max(0, int(deadline - datetime.now(timezone.utc).timestamp()))


async def release_takeover_session(
    user_id: int,
    companion_id: str,
    *,
    actor: Optional[Dict[str, Any]] = None,
    reason: str = "manual",
) -> Dict[str, Any]:
    """释放接管（手动 / 超时共用）。reason: manual | timeout | force。"""
    cid = (companion_id or "").strip()[:8]
    actor = actor or {
        "admin_id": None,
        "username": "system",
        "is_super": True,
    }
    with get_db() as db:
        row = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == int(user_id),
                UserCompanionStateORM.companion_id == cid,
            )
            .first()
        )
        if not row or row.takeover_by is None:
            return {
                "ok": True,
                "user_id": int(user_id),
                "companion_id": cid,
                "is_taken_over": False,
                "released": False,
                "reason": reason,
            }
        prev_by = int(row.takeover_by)
        row.takeover_by = None
        row.takeover_at = None
        row.takeover_active_at = None
        db.flush()

    write_im_audit(
        action="takeover_timeout" if reason == "timeout" else "takeover_exit",
        actor=actor,
        user_id=int(user_id),
        companion_id=cid,
        detail={"reason": reason, "previous_takeover_by": prev_by},
    )
    await broadcast_admin_im(
        int(user_id),
        cid,
        {
            "type": "takeover_state",
            "is_taken_over": False,
            "takeover_by": None,
            "takeover_at": None,
            "takeover_active_at": None,
            "reason": reason,
        },
    )
    try:
        from services.im_reply_mode import cancel_pending

        cancel_pending(int(user_id), cid)
    except Exception:
        logger.debug("cancel human_first pending after release failed", exc_info=True)
    schedule_catchup_ai_after_takeover_release(int(user_id), cid)
    return {
        "ok": True,
        "user_id": int(user_id),
        "companion_id": cid,
        "is_taken_over": False,
        "released": True,
        "reason": reason,
        "previous_takeover_by": prev_by,
    }


def list_idle_takeovers(limit: int = 100) -> List[Tuple[int, str]]:
    """返回已空闲超时的 (user_id, companion_id)。"""
    from sqlalchemy import and_, or_

    timeout_s = takeover_idle_timeout_seconds()
    # 与列类型一致：UTC naive，避免 TIMESTAMP WITHOUT TIME ZONE 与 aware 比较异常
    cutoff_dt = _utc_now_naive() - timedelta(seconds=timeout_s)
    out: List[Tuple[int, str]] = []
    with get_db() as db:
        rows = (
            db.query(
                UserCompanionStateORM.user_id,
                UserCompanionStateORM.companion_id,
                UserCompanionStateORM.takeover_active_at,
                UserCompanionStateORM.takeover_at,
            )
            .filter(
                UserCompanionStateORM.takeover_by.isnot(None),
                or_(
                    UserCompanionStateORM.takeover_active_at <= cutoff_dt,
                    and_(
                        UserCompanionStateORM.takeover_active_at.is_(None),
                        or_(
                            UserCompanionStateORM.takeover_at <= cutoff_dt,
                            UserCompanionStateORM.takeover_at.is_(None),
                        ),
                    ),
                ),
            )
            .limit(max(1, min(500, int(limit))))
            .all()
        )
        for uid, cid, active_at, taken_at in rows:
            # 二次校验：防止驱动时区导致边界误命中
            rem = idle_remaining_seconds(active_at, taken_at)
            if rem is not None and rem > 0:
                continue
            out.append((int(uid), str(cid)[:8]))
    return out


def ensure_user_companion_state(
    user_id: int,
    companion_id: str,
    *,
    touch_updated_at: bool = True,
) -> None:
    """确保 user_companion_states 存在（触达私信等仅有消息、无会话行时 IM 列表不可见）。"""
    cid = (companion_id or "").strip()[:8]
    if not cid or not user_id:
        return
    now = datetime.now(timezone.utc)
    with get_db() as db:
        row = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == int(user_id),
                UserCompanionStateORM.companion_id == cid,
            )
            .first()
        )
        if row:
            if touch_updated_at:
                row.updated_at = now
            return
        db.add(
            UserCompanionStateORM(
                user_id=int(user_id),
                companion_id=cid,
                affection=0,
                turns=0,
                updated_at=now,
            )
        )


def get_takeover_by(user_id: int, companion_id: str) -> Optional[int]:
    cid = (companion_id or "").strip()[:8]
    if not cid:
        return None
    with get_db() as db:
        row = (
            db.query(UserCompanionStateORM.takeover_by)
            .filter(
                UserCompanionStateORM.user_id == int(user_id),
                UserCompanionStateORM.companion_id == cid,
            )
            .first()
        )
        if not row:
            return None
        return int(row[0]) if row[0] is not None else None


def is_session_taken_over(user_id: int, companion_id: str) -> bool:
    return get_takeover_by(user_id, companion_id) is not None


def _pending_user_text_for_catchup(user_id: int, companion_id: str) -> Optional[str]:
    """最近一条 STM 为 user（其后无 assistant/operator）时返回其正文，否则 None。"""
    from services.memory import ShortTermMemory

    cid = (companion_id or "").strip()[:8]
    if not cid:
        return None
    recent = ShortTermMemory(cid, user_id=int(user_id)).get_recent(1)
    if not recent:
        return None
    last = recent[-1] or {}
    if (last.get("role") or "").strip() != "user":
        return None
    from services.memory import is_unpaid_message

    if is_unpaid_message(last):
        return None
    text = (last.get("content") or "").strip()
    return text or None


async def catchup_ai_after_takeover_release(user_id: int, companion_id: str) -> None:
    """释放接管后：若用户有未回复消息，补一轮 AI（不二次扣费、不重复写用户消息）。"""
    cid = (companion_id or "").strip()[:8]
    uid = int(user_id)
    if not cid:
        return

    from core.concurrency import RedisTurnLock, get_user_lock
    from core.executor import AGENT_POOL
    from core.state import get_companion_manager, get_session
    from services.agent_runner import run_agent
    from services.chat_turn import persist_agent_result, prepare_agent_job
    from services.prompts.resolve import resolve_lang

    lock = await get_user_lock(uid, cid)
    acquired = False
    try:
        if isinstance(lock, RedisTurnLock):
            acquired = await lock.acquire(timeout=8.0)
        else:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=8.0)
                acquired = True
            except asyncio.TimeoutError:
                acquired = False
        if not acquired:
            logger.info(
                "takeover catchup skipped (lock busy) user=%s companion=%s",
                uid,
                cid,
            )
            return

        if is_session_taken_over(uid, cid):
            return

        pending = _pending_user_text_for_catchup(uid, cid)
        if not pending:
            return

        cm = get_companion_manager()
        companion = cm.get(cid, include_deleted=True) if cm else None
        if not companion or bool(getattr(companion.profile, "is_deleted", False)):
            return
        if cm.is_soft_deleted(cid):
            return

        session_meta = get_session(cid, user_id=uid) or {}
        language = resolve_lang(
            body_lang=None,
            accept_language=None,
            session_lang=session_meta.get("lang")
            or getattr(companion.profile, "language", None)
            or "zh",
        )
        try:
            from services.quick_reply import persist_picked_quick_reply, pick_quick_reply
            from api.companion_ws import try_push_assistant_message

            picked = pick_quick_reply(
                companion_id=cid,
                language=language,
                user_text=pending,
                user_id=uid,
            )
            content = ((picked or {}).get("content") or "").strip()
            if content:
                persist_picked_quick_reply(
                    companion,
                    user_id=uid,
                    companion_id=cid,
                    user_text=pending,
                    picked=picked,
                )
                try_push_assistant_message(uid, cid, content)
                await broadcast_admin_im(
                    uid,
                    cid,
                    {
                        "type": "message",
                        "role": "assistant",
                        "text": content,
                        "quick_reply": True,
                        "quick_reply_id": picked.get("id"),
                        "source": "takeover_catchup",
                    },
                )
                logger.info(
                    "takeover catchup quick_reply user=%s companion=%s id=%s",
                    uid,
                    cid,
                    picked.get("id"),
                )
                return
        except Exception:
            logger.exception(
                "takeover catchup quick_reply failed user=%s companion=%s",
                uid,
                cid,
            )
        user_gender = ""
        try:
            with get_db() as db:
                u = db.query(UserORM).filter(UserORM.id == uid).first()
                if u:
                    user_gender = (getattr(u, "gender", None) or "") or ""
        except Exception:
            pass

        job = await prepare_agent_job(
            companion=companion,
            user_id=uid,
            text=pending,
            language=language,
            user_gender=user_gender,
            tz=(session_meta.get("client_tz") or "") or "",
            tz_offset=session_meta.get("client_tz_offset")
            if isinstance(session_meta.get("client_tz_offset"), int)
            else None,
            connection_id="",
            source="im_takeover_catchup",
            append_user_message=False,
        )
        job.connection_id = f"im_catchup:{job.job_id}"

        # 持锁期间再确认一次：避免与用户新消息/运营重接管竞态
        if is_session_taken_over(uid, cid):
            return
        if _pending_user_text_for_catchup(uid, cid) != pending:
            return

        loop = asyncio.get_running_loop()

        def _idle_seconds(j) -> Optional[float]:
            if j.idle_seconds is None or j.idle_seconds < 0:
                return None
            return float(j.idle_seconds)

        result = await loop.run_in_executor(
            AGENT_POOL,
            functools.partial(
                run_agent,
                job.user_text_for_agent,
                job.profile,
                job.companion_state,
                job.memory_text,
                "",
                job.language,
                job.current_time,
                job.user_gender,
                job.summary_due,
                job.extract_due,
                job.has_leave_intent,
                job.burst_count,
                job.open_threads,
                job.deny_hooks,
                job.recent_assistant,
                getattr(job, "recent_user", None) or [],
                _idle_seconds(job),
                job.last_extreme_ts,
                getattr(job, "reply_lang_override", None) or "",
                job.user_id,
            ),
        )

        if is_session_taken_over(uid, cid):
            return
        if _pending_user_text_for_catchup(uid, cid) != pending:
            return

        public = persist_agent_result(companion, job, result or {})
        bubbles = list(public.get("bubbles") or [])
        if not bubbles:
            text = (public.get("text") or "").strip()
            if text:
                bubbles = [text]

        from api.companion_ws import try_push_assistant_message

        for bubble in bubbles:
            try_push_assistant_message(uid, cid, bubble)
            await broadcast_admin_im(
                uid,
                cid,
                {
                    "type": "message",
                    "role": "assistant",
                    "text": bubble,
                    "source": "takeover_catchup",
                },
            )

        write_im_audit(
            action="takeover_catchup",
            actor={"admin_id": None, "username": "system"},
            user_id=uid,
            companion_id=cid,
            detail={"bubbles": len(bubbles), "pending_len": len(pending)},
        )
        logger.info(
            "takeover catchup done user=%s companion=%s bubbles=%s",
            uid,
            cid,
            len(bubbles),
        )
    except Exception:
        logger.exception(
            "takeover catchup failed user=%s companion=%s",
            uid,
            cid,
        )
    finally:
        if acquired:
            try:
                lock.release()
            except Exception:
                pass


def schedule_catchup_ai_after_takeover_release(user_id: int, companion_id: str) -> None:
    """HTTP 释放后异步补答；失败不影响 release 响应。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "takeover catchup not scheduled (no loop) user=%s companion=%s",
            user_id,
            companion_id,
        )
        return
    cid = (companion_id or "").strip()[:8]
    loop.create_task(
        catchup_ai_after_takeover_release(int(user_id), cid),
        name=f"im-takeover-catchup-{user_id}-{cid}",
    )


async def register_admin_im_ws(user_id: int, companion_id: str, ws: WebSocket) -> None:
    key = session_key(user_id, companion_id)
    async with _admin_im_lock:
        _admin_im_ws.setdefault(key, set()).add(ws)


async def unregister_admin_im_ws(user_id: int, companion_id: str, ws: WebSocket) -> None:
    key = session_key(user_id, companion_id)
    async with _admin_im_lock:
        s = _admin_im_ws.get(key)
        if not s:
            return
        s.discard(ws)
        if not s:
            _admin_im_ws.pop(key, None)


async def broadcast_admin_im_local(
    user_id: int,
    companion_id: str,
    payload: dict,
) -> int:
    """仅向本进程内运营 WS 推送；供本机发送与 Redis 桥跨 worker 投递。"""
    key = session_key(user_id, companion_id)
    async with _admin_im_lock:
        targets = list(_admin_im_ws.get(key) or ())
    if not targets:
        return 0
    dead: List[WebSocket] = []
    ok_n = 0
    raw = json.dumps(payload, ensure_ascii=False)
    for ws in targets:
        try:
            if ws.client_state != WebSocketState.CONNECTED:
                dead.append(ws)
                continue
            await ws.send_text(raw)
            ok_n += 1
        except Exception:
            dead.append(ws)
    if dead:
        async with _admin_im_lock:
            s = _admin_im_ws.get(key)
            if s:
                for ws in dead:
                    s.discard(ws)
                if not s:
                    _admin_im_ws.pop(key, None)
    return ok_n


async def broadcast_admin_im(
    user_id: int,
    companion_id: str,
    payload: dict,
) -> int:
    """向运营 WS 推送：本机投递 + Redis 广播到其它 worker。返回本机成功数。"""
    ok_n = await broadcast_admin_im_local(user_id, companion_id, payload)
    try:
        from services.im_ws_bridge import publish_admin_im

        await publish_admin_im(user_id, companion_id, payload)
    except Exception:
        logger.exception(
            "admin IM redis bridge publish failed user=%s companion=%s",
            user_id,
            companion_id,
        )
    return ok_n


async def register_admin_im_inbox(admin_id: int, ws: WebSocket) -> None:
    aid = int(admin_id)
    async with _admin_im_inbox_lock:
        _admin_im_inbox.setdefault(aid, set()).add(ws)


async def unregister_admin_im_inbox(admin_id: int, ws: WebSocket) -> None:
    aid = int(admin_id)
    async with _admin_im_inbox_lock:
        s = _admin_im_inbox.get(aid)
        if not s:
            return
        s.discard(ws)
        if not s:
            _admin_im_inbox.pop(aid, None)


async def broadcast_admin_im_inbox_local(admin_id: int, payload: dict) -> int:
    """仅向本进程内某运营的 inbox WS 推送。"""
    aid = int(admin_id)
    async with _admin_im_inbox_lock:
        targets = list(_admin_im_inbox.get(aid) or ())
    if not targets:
        return 0
    dead: List[WebSocket] = []
    ok_n = 0
    raw = json.dumps(payload, ensure_ascii=False)
    for ws in targets:
        try:
            if ws.client_state != WebSocketState.CONNECTED:
                dead.append(ws)
                continue
            await ws.send_text(raw)
            ok_n += 1
        except Exception:
            dead.append(ws)
    if dead:
        async with _admin_im_inbox_lock:
            s = _admin_im_inbox.get(aid)
            if s:
                for ws in dead:
                    s.discard(ws)
                if not s:
                    _admin_im_inbox.pop(aid, None)
    return ok_n


async def broadcast_admin_im_inbox(admin_id: int, payload: dict) -> int:
    """向指定运营 inbox 推送：本机 + Redis。"""
    ok_n = await broadcast_admin_im_inbox_local(int(admin_id), payload)
    try:
        from services.im_ws_bridge import publish_admin_im_inbox

        await publish_admin_im_inbox(int(admin_id), payload)
    except Exception:
        logger.exception(
            "admin IM inbox redis bridge publish failed admin_id=%s",
            admin_id,
        )
    return ok_n


def bump_ops_unread(
    user_id: int,
    companion_id: str,
    *,
    delta: int = 1,
) -> Optional[Dict[str, Any]]:
    """用户新消息：Redis 未读 +delta。

    有责任运营挂到该运营；无责任运营挂到未分配池（ops=0），超管 summary 可见。
    Redis 不可用返回 None。
    """
    from services.im_ops_unread import (
        UNASSIGNED_OPS_ADMIN_ID,
        incr_session_unread,
        redis_available,
    )

    cid = (companion_id or "").strip()[:8]
    if not cid or not user_id:
        return None
    delta = max(1, int(delta or 1))
    with get_db() as db:
        row = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == int(user_id),
                UserCompanionStateORM.companion_id == cid,
            )
            .first()
        )
        taken_over = row.takeover_by is not None if row else False
        resolved = get_session_ops_admin(db, cid)
        ops_admin_id = (
            int(resolved) if resolved is not None else UNASSIGNED_OPS_ADMIN_ID
        )
        if row:
            try:
                row.updated_at = datetime.now(timezone.utc)
            except Exception:
                pass

    if not redis_available():
        logger.warning(
            "im unread bump skipped: redis unavailable user=%s companion=%s",
            user_id,
            cid,
        )
        return None

    cur = incr_session_unread(ops_admin_id, cid, int(user_id), delta=delta)
    if cur is None:
        return None
    return {
        "user_id": int(user_id),
        "companion_id": cid,
        "ops_admin_id": ops_admin_id,
        "unassigned": ops_admin_id == UNASSIGNED_OPS_ADMIN_ID,
        "ops_unread_count": int(cur),
        "taken_over": taken_over,
    }


def clear_ops_unread(user_id: int, companion_id: str) -> Optional[Dict[str, Any]]:
    """打开会话：Redis 未读清零（清所有 ops 下该会话，避免换责残留）。"""
    from services.im_ops_unread import clear_session_unread_for_all_ops, redis_available

    cid = (companion_id or "").strip()[:8]
    if not cid or not user_id:
        return None
    now = datetime.now(timezone.utc)
    with get_db() as db:
        ops_admin_id = get_session_ops_admin(db, cid)

    cleared_from = 0
    if redis_available():
        cleared_from = clear_session_unread_for_all_ops(cid, int(user_id))

    return {
        "user_id": int(user_id),
        "companion_id": cid,
        "ops_admin_id": ops_admin_id,
        "ops_unread_count": 0,
        "ops_last_read_at": serialize_datetime(now),
        "cleared_from": int(cleared_from),
    }


def list_super_admin_ids(db) -> List[int]:
    """库内 role=admin 的超管 user id（用于未分配未读 inbox 广播）。"""
    out: List[int] = []
    for u in _iter_admin_users(db):
        if (u.role or "").strip().lower() == "admin":
            out.append(int(u.id))
    return out


async def notify_ops_new_user_message(
    user_id: int,
    companion_id: str,
    text: str,
    *,
    delta: int = 1,
) -> Optional[Dict[str, Any]]:
    """落库用户消息后：累加未读并推 inbox（user_message_alert）。"""
    from services.im_ops_unread import (
        UNASSIGNED_OPS_ADMIN_ID,
        recompute_ops_unread,
    )

    info = bump_ops_unread(user_id, companion_id, delta=delta)
    if not info:
        return None
    preview = (text or "").strip().replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:120]
    total_unread, _sc = recompute_ops_unread(int(info["ops_admin_id"]))[:2]
    payload = {
        "type": "user_message_alert",
        "user_id": info["user_id"],
        "companion_id": info["companion_id"],
        "ops_admin_id": info["ops_admin_id"],
        "unassigned": bool(info.get("unassigned")),
        "ops_unread_count": info["ops_unread_count"],
        "ops_total_unread": int(total_unread),
        "preview": preview,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "taken_over": bool(info.get("taken_over")),
    }
    aid = int(info["ops_admin_id"])
    if aid == UNASSIGNED_OPS_ADMIN_ID:
        # 未分配：推给所有超管 Tab（若已连 inbox）
        with get_db() as db:
            super_ids = list_super_admin_ids(db)
        for sid in super_ids:
            await broadcast_admin_im_inbox(int(sid), payload)
    else:
        await broadcast_admin_im_inbox(aid, payload)
    return info


async def notify_ops_unread_cleared(
    user_id: int,
    companion_id: str,
    *,
    ops_admin_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """清未读后通知该责任运营其它 Tab（仅原先有未读时推送）。"""
    from services.im_ops_unread import (
        UNASSIGNED_OPS_ADMIN_ID,
        recompute_ops_unread,
        sync_ops_aggregate_keys,
    )

    info = clear_ops_unread(user_id, companion_id)
    if not info:
        return None
    if int(info.get("cleared_from") or 0) <= 0:
        return info
    aid = ops_admin_id if ops_admin_id is not None else info.get("ops_admin_id")
    if aid is None:
        aid = UNASSIGNED_OPS_ADMIN_ID
    total_unread, _sc, by_c = recompute_ops_unread(int(aid))
    sync_ops_aggregate_keys(int(aid), total=total_unread, session_count=_sc, by_companion=by_c)
    payload = {
        "type": "unread_cleared",
        "user_id": info["user_id"],
        "companion_id": info["companion_id"],
        "ops_admin_id": int(aid),
        "unassigned": int(aid) == UNASSIGNED_OPS_ADMIN_ID,
        "ops_unread_count": 0,
        "ops_total_unread": int(total_unread),
        "ops_last_read_at": info.get("ops_last_read_at"),
    }
    if int(aid) == UNASSIGNED_OPS_ADMIN_ID:
        with get_db() as db:
            super_ids = list_super_admin_ids(db)
        for sid in super_ids:
            await broadcast_admin_im_inbox(int(sid), payload)
    else:
        await broadcast_admin_im_inbox(int(aid), payload)
    return info


def ops_unread_summary_for_actor(actor: Dict[str, Any]) -> Dict[str, Any]:
    """当前可见范围内未读汇总（Redis）：总数 + 按智能体。

    汇总前先自愈：清掉无 UCS / 错 ops / 无用户消息的幽灵未读。
    未分配池（ops=0）仅超管可见；普通运营只看自己名下。
    """
    from services.im_ops_unread import build_unread_summary

    try:
        reconcile_stale_ops_unread()
    except Exception:
        logger.warning("im unread reconcile before summary failed", exc_info=True)

    return build_unread_summary(
        ops_admin_id=actor.get("admin_id"),
        is_super=bool(actor.get("is_super")),
    )


def reconcile_stale_ops_unread() -> Dict[str, int]:
    """清除/纠正不可运营处理的幽灵未读。

    - 无 UCS / 无用户消息：清除
    - Redis ops 与当前责任人不一致：迁到正确桶（有责→该 ops；无责→未分配池 0）
    """
    from services.im_ops_unread import (
        UNASSIGNED_OPS_ADMIN_ID,
        clear_session_unread,
        incr_session_unread,
        iter_unread_session_entries,
        redis_available,
    )

    if not redis_available():
        return {"cleared_entries": 0, "cleared_unread": 0, "migrated_entries": 0}

    entries = iter_unread_session_entries()
    if not entries:
        return {"cleared_entries": 0, "cleared_unread": 0, "migrated_entries": 0}

    cleared_entries = 0
    cleared_unread = 0
    migrated_entries = 0
    with get_db() as db:
        pairs = {(int(e["user_id"]), str(e["companion_id"])[:8]) for e in entries}
        ucs_ok: set[Tuple[int, str]] = set()
        if pairs:
            user_ids = list({p[0] for p in pairs})
            rows = (
                db.query(
                    UserCompanionStateORM.user_id,
                    UserCompanionStateORM.companion_id,
                )
                .filter(UserCompanionStateORM.user_id.in_(user_ids))
                .all()
            )
            for uid, cid in rows:
                key = (int(uid), str(cid)[:8])
                if key in pairs:
                    ucs_ok.add(key)

        has_user_msg: set[Tuple[int, str]] = set()
        if pairs:
            user_ids = list({p[0] for p in pairs})
            msg_rows = (
                db.query(
                    ShortTermMessageORM.user_id,
                    ShortTermMessageORM.companion_id,
                )
                .filter(
                    ShortTermMessageORM.user_id.in_(user_ids),
                    ShortTermMessageORM.role == "user",
                )
                .distinct()
                .all()
            )
            for uid, cid in msg_rows:
                key = (int(uid), str(cid)[:8])
                if key in pairs:
                    has_user_msg.add(key)

        cids = list({str(e["companion_id"])[:8] for e in entries})
        ops_map = build_companion_ops_map(db, cids)

        for e in entries:
            uid = int(e["user_id"])
            cid = str(e["companion_id"])[:8]
            ops = int(e["ops_admin_id"])
            cnt = int(e["count"])
            current = lookup_companion_ops_map(ops_map, cid)
            expected = (
                int(current)
                if current is not None
                else UNASSIGNED_OPS_ADMIN_ID
            )

            if (uid, cid) not in ucs_ok or (uid, cid) not in has_user_msg:
                prev = clear_session_unread(ops, cid, uid)
                if prev > 0:
                    cleared_entries += 1
                    cleared_unread += prev
                    logger.info(
                        "im unread stale cleared ops=%s companion=%s user=%s prev=%s "
                        "ucs=%s has_user_msg=%s",
                        ops,
                        cid,
                        uid,
                        prev,
                        (uid, cid) in ucs_ok,
                        (uid, cid) in has_user_msg,
                    )
                continue

            if ops == expected:
                continue

            # 错桶：迁到 expected
            prev = clear_session_unread(ops, cid, uid)
            if prev <= 0:
                continue
            incr_session_unread(expected, cid, uid, delta=prev)
            migrated_entries += 1
            logger.info(
                "im unread migrated companion=%s user=%s count=%s from_ops=%s to_ops=%s",
                cid,
                uid,
                prev,
                ops,
                expected,
            )

    return {
        "cleared_entries": cleared_entries,
        "cleared_unread": cleared_unread,
        "migrated_entries": migrated_entries,
    }


def write_im_audit(
    *,
    action: str,
    actor: Dict[str, Any],
    user_id: Optional[int] = None,
    companion_id: Optional[str] = None,
    detail: Any = None,
) -> None:
    try:
        detail_s = ""
        if detail is None:
            detail_s = ""
        elif isinstance(detail, str):
            detail_s = detail[:2000]
        else:
            detail_s = json.dumps(detail, ensure_ascii=False)[:2000]
        with get_db() as db:
            db.add(
                AdminImAuditLogORM(
                    admin_id=actor.get("admin_id"),
                    admin_username=(actor.get("username") or "")[:64],
                    action=(action or "")[:40],
                    user_id=user_id,
                    companion_id=(companion_id or None) and str(companion_id)[:8],
                    detail=detail_s,
                )
            )
    except Exception:
        logger.exception("write_im_audit failed action=%s", action)


def admin_user_brief(db, admin_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if admin_id is None:
        return None
    u = db.query(UserORM).filter(UserORM.id == int(admin_id)).first()
    if not u:
        return {"id": int(admin_id), "username": "", "nickname": ""}
    return {
        "id": u.id,
        "username": u.username or "",
        "nickname": u.nickname or "",
    }


def persist_operator_message(
    *,
    user_id: int,
    companion_id: str,
    content: str,
    admin_id: Optional[int],
) -> Dict[str, Any]:
    """落库运营消息（role=operator），并写入短期记忆桥。"""
    from services.memory import ShortTermMemory

    text = (content or "").strip()
    cid = (companion_id or "").strip()[:8]
    ShortTermMemory(cid, user_id=int(user_id)).add("operator", text)

    msg_id = None
    created_at = datetime.now(timezone.utc)
    with get_db() as db:
        row = (
            db.query(ShortTermMessageORM)
            .filter(
                ShortTermMessageORM.companion_id == cid,
                ShortTermMessageORM.user_id == int(user_id),
                ShortTermMessageORM.role == "operator",
                ShortTermMessageORM.content == text,
            )
            .order_by(ShortTermMessageORM.id.desc())
            .first()
        )
        if row:
            if admin_id is not None:
                row.admin_id = admin_id
            msg_id = row.id
            created_at = row.created_at or created_at
        ucs = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == int(user_id),
                UserCompanionStateORM.companion_id == cid,
            )
            .first()
        )
        if ucs:
            ucs.updated_at = datetime.now(timezone.utc)

    try:
        from services.im_reply_mode import cancel_pending

        cancel_pending(int(user_id), cid)
    except Exception:
        logger.debug("cancel human_first pending after operator message failed", exc_info=True)

    return {
        "id": msg_id,
        "role": "operator",
        "content": text,
        "admin_id": admin_id,
        "created_at": serialize_datetime(created_at),
    }


def push_operator_to_user_app(user_id: int, companion_id: str, text: str) -> bool:
    """推给 App：外观与 AI 一致（assistant），用户无感。

    True 表示本机已推到连接，或 Redis 标记该会话仍在线（其它 worker 可能已收到）。
    """
    from api.companion_ws import try_push_assistant_message

    return try_push_assistant_message(int(user_id), str(companion_id)[:8], text)


def companion_profile_payload(db, companion_id: str) -> Optional[Dict[str, Any]]:
    c = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
    if not c:
        return None
    return {
        "id": c.id,
        "name": c.name or "",
        "gender": getattr(c, "gender", None) or "",
        "age": getattr(c, "age", None),
        "mbti": getattr(c, "mbti", None) or "",
        "background": getattr(c, "background", None) or "",
        "sexual_orientation": getattr(c, "sexual_orientation", None) or "",
        "city": getattr(c, "city", None) or "",
        "country": getattr(c, "country", None) or "",
        "speech_style": getattr(c, "speech_style", None) or "",
        "avatar_url": getattr(c, "avatar_url", None) or "",
        "description": getattr(c, "description", None) or "",
        "language": getattr(c, "language", None) or "",
        "region_label": getattr(c, "region_label", None) or "",
        "tier": getattr(c, "tier", None) or "",
        "is_active": bool(getattr(c, "is_active", True)),
        "created_by": getattr(c, "created_by", None) or "",
    }
