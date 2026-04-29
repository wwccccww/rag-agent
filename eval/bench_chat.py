import argparse
import json
import math
import time
from typing import Iterable

import httpx


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if p <= 0:
        return float(values[0])
    if p >= 100:
        return float(values[-1])
    k = math.ceil((p / 100) * len(values)) - 1
    k = max(0, min(k, len(values) - 1))
    return float(values[k])


def iter_sse_lines(resp: httpx.Response) -> Iterable[str]:
    # FastAPI StreamingResponse 常见是逐行输出：`data: {...}\n\n`
    for line in resp.iter_lines():
        if not line:
            continue
        yield line


def bench_once(
    client: httpx.Client,
    *,
    api_base: str,
    question: str,
    session_id: str | None,
    user_id: str,
    agent: bool,
) -> tuple[float | None, float]:
    url = f"{api_base.rstrip('/')}/v1/chat/agent/stream" if agent else f"{api_base.rstrip('/')}/v1/chat/stream"
    payload = {
        "user_id": user_id,
        "session_id": session_id,
        "message": question,
    }

    t0 = time.perf_counter()
    ttft_ms: float | None = None
    current_event: str | None = None

    with client.stream("POST", url, json=payload) as resp:
        resp.raise_for_status()
        for line in iter_sse_lines(resp):
            if line.startswith("event:"):
                current_event = line[len("event:") :].strip()
                continue
            if line.startswith("data:"):
                data = line[len("data:") :].strip()
            else:
                # 兼容后端如果直接输出 json 行
                data = line.strip()
            if not data:
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue

            # 兼容两种协议：
            # 1) 标准 SSE：event: token + data: {...}
            # 2) 直接输出 JSON 行：{"type":"token", ...}
            event_type = current_event or str(obj.get("type") or "")

            if ttft_ms is None and event_type == "token":
                ttft_ms = (time.perf_counter() - t0) * 1000
            if event_type in ("final", "error"):
                break

    total_ms = (time.perf_counter() - t0) * 1000
    return ttft_ms, total_ms


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark SSE chat TTFT/latency (p50/p95).")
    ap.add_argument("--api-base", default="http://127.0.0.1:8000", help="FastAPI base url")
    ap.add_argument("--n", type=int, default=20, help="number of runs")
    ap.add_argument("--agent", action="store_true", help="benchmark /v1/chat/agent/stream")
    ap.add_argument("--user-id", default="demo")
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--question", default="请用三点解释 RAG，并给一个小例子。")
    args = ap.parse_args()

    ttfts: list[float] = []
    totals: list[float] = []

    with httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        for i in range(args.n):
            ttft_ms, total_ms = bench_once(
                client,
                api_base=args.api_base,
                question=args.question,
                session_id=args.session_id,
                user_id=args.user_id,
                agent=args.agent,
            )
            if ttft_ms is not None:
                ttfts.append(ttft_ms)
            totals.append(total_ms)
            print(f"[{i+1:02d}/{args.n}] ttft={None if ttft_ms is None else round(ttft_ms, 1)}ms total={round(total_ms, 1)}ms")

    print("=" * 72)
    print(f"runs={args.n} agent={args.agent} api_base={args.api_base}")
    print(f"TTFT(ms):  n={len(ttfts)} p50={percentile(ttfts,50)} p95={percentile(ttfts,95)} max={(max(ttfts) if ttfts else None)}")
    print(f"TOTAL(ms): n={len(totals)} p50={percentile(totals,50)} p95={percentile(totals,95)} max={(max(totals) if totals else None)}")


if __name__ == "__main__":
    main()

