import math
import threading
import time
from collections import deque
from dataclasses import dataclass


def _percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile on a sorted list."""
    if not values:
        return None
    if p <= 0:
        return float(values[0])
    if p >= 100:
        return float(values[-1])
    k = math.ceil((p / 100) * len(values)) - 1
    k = max(0, min(k, len(values) - 1))
    return float(values[k])


@dataclass(frozen=True)
class StreamSample:
    ttft_ms: float | None
    total_ms: float
    tokens: int


class _Rolling:
    def __init__(self, maxlen: int) -> None:
        self._dq: deque[float] = deque(maxlen=maxlen)

    def add(self, v: float) -> None:
        self._dq.append(float(v))

    def snapshot(self) -> list[float]:
        return list(self._dq)


class _RollingStream:
    def __init__(self, maxlen: int) -> None:
        self._dq: deque[StreamSample] = deque(maxlen=maxlen)

    def add(self, s: StreamSample) -> None:
        self._dq.append(s)

    def snapshot(self) -> list[StreamSample]:
        return list(self._dq)


class Telemetry:
    """进程内轻量指标聚合（无外部依赖，适合本地/作品集证明可观测）。"""

    def __init__(self, window: int = 200) -> None:
        self._start_ts = time.time()
        self._lock = threading.Lock()
        self._window = int(window)

        self._embed_ms = _Rolling(window)
        self._chat_complete_ms = _Rolling(window)
        self._chat_with_tools_ms = _Rolling(window)
        self._stream = _RollingStream(window)

        self._counters: dict[str, int] = {
            "embed_calls": 0,
            "chat_complete_calls": 0,
            "chat_with_tools_calls": 0,
            "stream_calls": 0,
        }

    def record_embed(self, elapsed_ms: float) -> None:
        with self._lock:
            self._counters["embed_calls"] += 1
            self._embed_ms.add(elapsed_ms)

    def record_chat_complete(self, elapsed_ms: float) -> None:
        with self._lock:
            self._counters["chat_complete_calls"] += 1
            self._chat_complete_ms.add(elapsed_ms)

    def record_chat_with_tools(self, elapsed_ms: float) -> None:
        with self._lock:
            self._counters["chat_with_tools_calls"] += 1
            self._chat_with_tools_ms.add(elapsed_ms)

    def record_stream(self, *, ttft_ms: float | None, total_ms: float, tokens: int) -> None:
        with self._lock:
            self._counters["stream_calls"] += 1
            self._stream.add(StreamSample(ttft_ms=ttft_ms, total_ms=total_ms, tokens=int(tokens)))

    def snapshot(self) -> dict:
        with self._lock:
            embed = sorted(self._embed_ms.snapshot())
            cc = sorted(self._chat_complete_ms.snapshot())
            cwt = sorted(self._chat_with_tools_ms.snapshot())
            stream = self._stream.snapshot()

            ttfts = sorted([s.ttft_ms for s in stream if s.ttft_ms is not None])
            totals = sorted([s.total_ms for s in stream])
            tokens = [s.tokens for s in stream]

            total_s = sum(s.total_ms for s in stream) / 1000 if stream else 0.0
            tps = (sum(tokens) / total_s) if total_s > 0 else None

            return {
                "uptime_s": round(time.time() - self._start_ts, 3),
                "window": self._window,
                "counters": dict(self._counters),
                "ollama": {
                    "embed_ms": {
                        "n": len(embed),
                        "p50": _percentile(embed, 50),
                        "p95": _percentile(embed, 95),
                        "max": embed[-1] if embed else None,
                    },
                    "chat_complete_ms": {
                        "n": len(cc),
                        "p50": _percentile(cc, 50),
                        "p95": _percentile(cc, 95),
                        "max": cc[-1] if cc else None,
                    },
                    "chat_with_tools_ms": {
                        "n": len(cwt),
                        "p50": _percentile(cwt, 50),
                        "p95": _percentile(cwt, 95),
                        "max": cwt[-1] if cwt else None,
                    },
                    "stream": {
                        "n": len(stream),
                        "ttft_ms": {
                            "n": len(ttfts),
                            "p50": _percentile(ttfts, 50),
                            "p95": _percentile(ttfts, 95),
                            "max": ttfts[-1] if ttfts else None,
                        },
                        "total_ms": {
                            "n": len(totals),
                            "p50": _percentile(totals, 50),
                            "p95": _percentile(totals, 95),
                            "max": totals[-1] if totals else None,
                        },
                        "tokens_total": int(sum(tokens)) if tokens else 0,
                        "tokens_per_sec_overall": None if tps is None else round(tps, 3),
                    },
                },
            }


telemetry = Telemetry()

