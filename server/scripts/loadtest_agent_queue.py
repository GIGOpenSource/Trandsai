#!/usr/bin/env python3
"""
Agent 队列模式压测（AGENT_QUEUE=1）。

用法示例：
  python scripts/loadtest_agent_queue.py \\
    --base-url http://localhost:8000 \\
    --token YOUR_USER_TOKEN \\
    --companion-id abcd1234 \\
    --concurrency 20 \\
    --messages 3

环境要求：
  - REDIS_URL / ARQ_REDIS_URL 已配置
  - AGENT_QUEUE=1
  - agent-worker 已启动（arq workers.agent_worker.WorkerSettings）
  - 有效 user token + companion_id

观测指标（stdout）：
  - 连接成功数 / 失败数
  - 每轮 typing→首条 message 延迟 P50/P95
  - 错误与超时
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from typing import List, Optional

try:
    import websockets
except ImportError:
    raise SystemExit("请先安装: pip install websockets")


async def one_client(
    base_ws: str,
    companion_id: str,
    token: str,
    messages: int,
    timeout: float,
    client_id: int,
) -> List[float]:
    """返回每条消息从 send 到首个 assistant message 的秒数。"""
    lang = "zh"
    url = f"{base_ws}/ws/chat/{companion_id}?lang={lang}&token={token}"
    latencies: List[float] = []
    try:
        async with websockets.connect(url, open_timeout=15, max_size=2**22) as ws:
            for i in range(messages):
                payload = {
                    "text": f"[loadtest] c{client_id} m{i+1} {time.time():.0f}",
                    "lang": lang,
                    "user_gender": "",
                    "tz": "Asia/Shanghai",
                    "tz_offset": -480,
                }
                t0 = time.perf_counter()
                await ws.send(json.dumps(payload, ensure_ascii=False))
                got = False
                deadline = t0 + timeout
                while time.perf_counter() < deadline:
                    remaining = deadline - time.perf_counter()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 5.0))
                    except asyncio.TimeoutError:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") == "message" and data.get("role") == "assistant":
                        latencies.append(time.perf_counter() - t0)
                        got = True
                        break
                    if data.get("type") == "error":
                        print(f"[c{client_id}] error: {data.get('text')}")
                        break
                if not got:
                    print(f"[c{client_id}] timeout waiting reply for m{i+1}")
    except Exception as e:
        print(f"[c{client_id}] connect/fail: {e}")
    return latencies


async def run(args: argparse.Namespace) -> None:
    base = args.base_url.rstrip("/")
    if base.startswith("https://"):
        base_ws = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base_ws = "ws://" + base[len("http://") :]
    else:
        base_ws = base

    print(
        f"Queue loadtest: concurrency={args.concurrency} messages/client={args.messages} "
        f"timeout={args.timeout}s companion={args.companion_id}"
    )
    tasks = [
        one_client(
            base_ws,
            args.companion_id,
            args.token,
            args.messages,
            args.timeout,
            i,
        )
        for i in range(args.concurrency)
    ]
    t_start = time.perf_counter()
    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - t_start
    all_lat: List[float] = [x for row in results for x in row]
    expected = args.concurrency * args.messages
    ok = len(all_lat)
    print("---")
    print(f"wall_time_s={elapsed:.1f}")
    print(f"replies_ok={ok}/{expected}")
    if all_lat:
        all_lat.sort()
        p50 = statistics.median(all_lat)
        p95 = all_lat[max(0, int(len(all_lat) * 0.95) - 1)]
        print(f"latency_s p50={p50:.2f} p95={p95:.2f} max={max(all_lat):.2f}")
    else:
        print("latency: n/a (no successful replies)")
    print(
        "提示: 同时观察 redis LLEN chat:flush:queue / ARQ 队列深度，以及 agent-worker 日志。"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="AGENT_QUEUE=1 WebSocket load test")
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--token", required=True, help="user_token (x-token)")
    p.add_argument("--companion-id", required=True)
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument("--messages", type=int, default=2)
    p.add_argument("--timeout", type=float, default=120.0)
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
