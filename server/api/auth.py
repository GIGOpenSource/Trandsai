import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from core.database import AdminTokenORM, CompanionORM, CompanionStateORM, UserCompanionStateORM, UserORM, get_db
from core.auth import generate_token, delete_token, verify_token as redis_verify_token
router = APIRouter()

_PBKDF2_ROUNDS = 200_000
_PASSWORD_HASH_PREFIX = "pbkdf2_sha256"


def _get_admin_password() -> str:
    password = (os.getenv("ADMIN_PASSWORD") or "").strip()
    if not password or password == "admin123":
        raise ValueError("ADMIN_PASSWORD is missing or using an insecure default value")
    return password


def _hash_token(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


# 兼容旧版（历史数据已存储为截断 sha256）
def _legacy_password_hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()[:32]


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ROUNDS
    ).hex()
    return f"{_PASSWORD_HASH_PREFIX}${_PBKDF2_ROUNDS}${salt}${digest}"


def _verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False

    parts = stored_hash.split("$")
    if len(parts) == 4 and parts[0] == _PASSWORD_HASH_PREFIX:
        _, rounds_text, salt, expected = parts
        try:
            rounds = int(rounds_text)
        except ValueError:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds
        ).hex()
        return secrets.compare_digest(actual, expected)

    # 兼容旧 hash
    return secrets.compare_digest(_legacy_password_hash(password), stored_hash)


# ===== 管理员 Token（数据库持久化存储，24小时有效）=====
# 保留此变量供外部清空所有 token 时使用（如修改密码后）
_admin_token_store: dict[str, dict] = {}


def create_token(password: str) -> Optional[str]:
    """验证密码并生成 24 小时有效 Token，持久化到数据库"""
    if password != _get_admin_password():
        return None
    token = _hash_token(f"{password}{time.time()}")
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    with get_db() as db:
        db.add(AdminTokenORM(token=token, expire_at=expire))
    # 同时写入内存缓存，避免同一进程内重复查库
    _admin_token_store[token] = {"expire": expire.timestamp()}
    return token


def verify_token(token: str) -> bool:
    """校验管理员 Token 是否有效（查库并清理过期 token）"""
    now = datetime.now(timezone.utc)
    with get_db() as db:
        # 先清理所有过期 token
        expired = db.query(AdminTokenORM).filter(AdminTokenORM.expire_at < now).all()
        for row in expired:
            db.delete(row)
            _admin_token_store.pop(row.token, None)
        # 查询当前 token
        row = db.query(AdminTokenORM).filter(AdminTokenORM.token == token).first()
        if not row:
            return False
        return True


# ===== 用户 Token（数据库存储，7天有效，服务器重启不丢失）=====

def create_user_token(user_id: int) -> str:
    """为用户生成 7 天有效 Token，并持久化到数据库"""
    token = _hash_token(f"user_{user_id}_{time.time()}")
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    from core.database import get_db, UserORM
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if user:
            user.token = token
            user.token_expire = expire
            db.commit()
    return token


def verify_user_token(token: str) -> Optional[int]:
    """校验用户 Token，返回 user_id 或 None"""
    if not token:
        return None
    from core.database import get_db, UserORM
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.token == token).first()
        if not user or not user.token_expire:
            return None
        now = datetime.now(timezone.utc)
        expire = user.token_expire
        if expire.tzinfo is None:
            expire = expire.replace(tzinfo=timezone.utc)
        if now > expire:
            # Token 已过期，清空
            user.token = ""
            user.token_expire = None
            db.commit()
            return None
        return user.id


@router.post("/api/auth/register")
async def user_register(data: dict):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    nickname = (data.get("nickname") or "").strip()
    gender = data.get("gender") or ""
    sexual_orientation = data.get("sexual_orientation") or ""

    if not username or len(username) < 3:
        raise HTTPException(status_code=400, detail="用户名至少3个字符")
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少6个字符")

    with get_db() as db:
        existing = db.query(UserORM).filter(UserORM.username == username).first()
        if existing:
            raise HTTPException(status_code=400, detail="用户名已存在")

        user = UserORM(
            username=username,
            nickname=nickname or username,
            password_hash=_hash_password(password),
            gender=gender,
            sexual_orientation=sexual_orientation,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        user_id = user.id
        out_user = {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname,
            "gender": user.gender,
            "sexual_orientation": user.sexual_orientation,
            "age": user.age,
            "region": user.region or "",
            "occupation": user.occupation or "",
        }

    token = generate_token(user_id)
    return {"token": token, "user": out_user}


@router.post("/api/auth/login")
async def user_login(data: dict):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.username == username).first()
        if not user or not _verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if "$" not in (user.password_hash or ""):
            # 兼容升级：用户首次使用旧 hash 登录后，平滑迁移到新 hash
            user.password_hash = _hash_password(password)
            db.commit()

        user_id = user.id
        out_user = {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname,
            "role": user.role or "user",
            "gender": user.gender,
            "sexual_orientation": user.sexual_orientation or "",
            "age": user.age,
            "region": user.region or "",
            "occupation": user.occupation or "",
        }

    token = generate_token(user_id)
    return {"token": token, "user": out_user}
@router.post("/api/auth/logout")
async def user_logout(x_token: Optional[str] = Header(None)):
  """登出：删除 Redis 中的 Token"""
  if x_token:
      delete_token(x_token)
  return {"ok": True}

@router.get("/api/auth/me")
async def user_me(x_token: Optional[str] = Header(None)):
    user_id = redis_verify_token(x_token) if x_token else None
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录或Token已过期")

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        return {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname,
            "gender": user.gender,
            "sexual_orientation": user.sexual_orientation,
            "age": user.age,
            "region": (user.region or "").strip() if getattr(user, "region", None) is not None else "",
            "occupation": (user.occupation or "").strip() if getattr(user, "occupation", None) is not None else "",
            "avatar_url": getattr(user, "avatar_url", None) or "",
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }


@router.patch("/api/auth/me")
async def user_update_me(data: dict, x_token: Optional[str] = Header(None)):
    user_id = redis_verify_token(x_token) if x_token else None
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录或Token已过期")

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        if "nickname" in data:
            user.nickname = data["nickname"].strip()
        if "gender" in data:
            user.gender = data["gender"]
        if "sexual_orientation" in data:
            user.sexual_orientation = data["sexual_orientation"]
        if "avatar_url" in data and isinstance(data["avatar_url"], str):
            user.avatar_url = data["avatar_url"].strip()[:500]
        if "age" in data:
            a = data.get("age")
            if a is None or a == "":
                user.age = None
            else:
                try:
                    ai = int(a)
                    user.age = ai if 0 <= ai <= 150 else None
                except (TypeError, ValueError):
                    pass
        if "region" in data and isinstance(data["region"], str):
            user.region = data["region"].strip()[:120]
        if "occupation" in data and isinstance(data["occupation"], str):
            user.occupation = data["occupation"].strip()[:100]

        return {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname,
            "gender": user.gender,
            "sexual_orientation": user.sexual_orientation,
            "age": user.age,
            "region": (user.region or "") if getattr(user, "region", None) is not None else "",
            "occupation": (user.occupation or "") if getattr(user, "occupation", None) is not None else "",
            "avatar_url": getattr(user, "avatar_url", None) or "",
        }


def clear_all_admin_tokens():
    """清空所有管理员 token（如修改密码后调用）"""
    with get_db() as db:
        db.query(AdminTokenORM).delete()
    _admin_token_store.clear()


@router.get("/api/users/stats")
async def user_stats(x_token: Optional[str] = Header(None)):
    """获取当前用户的统计数据（亲密度>5的伴侣数、总对话轮数、陪伴最久天数）"""
    user_id = redis_verify_token(x_token) if x_token else None
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录")

    from datetime import datetime, timezone

    user_id_str = str(user_id)

    with get_db() as db:
        # 获取当前用户信息
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        # 获取用户拥有的 companions（通过 created_by 匹配）
        all_companions = db.query(CompanionORM).all()
        user_companions = []
        for c in all_companions:
            cb = (c.created_by or "").strip()
            if cb == user_id_str:
                user_companions.append(c)

        # 统计亲密度>5的伴侣数、总对话轮数、陪伴最久天数
        intimate_companion_count = 0
        total_turns = 0
        max_days_together = 0
        now = datetime.now(timezone.utc)

        for c in user_companions:
            user_state = db.query(UserCompanionStateORM).filter(
                UserCompanionStateORM.user_id == user_id,
                UserCompanionStateORM.companion_id == c.id
            ).first()

            affection = user_state.affection if user_state else 0
            turns = user_state.turns if user_state else 0

            # 亲密度>5的伴侣
            if affection and affection > 5:
                intimate_companion_count += 1
                total_turns += turns

                # 计算陪伴天数
                if c.created_at:
                    ct = c.created_at
                    if ct.tzinfo is None:
                        ct = ct.replace(tzinfo=timezone.utc)
                    days = (now - ct).days
                    if days > max_days_together:
                        max_days_together = days

    return {
        "intimate_companion_count": intimate_companion_count,
        "total_turns": total_turns,
        "max_days_together": max_days_together,
    }
