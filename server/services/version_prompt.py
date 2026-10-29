"""按客户端版本号注入聊天兜底提示词（最高权重前缀）。"""
from __future__ import annotations

import logging
import re
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_VERSION = "1.1.1"
DEFAULT_PROMPT = (
    "Keep the conversation healthy and non-sexual. Refuse erotic, explicit, or adult "
    "content in any language and steer back to a normal topic."
)

# 固定包一层，避免中文策略把回复语种带偏；真正回复语言仍由后续 system 决定
_LANG_GUARD = (
    "[HIGHEST PRIORITY CONTENT POLICY / 最高优先级内容规则] "
    "Applies in every language. Do not change the reply language because of this block; "
    "follow the language instructions that appear after it.\n"
)

_bound_version: ContextVar[str] = ContextVar("chat_app_version", default="")
_bound_registered_at: ContextVar[Optional[datetime]] = ContextVar(
    "chat_user_registered_at", default=None
)
_bound_user_id: ContextVar[Optional[int]] = ContextVar("chat_version_user_id", default=None)
_cache: dict[str, dict[str, Any]] = {}
_cache_at = 0.0
_excl_cache: dict[int, set[int]] = {}
_excl_cache_at = 0.0
_CACHE_TTL_S = 15.0
_last_saved_version: dict[int, str] = {}

_VER_RE = re.compile(r"[^0-9A-Za-z._+-]+")


def normalize_app_version(raw: Optional[str]) -> str:
    text = (raw or "").strip()
    if "," in text:
        text = text.split(",", 1)[0].strip()
    if text.lower().startswith("v") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    text = _VER_RE.sub("", text)
    return text[:32]


def extract_app_version(headers=None, query=None) -> str:
    """WebSocket / HTTP：Header 优先，其次 query。"""
    headers = headers or {}
    query = query or {}

    def _get(src, *names: str) -> str:
        if src is None:
            return ""
        getter = getattr(src, "get", None)
        if not getter:
            return ""
        for name in names:
            val = getter(name)
            if val is None and hasattr(src, "get"):
                try:
                    val = src.get(name.lower())
                except Exception:
                    val = None
            if val:
                return str(val).strip()
        return ""

    raw = _get(
        headers,
        "x-app-version",
        "X-App-Version",
        "x-app-version-name",
        "X-App-Version-Name",
        "x-client-version",
        "X-Client-Version",
    )
    if not raw:
        raw = _get(query, "app_version", "version", "ver")
    return normalize_app_version(raw)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def parse_optional_datetime(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _aware(raw)
    text = str(raw).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return _aware(datetime.fromisoformat(text))
    except ValueError as exc:
        raise ValueError("invalid_datetime") from exc


def format_version_prefix(prompt: str) -> str:
    body = (prompt or "").strip()
    if not body:
        return ""
    if body.lstrip().startswith("[HIGHEST PRIORITY"):
        return body
    return _LANG_GUARD + body


def remember_user_app_version(user_id: Optional[int], version: Optional[str], db=None) -> None:
    """有版本才写入 users.app_version；空串不覆盖。"""
    ver = normalize_app_version(version)
    if not ver or not user_id:
        return
    uid = int(user_id)
    if _last_saved_version.get(uid) == ver:
        return
    from core.database import UserORM, get_db

    def _write(session) -> None:
        user = session.query(UserORM).filter(UserORM.id == uid).first()
        if not user:
            return
        current = (getattr(user, "app_version", None) or "").strip()
        if current == ver:
            _last_saved_version[uid] = ver
            return
        user.app_version = ver
        session.flush()
        _last_saved_version[uid] = ver

    try:
        if db is not None:
            _write(db)
            return
        with get_db() as session:
            _write(session)
            session.commit()
    except Exception as e:
        logger.warning("[version_prompt] save user app_version failed user=%s: %s", uid, e)


def persist_app_version_from_request(request, user_id: Optional[int], db=None) -> str:
    if request is None or not user_id:
        return ""
    ver = extract_app_version(
        getattr(request, "headers", None),
        getattr(request, "query_params", None),
    )
    remember_user_app_version(user_id, ver, db=db)
    return ver


def hydrate_user_version_context(
    user_id: Optional[int], incoming_version: Optional[str] = None
) -> tuple[str, Optional[datetime]]:
    """聊天用：header 优先，否则用户表已存版本；并取出注册时间做批次窗。"""
    ver = normalize_app_version(incoming_version)
    registered_at = None
    stored = ""
    if user_id:
        try:
            from core.database import UserORM, get_db

            with get_db() as db:
                user = db.query(UserORM).filter(UserORM.id == int(user_id)).first()
                if user:
                    registered_at = _aware(getattr(user, "created_at", None))
                    stored = normalize_app_version(getattr(user, "app_version", None) or "")
                    if ver:
                        remember_user_app_version(user_id, ver, db=db)
                        db.commit()
        except Exception as e:
            logger.warning("[version_prompt] hydrate user failed user=%s: %s", user_id, e)
    return (ver or stored), registered_at


def inspect_app_version(headers=None, query=None) -> dict[str, Any]:
    """只读版本相关字段，便于日志确认前端有没有传。"""
    headers = headers or {}
    query = query or {}
    getter_h = getattr(headers, "get", None)
    getter_q = getattr(query, "get", None)

    def _one(getter, *names: str) -> str:
        if not getter:
            return ""
        for name in names:
            val = getter(name)
            if val:
                return str(val).strip()[:64]
        return ""

    header_raw = _one(
        getter_h,
        "x-app-version",
        "X-App-Version",
        "x-app-version-name",
        "X-App-Version-Name",
        "x-client-version",
        "X-Client-Version",
    )
    query_raw = _one(getter_q, "app_version", "version", "ver")
    normalized = extract_app_version(headers, query)
    effective = normalized or DEFAULT_VERSION
    prefix = resolve_version_prompt(normalized)
    return {
        "header": header_raw,
        "query": query_raw,
        "normalized": normalized,
        "effective": effective,
        "prompt_on": bool(prefix),
        "prompt_len": len(prefix or ""),
    }


def log_app_version(source: str, *, headers=None, query=None, incoming: str = "", user_id=None) -> str:
    if headers is not None or query is not None:
        info = inspect_app_version(headers, query)
    else:
        normalized = normalize_app_version(incoming)
        effective = normalized or DEFAULT_VERSION
        prefix = resolve_version_prompt(normalized)
        info = {
            "header": "",
            "query": "",
            "normalized": normalized,
            "effective": effective,
            "prompt_on": bool(prefix),
            "prompt_len": len(prefix or ""),
        }
    logger.info(
        "[APP_VERSION] source=%s user=%s header=%s query=%s incoming=%s "
        "normalized=%s effective=%s prompt_on=%s prompt_len=%s",
        source,
        user_id if user_id is not None else "-",
        info.get("header") or "-",
        info.get("query") or "-",
        (incoming or "-"),
        info.get("normalized") or "-",
        info.get("effective"),
        info.get("prompt_on"),
        info.get("prompt_len"),
    )
    return str(info.get("normalized") or "")


def bind_app_version(version: Optional[str]) -> Any:
    return _bound_version.set(normalize_app_version(version))


def bind_registered_at(registered_at: Optional[datetime]) -> Any:
    return _bound_registered_at.set(_aware(registered_at))


def bind_version_user_id(user_id: Optional[int]) -> Any:
    try:
        uid = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        uid = None
    return _bound_user_id.set(uid)


def reset_app_version(token) -> None:
    try:
        _bound_version.reset(token)
    except Exception:
        pass


def reset_registered_at(token) -> None:
    try:
        _bound_registered_at.reset(token)
    except Exception:
        pass


def reset_version_user_id(token) -> None:
    try:
        _bound_user_id.reset(token)
    except Exception:
        pass


def bound_app_version() -> str:
    return normalize_app_version(_bound_version.get() or "")


def bound_registered_at() -> Optional[datetime]:
    return _aware(_bound_registered_at.get())


def bound_version_user_id() -> Optional[int]:
    return _bound_user_id.get()


def invalidate_version_prompt_cache() -> None:
    global _cache, _cache_at, _excl_cache, _excl_cache_at
    _cache = {}
    _cache_at = 0.0
    _excl_cache = {}
    _excl_cache_at = 0.0


def _load_map() -> dict[str, dict[str, Any]]:
    global _cache, _cache_at
    now = time.monotonic()
    if _cache_at and (now - _cache_at) < _CACHE_TTL_S:
        return _cache
    from core.database import ChatVersionPromptORM, get_db

    out: dict[str, dict[str, Any]] = {}
    try:
        with get_db() as db:
            rows = db.query(ChatVersionPromptORM).all()
            for row in rows:
                ver = normalize_app_version(row.version)
                if not ver:
                    continue
                out[ver] = {
                    "id": row.id,
                    "version": ver,
                    "prompt": (row.prompt or "").strip(),
                    "is_enabled": bool(row.is_enabled),
                    "apply_registered_start_at": _aware(
                        getattr(row, "apply_registered_start_at", None)
                    ),
                    "apply_registered_end_at": _aware(
                        getattr(row, "apply_registered_end_at", None)
                    ),
                }
    except Exception as e:
        logger.warning("[version_prompt] load failed: %s", e)
        return _cache or {}
    _cache = out
    _cache_at = now
    return out


def _load_exclusions() -> dict[int, set[int]]:
    global _excl_cache, _excl_cache_at
    now = time.monotonic()
    if _excl_cache_at and (now - _excl_cache_at) < _CACHE_TTL_S:
        return _excl_cache
    from core.database import ChatVersionPromptUserORM, get_db

    out: dict[int, set[int]] = {}
    try:
        with get_db() as db:
            rows = db.query(
                ChatVersionPromptUserORM.prompt_id,
                ChatVersionPromptUserORM.user_id,
            ).all()
            for prompt_id, user_id in rows:
                if not prompt_id or not user_id:
                    continue
                out.setdefault(int(prompt_id), set()).add(int(user_id))
    except Exception as e:
        logger.warning("[version_prompt] load exclusions failed: %s", e)
        return _excl_cache or {}
    _excl_cache = out
    _excl_cache_at = now
    return out


def user_excluded_from_prompt(prompt_id: Optional[int], user_id: Optional[int]) -> bool:
    if not prompt_id or not user_id:
        return False
    try:
        return int(user_id) in _load_exclusions().get(int(prompt_id), set())
    except (TypeError, ValueError):
        return False


def _window_ok(row: dict[str, Any], registered_at: Optional[datetime]) -> bool:
    start = row.get("apply_registered_start_at")
    end = row.get("apply_registered_end_at")
    if start is None and end is None:
        return True
    if registered_at is None:
        return False
    ra = _aware(registered_at)
    if start and ra < start:
        return False
    if end and ra > end:
        return False
    return True


def resolve_version_prompt(
    version: Optional[str] = None,
    registered_at: Optional[datetime] = None,
    user_id: Optional[int] = None,
) -> str:
    """未传版本 → 1.1.1；启用且注册时间落入窗口、且未被移出合规名单才注入。"""
    requested = normalize_app_version(version) or DEFAULT_VERSION
    table = _load_map()
    row = table.get(requested)
    if row is None and requested != DEFAULT_VERSION:
        row = table.get(DEFAULT_VERSION)
    if not row or not row.get("is_enabled") or not _window_ok(row, registered_at):
        return ""
    uid = user_id if user_id is not None else bound_version_user_id()
    if user_excluded_from_prompt(row.get("id"), uid):
        return ""
    return format_version_prefix(str(row.get("prompt") or "").strip())


def current_version_prefix() -> str:
    return resolve_version_prompt(
        bound_app_version(),
        bound_registered_at(),
        bound_version_user_id(),
    )


def apply_version_prompt_prefix(system_text: str, version: Optional[str] = None) -> str:
    prefix = resolve_version_prompt(
        version if version is not None else bound_app_version(),
        bound_registered_at(),
        bound_version_user_id(),
    )
    body = system_text or ""
    if not prefix:
        return body
    if body.lstrip().startswith(prefix):
        return body
    return f"{prefix}\n\n{body}"


def ensure_default_version_prompt() -> None:
    from core.database import ChatVersionPromptORM, get_db

    try:
        with get_db() as db:
            exists = (
                db.query(ChatVersionPromptORM.id)
                .filter(ChatVersionPromptORM.version == DEFAULT_VERSION)
                .first()
            )
            if exists:
                return
            db.add(
                ChatVersionPromptORM(
                    version=DEFAULT_VERSION,
                    prompt=DEFAULT_PROMPT,
                    is_enabled=False,
                    remark="缺省版本：未传版本号时使用；默认关闭",
                    sort_order=0,
                )
            )
            db.commit()
            invalidate_version_prompt_cache()
    except Exception as e:
        logger.warning("[version_prompt] seed default failed: %s", e)


def cohort_users_query(db, row):
    """注册窗内用户（永久批次）；无窗则按当前 app_version 匹配。"""
    from sqlalchemy import or_

    from core.database import UserORM

    q = db.query(UserORM)
    start = _aware(getattr(row, "apply_registered_start_at", None))
    end = _aware(getattr(row, "apply_registered_end_at", None))
    if start is not None:
        q = q.filter(UserORM.created_at >= start)
    if end is not None:
        q = q.filter(UserORM.created_at <= end)
    if start is None and end is None:
        ver = normalize_app_version(getattr(row, "version", None))
        if ver == DEFAULT_VERSION:
            q = q.filter(
                or_(
                    UserORM.app_version == ver,
                    UserORM.app_version == "",
                    UserORM.app_version.is_(None),
                )
            )
        else:
            q = q.filter(UserORM.app_version == ver)
    return q


def row_to_dict(row, *, applied_count=None, removed_count=None) -> dict[str, Any]:
    def _iso(dt) -> str:
        dt = _aware(dt)
        return dt.isoformat() if dt else ""

    return {
        "id": row.id,
        "version": row.version or "",
        "prompt": row.prompt or "",
        "is_enabled": bool(row.is_enabled),
        "remark": row.remark or "",
        "sort_order": int(row.sort_order or 0),
        "apply_registered_start_at": _iso(getattr(row, "apply_registered_start_at", None)),
        "apply_registered_end_at": _iso(getattr(row, "apply_registered_end_at", None)),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "applied_count": int(applied_count or 0),
        "removed_count": int(removed_count or 0),
    }
