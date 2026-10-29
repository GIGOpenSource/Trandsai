#!/usr/bin/env python3
"""对话抗压：测本机能撑多少在线连接 / 多少路同时聊天。

不要用 Apifox 当主力。对话是 WebSocket + LLM，Apifox 适合 REST 冒烟，测不出
排队、回合锁、typing→首包延迟。本仓库已有同目录的 loadtest_agent_queue.py，
本脚本在其基础上加了：爬坡找拐点、多 token（多用户）、纯连接压测、拉取 /api/health。

瓶颈（默认值，以进程环境为准）：
  HEAVY_PATH_SEMAPHORE / MAX_CONCURRENT_AGENTS = 8   同时跑 Agent 的上限
  MAX_LLM_CONCURRENT = 50                          LLM 线程闸
  同一 user_id × companion_id 回合锁：同一账号打同一智能体 = 串行，不是并发
  另外还有模型供应商限流、余额、本机 CPU/内存/带宽

因此「能支持多少客户」要拆开看：
  1) 挂着 WS 不说话的在线人数  →  --mode connect
  2) 同时各发一轮、等 AI 回复  →  --mode chat，且必须多个用户 token

示例：
  pip install websockets

  # 先测连接上限（几乎不耗 LLM）
  python scripts/loadtest_chat.py --mode connect --ramp 20,50,100,200 \\
    --base-url http://127.0.0.1:8001 --companion-id abcd1234 --token USER_TOKEN

  # 再测同时对话（每个并发必须不同用户，一行一个 token）
  python scripts/loadtest_chat.py --mode chat --ramp 2,4,8,12,16 \\
    --base-url http://127.0.0.1:8001 --companion-id abcd1234 \\
    --tokens-file tokens.txt --messages 1 --timeout 90

  # 观察服务端闸门
  curl http://127.0.0.1:8001/api/health
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.request
from typing import List, Optional, Tuple

try:
    import websockets
except ImportError:
    raise SystemExit("请先安装: pip install websockets")


def _ws_base(http_base: str) -> str:
    base = http_base.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :]
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :]
    return base


def _load_tokens(args: argparse.Namespace) -> List[str]:
    tokens: List[str] = []
    if args.tokens_file:
        with open(args.tokens_file, "r", encoding="utf-8") as f:
            tokens = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    if args.token:
        tokens.insert(0, args.token.strip())
    # 保序去重
    seen = set()
    out = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    if not out:
        raise SystemExit("需要 --token 或 --tokens-file")
    return out


def fetch_health(base_url: str) -> dict:
    url = base_url.rstrip("/") + "/api/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def _pct(sorted_vals: List[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round((len(sorted_vals) - 1) * q))))
    return sorted_vals[idx]


async def one_connect(
    url: str,
    hold: float,
) -> Tuple[bool, str]:
    try:
        async with websockets.connect(url, open_timeout=15, max_size=2**22) as ws:
            t_end = time.perf_counter() + hold
            while time.perf_counter() < t_end:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
        return True, ""
    except Exception as e:
        return False, str(e)[:200]


async def one_chat(
    url: str,
    messages: int,
    timeout: float,
    client_id: int,
) -> Tuple[List[float], int, int]:
    """返回 (成功延迟列表, 超时数, 业务错误数)。"""
    latencies: List[float] = []
    timeouts = 0
    errors = 0
    try:
        async with websockets.connect(url, open_timeout=15, max_size=2**22) as ws:
            for i in range(messages):
                payload = {
                    "text": f"[loadtest] c{client_id} m{i + 1} {int(time.time())}",
                    "lang": "zh",
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
                    typ = data.get("type")
                    if typ == "message" and data.get("role") == "assistant":
                        latencies.append(time.perf_counter() - t0)
                        got = True
                        break
                    if typ == "error":
                        errors += 1
                        print(f"  [c{client_id}] error: {data.get('text') or data.get('reason')}")
                        got = True
                        break
                if not got:
                    timeouts += 1
                    print(f"  [c{client_id}] timeout m{i + 1}")
    except Exception as e:
        errors += max(1, messages - len(latencies) - timeouts - errors)
        print(f"  [c{client_id}] connect/fail: {e}")
    return latencies, timeouts, errors


def _print_health(h: dict) -> None:
    if h.get("error"):
        print(f"  health: {h['error']}")
        return
    llm = h.get("llm") or {}
    print(
        "  health: queue={q} depth={d} max_agents={m} llm_inflight={i}/{cap}".format(
            q=h.get("agent_queue_enabled"),
            d=h.get("agent_queue_depth"),
            m=h.get("max_concurrent_agents"),
            i=llm.get("inflight"),
            cap=llm.get("limit") or llm.get("cap") or llm.get("max"),
        )
    )


async def stage_connect(args, tokens: List[str], n: int) -> dict:
    base_ws = _ws_base(args.base_url)
    cid = args.companion_id
    urls = [
        f"{base_ws}/ws/chat/{cid}?lang=zh&token={tokens[i % len(tokens)]}"
        for i in range(n)
    ]
    t0 = time.perf_counter()
    results = await asyncio.gather(*[one_connect(u, args.hold) for u in urls])
    ok = sum(1 for ok_, _ in results if ok_)
    fail = n - ok
    samples = [err for ok_, err in results if not ok_ and err]
    return {
        "n": n,
        "ok": ok,
        "fail": fail,
        "wall_s": time.perf_counter() - t0,
        "sample_err": samples[:3],
        "error_rate": fail / n if n else 0,
    }


async def stage_chat(args, tokens: List[str], n: int) -> dict:
    if len(tokens) < n:
        print(
            f"  警告: 只有 {len(tokens)} 个 token，却要 {n} 路并发。"
            "同一账号同一智能体会回合锁串行，结果会偏乐观/偏慢，不能代表多客户。"
        )
    base_ws = _ws_base(args.base_url)
    cid = args.companion_id
    urls = [
        f"{base_ws}/ws/chat/{cid}?lang=zh&token={tokens[i % len(tokens)]}"
        for i in range(n)
    ]
    t0 = time.perf_counter()
    rows = await asyncio.gather(
        *[one_chat(u, args.messages, args.timeout, i) for i, u in enumerate(urls)]
    )
    wall = time.perf_counter() - t0
    lats: List[float] = []
    timeouts = errors = 0
    for lat, to, er in rows:
        lats.extend(lat)
        timeouts += to
        errors += er
    expected = n * args.messages
    ok = len(lats)
    lats.sort()
    fail = expected - ok
    return {
        "n": n,
        "ok": ok,
        "fail": fail,
        "timeouts": timeouts,
        "errors": errors,
        "expected": expected,
        "wall_s": wall,
        "p50": _pct(lats, 0.50),
        "p95": _pct(lats, 0.95),
        "max": lats[-1] if lats else 0.0,
        "rps": ok / wall if wall else 0.0,
        "error_rate": fail / expected if expected else 0,
    }


def _parse_ramp(raw: str) -> List[int]:
    nums = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        n = int(part)
        if n > 0:
            nums.append(n)
    if not nums:
        raise SystemExit("--ramp 至少要有一个正整数，例如 2,4,8,16")
    return nums


async def run(args: argparse.Namespace) -> int:
    tokens = _load_tokens(args)
    levels = _parse_ramp(args.ramp)
    print(f"mode={args.mode} base={args.base_url} companion={args.companion_id}")
    print(f"tokens={len(tokens)} ramp={levels}")
    print("health_before:", json.dumps(fetch_health(args.base_url), ensure_ascii=False)[:400])
    print("---")
    limit_n: Optional[int] = None
    last_ok = 0
    for n in levels:
        print(f"\n== stage n={n} ==")
        if args.mode == "connect":
            stats = await stage_connect(args, tokens, n)
            print(
                f"  connect_ok={stats['ok']}/{stats['n']} "
                f"error_rate={stats['error_rate']:.1%} wall={stats['wall_s']:.1f}s"
            )
            if stats["sample_err"]:
                print(f"  sample_err={stats['sample_err']}")
        else:
            stats = await stage_chat(args, tokens, n)
            print(
                f"  replies={stats['ok']}/{stats['expected']} "
                f"timeouts={stats['timeouts']} errors={stats['errors']} "
                f"error_rate={stats['error_rate']:.1%}"
            )
            print(
                f"  latency_s p50={stats['p50']:.2f} p95={stats['p95']:.2f} "
                f"max={stats['max']:.2f} wall={stats['wall_s']:.1f}s rps={stats['rps']:.2f}"
            )
        _print_health(fetch_health(args.base_url))
        too_many_errors = stats["error_rate"] > args.error_budget
        too_slow = args.mode == "chat" and stats.get("p95", 0) > args.p95_budget
        if too_many_errors or too_slow:
            why = []
            if too_many_errors:
                why.append(f"error_rate>{args.error_budget:.0%}")
            if too_slow:
                why.append(f"p95>{args.p95_budget}s")
            print(f"  STOP: {', '.join(why)}  ← 本机在 n={n} 已过拐点")
            limit_n = n
            break
        last_ok = n
        if args.pause > 0:
            await asyncio.sleep(args.pause)
    print("\n== 结论 ==")
    if args.mode == "connect":
        print(f"WS 同时在线：最后稳定档 {last_ok}，下一档压垮点 {limit_n or '未碰到'}")
        print("这是「挂着不说话」的人数，不是同时打字人数。")
    else:
        print(f"同时对话：最后稳定档 {last_ok} 路，下一档压垮点 {limit_n or '未碰到'}")
        print(
            "对照 /api/health 的 max_concurrent_agents（默认 8）："
            "超过后会排队，P95 先坏，不一定立刻报错。"
        )
        print("真·多客户必须 tokens 数 ≥ 并发数，否则同一账号会被回合锁串行。")
    return 0 if limit_n is None or last_ok > 0 else 1


def main() -> None:
    p = argparse.ArgumentParser(description="WebSocket 对话抗压（找本机拐点）")
    p.add_argument("--base-url", default="http://127.0.0.1:8001")
    p.add_argument("--token", default="", help="单个用户 token；多用户请用 --tokens-file")
    p.add_argument("--tokens-file", default="", help="每行一个 user token")
    p.add_argument("--companion-id", required=True)
    p.add_argument("--mode", choices=("connect", "chat"), default="chat")
    p.add_argument("--ramp", default="2,4,8,12", help="逗号分隔的并发档，由小到大")
    p.add_argument("--messages", type=int, default=1, help="chat 模式每连接发送条数")
    p.add_argument("--timeout", type=float, default=90.0)
    p.add_argument("--hold", type=float, default=8.0, help="connect 模式保持连接秒数")
    p.add_argument("--p95-budget", type=float, default=20.0, help="chat 模式 P95 超过则视为拐点")
    p.add_argument("--error-budget", type=float, default=0.05)
    p.add_argument("--pause", type=float, default=2.0, help="档位之间休息秒")
    args = p.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
