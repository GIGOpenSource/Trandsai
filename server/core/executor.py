"""Dedicated thread pools for Agent vs REST workloads."""
import concurrent.futures
import os

AGENT_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=int(os.getenv("AGENT_POOL_SIZE", "16")),
    thread_name_prefix="agent",
)

REST_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=int(os.getenv("REST_POOL_SIZE", "8")),
    thread_name_prefix="rest",
)
