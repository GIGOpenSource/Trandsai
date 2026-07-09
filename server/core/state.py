"""全局运行时状态，供各路由模块共享访问"""

import asyncio

companion_manager = None
_companion_sessions: dict[str, dict] = {}
_session_lock = asyncio.Lock()


def get_companion_manager():
    return companion_manager


def set_companion_manager(cm):
    global companion_manager
    companion_manager = cm


def get_session(companion_id: str) -> dict:
    return _companion_sessions.get(companion_id, {}).copy()


async def set_session(companion_id: str, data: dict):
    async with _session_lock:
        _companion_sessions[companion_id] = data.copy()


async def update_session(companion_id: str, **kwargs):
    """原子性更新 session 中的部分字段"""
    async with _session_lock:
        session = _companion_sessions.get(companion_id, {})
        updated = session.copy()
        updated.update(kwargs)
        _companion_sessions[companion_id] = updated
        return updated
