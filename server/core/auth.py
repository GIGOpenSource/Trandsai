import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.redis_client import get_redis_client

TOKEN_EXPIRE_HOURS = 7 * 24


def generate_token(user_id: int) -> str:
    """Generate token and store in Redis (legacy helper; prefer api.auth.create_user_token)."""
    salt = secrets.token_hex(16)
    raw = f"{user_id}:{datetime.now(timezone.utc).isoformat()}:{salt}"
    token = hashlib.md5(raw.encode()).hexdigest()
    r = get_redis_client()
    if r:
        r.setex(f"token:{token}", TOKEN_EXPIRE_HOURS * 3600, str(user_id))
    return token


def verify_token(token: str) -> Optional[int]:
    if not token:
        return None
    r = get_redis_client()
    if not r:
        return None
    try:
        user_id_str = r.get(f"token:{token}")
        return int(user_id_str) if user_id_str else None
    except Exception:
        return None


def delete_token(token: str) -> bool:
    if not token:
        return False
    r = get_redis_client()
    if not r:
        return False
    try:
        return r.delete(f"token:{token}") > 0
    except Exception:
        return False
