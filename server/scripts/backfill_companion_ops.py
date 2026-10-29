"""一次性回填：ucs.ops_admin_id → users.admin_companion_ids（standalone，无需 starlette）"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(r"d:\room\nomal_project\trandsai\.env")

from sqlalchemy import func

from core.database import CompanionORM, UserCompanionStateORM, UserORM, get_db

DRY = "--dry-run" in sys.argv


def norm(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("[") or s.startswith("{"):
            try:
                return norm(json.loads(s))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        return [p for p in s.replace("，", ",").split(",") if p.strip()]
    if isinstance(raw, dict):
        cid = str(raw.get("id") or raw.get("companion_id") or "").strip()
        return [cid] if cid else []
    if isinstance(raw, (list, tuple, set)):
        out = []
        for x in raw:
            if isinstance(x, dict):
                s = str(x.get("id") or x.get("companion_id") or "").strip()
            else:
                s = str(x).strip()
            if s and s not in out:
                out.append(s)
        return out
    return []


def match_keys(cid):
    cid = str(cid or "").strip()
    if not cid:
        return set()
    return {k for k in {cid, cid[:8]} if k}


def is_admin(db, uid):
    u = db.query(UserORM).filter(UserORM.id == int(uid)).first()
    if not u:
        return False
    role = (u.role or "").strip().lower()
    return bool(role) and role != "user"


def build_index(db):
    index = {}
    rows = db.query(UserORM).filter(UserORM.role.isnot(None), UserORM.role != "").all()
    for admin in rows:
        if (admin.role or "").strip().lower() in ("", "user"):
            continue
        for cid in norm(getattr(admin, "admin_companion_ids", None)):
            for key in match_keys(cid):
                prev = index.get(key)
                if prev is None or int(admin.id) < int(prev):
                    index[key] = int(admin.id)
    return index


def assign(db, companion_id, ops_admin_id, index):
    cid = str(companion_id or "").strip()
    if not cid:
        return
    for admin in db.query(UserORM).filter(UserORM.role.isnot(None), UserORM.role != "").all():
        if (admin.role or "").strip().lower() in ("", "user"):
            continue
        ids = norm(getattr(admin, "admin_companion_ids", None))
        new_ids = []
        keys = match_keys(cid)
        for s in ids:
            if s in keys or s[:8] in keys:
                continue
            new_ids.append(s)
        if new_ids != ids:
            admin.admin_companion_ids = new_ids
    if ops_admin_id is not None:
        target = db.query(UserORM).filter(UserORM.id == int(ops_admin_id)).first()
        if target:
            ids = norm(getattr(target, "admin_companion_ids", None))
            if not any(s in match_keys(cid) or s[:8] in match_keys(cid) for s in ids):
                ids.append(cid)
            target.admin_companion_ids = ids
            for k in match_keys(cid):
                index[k] = int(ops_admin_id)


with get_db() as db:
    index = build_index(db)
    assigned = set(index.keys())

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
    by_companion = {}
    for cid, ops_id, cnt in rows:
        ckey = str(cid or "").strip()
        if not ckey or ops_id is None:
            continue
        by_companion.setdefault(ckey, {})
        by_companion[ckey][int(ops_id)] = int(cnt or 0)

    migrated = []
    skipped = 0
    for cid, votes in by_companion.items():
        if any(k in assigned for k in match_keys(cid)):
            skipped += 1
            continue
        ops_admin_id = max(votes.items(), key=lambda x: (x[1], -x[0]))[0]
        if not is_admin(db, ops_admin_id):
            skipped += 1
            continue
        if DRY:
            migrated.append({"companion_id": cid, "ops_admin_id": ops_admin_id, "votes": votes})
            continue
        assign(db, cid, ops_admin_id, index)
        for k in match_keys(cid):
            assigned.add(k)
        migrated.append({"companion_id": cid, "ops_admin_id": ops_admin_id, "votes": votes})

    if not DRY and migrated:
        db.commit()

    print("dry_run=", DRY)
    print("migrated=", len(migrated), "skipped=", skipped)
    for item in migrated[:20]:
        print(" ", item)

    index2 = build_index(db)
    for cid in ["38322557", "250c1cee", "c8373e09", "7c074ec0"]:
        best = None
        for k in match_keys(cid):
            aid = index2.get(k)
            if aid is not None and (best is None or aid < best):
                best = aid
        print(f"resolve {cid} -> {best}")
