"""Run blocking DB/ORM work on REST_POOL without blocking the event loop."""
from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, TypeVar

from core.executor import REST_POOL

T = TypeVar("T")


async def run_rest(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Execute a synchronous callable on REST_POOL."""
    loop = asyncio.get_running_loop()
    if kwargs:
        fn = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(REST_POOL, fn)
    if not args:
        return await loop.run_in_executor(REST_POOL, func)
    return await loop.run_in_executor(REST_POOL, functools.partial(func, *args))
