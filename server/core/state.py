"""Global runtime state — session uses Redis when REDIS_URL is set."""

import asyncio

from core.session_store import session_get, session_set

companion_manager = None
_companion_sessions: dict[str, dict] = {}
_session_lock = asyncio.Lock()


def get_companion_manager():
    return companion_manager


def set_companion_manager(cm):
    global companion_manager
    companion_manager = cm


def get_session(companion_id: str) -> dict:
    remote = session_get(companion_id)
    if remote:
        return remote.copy()
    return _companion_sessions.get(companion_id, {}).copy()


async def set_session(companion_id: str, data: dict):
    async with _session_lock:
        _companion_sessions[companion_id] = data.copy()
        session_set(companion_id, data)


async def update_session(companion_id: str, **kwargs):
    async with _session_lock:
        session = _companion_sessions.get(companion_id, {})
        if not session:
            session = session_get(companion_id) or {}
        updated = session.copy()
        updated.update(kwargs)
        _companion_sessions[companion_id] = updated
        session_set(companion_id, updated)
        return updated
