"""Global concurrency limits for Agent and LLM calls (REQ-A6)."""
import asyncio
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Dict, Iterator, Optional, Tuple

# SRS aliases + backward-compatible env names
HEAVY_PATH_SEMAPHORE = int(
    os.getenv("HEAVY_PATH_SEMAPHORE")
    or os.getenv("MAX_CONCURRENT_AGENTS", "8")
)
MAX_CONCURRENT_AGENTS = HEAVY_PATH_SEMAPHORE
agent_semaphore = asyncio.Semaphore(HEAVY_PATH_SEMAPHORE)

MAX_LLM_CONCURRENT = int(os.getenv("MAX_LLM_CONCURRENT", "50"))

# asyncio.Semaphore is not safe across threads; LLM invoke runs in thread pools.
_llm_thread_gate = threading.Semaphore(MAX_LLM_CONCURRENT)
_inflight_lock = threading.Lock()
_llm_inflight = 0

_user_locks: Dict[str, asyncio.Lock] = {}
_user_locks_guard = asyncio.Lock()

_TURN_LOCK_TTL = int(os.getenv("TURN_LOCK_TTL", "130"))
# 等上一轮结束的最长时间（秒）；应对齐 agent 超时，避免短等就对用户回「有点忙」
_TURN_LOCK_WAIT_S = float(os.getenv("TURN_LOCK_WAIT_S", "120") or 120)
_RELEASE_OWN_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
else
  return 0
end
"""


def _user_lock_key(user_id: int, companion_id: str) -> str:
    return f"{user_id}:{companion_id}"


class RedisTurnLock:
    """分布式回合锁：SET NX EX；仅持有者可用 Lua/token 释放。"""

    def __init__(self, client: Any, key: str, ttl: int = _TURN_LOCK_TTL):
        self._client = client
        self._key = key
        self._ttl = ttl
        self._token = secrets.token_hex(16)
        self._held = False

    def _try_acquire_sync(self) -> bool:
        return bool(self._client.set(self._key, self._token, nx=True, ex=self._ttl))

    def _release_sync(self) -> None:
        try:
            self._client.eval(_RELEASE_OWN_LUA, 1, self._key, self._token)
        except Exception:
            pass

    async def acquire(self, timeout: float = 15.0) -> bool:
        """有超时的获取；timeout 内轮询 NX，避免僵死锁永等。"""
        deadline = asyncio.get_event_loop().time() + max(1.0, float(timeout))
        try:
            while True:
                ok = await asyncio.to_thread(self._try_acquire_sync)
                if ok:
                    self._held = True
                    return True
                if asyncio.get_event_loop().time() >= deadline:
                    return False
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            if self._held:
                await asyncio.to_thread(self._release_sync)
                self._held = False
            raise

    def release(self) -> None:
        if not self._held:
            return
        self._release_sync()
        self._held = False


async def get_user_lock(user_id: int, companion_id: str) -> Any:
    """返回带 async acquire()/sync release() 的锁；Redis 不可用时回退进程内 Lock。"""
    from core.redis_client import get_redis_client

    try:
        client = get_redis_client()
        if client is not None:
            return RedisTurnLock(
                client, f"turn_lock:{_user_lock_key(user_id, companion_id)}"
            )
    except Exception:
        pass

    key = _user_lock_key(user_id, companion_id)
    async with _user_locks_guard:
        lock = _user_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _user_locks[key] = lock
        return lock


@asynccontextmanager
async def user_turn_lock(user_id: int, companion_id: str) -> AsyncIterator[None]:
    """同一 user×companion 串行一轮对话（REQ-A6）。"""
    lock = await get_user_lock(user_id, companion_id)
    wait_s = float(os.getenv("TURN_LOCK_WAIT_S", str(_TURN_LOCK_WAIT_S)) or _TURN_LOCK_WAIT_S)
    try:
        if isinstance(lock, RedisTurnLock):
            acquired = await lock.acquire(timeout=wait_s)
        else:
            acquired = await asyncio.wait_for(lock.acquire(), timeout=wait_s)
    except asyncio.TimeoutError as e:
        raise TimeoutError("turn_lock_busy") from e
    if not acquired:
        raise TimeoutError("turn_lock_busy")
    try:
        yield
    finally:
        lock.release()


def heavy_slots_in_use() -> Tuple[int, int]:
    """返回 (in_use, capacity)。"""
    try:
        free = agent_semaphore._value  # type: ignore[attr-defined]
    except Exception:
        free = HEAVY_PATH_SEMAPHORE
    in_use = max(0, HEAVY_PATH_SEMAPHORE - int(free))
    return in_use, HEAVY_PATH_SEMAPHORE


def heavy_load_ratio() -> float:
    in_use, cap = heavy_slots_in_use()
    if cap <= 0:
        return 1.0
    return in_use / cap


@contextmanager
def llm_gate() -> Iterator[None]:
    """Thread-safe gate for all synchronous LLM invoke paths."""
    global _llm_inflight
    _llm_thread_gate.acquire()
    with _inflight_lock:
        _llm_inflight += 1
    try:
        yield
    finally:
        with _inflight_lock:
            _llm_inflight -= 1
        _llm_thread_gate.release()


@contextmanager
def llm_gate_timed() -> Iterator[float]:
    """同 llm_gate，但 yield 获取锁等待毫秒数（用于区分排队 vs 模型耗时）。"""
    global _llm_inflight
    t0 = time.perf_counter()
    _llm_thread_gate.acquire()
    wait_ms = (time.perf_counter() - t0) * 1000.0
    with _inflight_lock:
        _llm_inflight += 1
    try:
        yield wait_ms
    finally:
        with _inflight_lock:
            _llm_inflight -= 1
        _llm_thread_gate.release()


def llm_gate_stats() -> dict:
    """In-flight LLM calls vs configured cap (S-19)."""
    with _inflight_lock:
        inflight = _llm_inflight
    return {"max_concurrent": MAX_LLM_CONCURRENT, "in_flight": inflight}
