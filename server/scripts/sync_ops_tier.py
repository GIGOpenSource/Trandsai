"""存量：已分配责任人的智能体 → companions.tier = ops"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(r"d:\room\nomal_project\trandsai\.env")

from core.database import CompanionORM, UserORM, get_db

DRY = "--dry-run" in sys.argv
OPS_TIER = "ops"


def norm(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("[") or s.startswith("{"):
            try:
                return norm(json.loads(s))
            except Exception:
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


with get_db() as db:
    assigned = set()
    admins = db.query(UserORM).filter(UserORM.role.isnot(None), UserORM.role != "").all()
    for admin in admins:
        if (admin.role or "").strip().lower() in ("", "user"):
            continue
        for cid in norm(getattr(admin, "admin_companion_ids", None)):
            assigned.add(cid)
            assigned.add(cid[:8])

    rows = db.query(CompanionORM).filter(CompanionORM.id.in_(list(assigned))).all() if assigned else []
    to_update = [r for r in rows if (r.tier or "").strip().lower() != OPS_TIER]
    print("assigned companions matched:", len(rows))
    print("need tier=ops update:", len(to_update))
    for r in to_update[:30]:
        print(f"  {r.id} tier={r.tier!r} -> ops")
    if len(to_update) > 30:
        print(f"  ... and {len(to_update) - 30} more")

    if not DRY:
        for r in to_update:
            r.tier = OPS_TIER
        db.commit()
        print("committed")
    else:
        print("dry_run=True, no write")
