"""Global runtime state — session uses Redis when REDIS_URL is set."""

import asyncio
from typing import Optional, Union

from core.session_store import session_get, session_set

companion_manager = None
_companion_sessions: dict[str, dict] = {}
_session_lock = asyncio.Lock()


def get_companion_manager():
    return companion_manager


def set_companion_manager(cm):
    global companion_manager
    companion_manager = cm


def _mem_key(companion_id: str, user_id: Optional[Union[int, str]] = None) -> str:
    if user_id is None or user_id == "":
        return companion_id
    return f"{user_id}:{companion_id}"


def get_session(companion_id: str, user_id: Optional[Union[int, str]] = None) -> dict:
    remote = session_get(companion_id, user_id=user_id)
    if remote:
        return remote.copy()
    return _companion_sessions.get(_mem_key(companion_id, user_id), {}).copy()


async def set_session(
    companion_id: str,
    data: dict,
    user_id: Optional[Union[int, str]] = None,
):
    uid = user_id if user_id is not None else (data or {}).get("user_id")
    async with _session_lock:
        _companion_sessions[_mem_key(companion_id, uid)] = data.copy()
        session_set(companion_id, data, user_id=uid)


async def update_session(companion_id: str, user_id: Optional[Union[int, str]] = None, **kwargs):
    async with _session_lock:
        key = _mem_key(companion_id, user_id if user_id is not None else kwargs.get("user_id"))
        session = _companion_sessions.get(key, {})
        if not session:
            session = session_get(companion_id, user_id=user_id) or {}
        updated = session.copy()
        updated.update(kwargs)
        uid = user_id if user_id is not None else updated.get("user_id")
        _companion_sessions[_mem_key(companion_id, uid)] = updated
        session_set(companion_id, updated, user_id=uid)
        return updated
