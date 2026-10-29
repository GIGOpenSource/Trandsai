"""存量：已分配责任人的智能体 → tier=ops 且 tags 含 ops"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(r"d:\room\nomal_project\trandsai\.env")

from sqlalchemy.orm.attributes import flag_modified

from core.database import CompanionORM, UserORM, get_db

DRY = "--dry-run" in sys.argv
OPS = "ops"


def norm(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("["):
            try:
                return norm(json.loads(s))
            except Exception:
                pass
        return [p for p in s.replace("，", ",").split(",") if p.strip()]
    if isinstance(raw, dict):
        cid = str(raw.get("id") or "").strip()
        return [cid] if cid else []
    if isinstance(raw, (list, tuple, set)):
        out = []
        for x in raw:
            if isinstance(x, dict):
                s = str(x.get("id") or "").strip()
            else:
                s = str(x).strip()
            if s and s not in out:
                out.append(s)
        return out
    return []


with get_db() as db:
    assigned = set()
    for admin in db.query(UserORM).filter(UserORM.role.isnot(None), UserORM.role != "").all():
        if (admin.role or "").strip().lower() in ("", "user"):
            continue
        for cid in norm(getattr(admin, "admin_companion_ids", None)):
            assigned.add(cid)
            assigned.add(cid[:8])

    rows = db.query(CompanionORM).filter(CompanionORM.id.in_(list(assigned))).all() if assigned else []
    n_tier = n_tag = 0
    for r in rows:
        tags = [str(t).strip() for t in (r.tags or []) if str(t).strip()]
        if (r.tier or "").strip().lower() != OPS:
            if not DRY:
                if not (getattr(r, "pre_ops_tier", None) or "").strip():
                    r.pre_ops_tier = (r.tier or "").strip() or None
                r.tier = OPS
            n_tier += 1
        if OPS not in tags:
            if not DRY:
                tags.append(OPS)
                r.tags = tags
                flag_modified(r, "tags")
            n_tag += 1
            print(f"  +tag ops -> {r.id}")
    if not DRY and (n_tier or n_tag):
        db.commit()
    print(f"matched={len(rows)} tier_updated={n_tier} tag_updated={n_tag} dry={DRY}")
