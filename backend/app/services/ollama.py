import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Iterator

import httpx

from app.config import settings
from app.telemetry import telemetry

# ── 进程级 Embedding LRU 缓存 ──────────────────────────────────
# key = sha256(model + text)，value = embedding list
# 容量 512 条，线程安全（SQLAlchemy 同步路由在线程池中运行）
_EMBED_CACHE: "OrderedDict[str, list[float]]" = OrderedDict()
_EMBED_CACHE_MAX = 512
_EMBED_LOCK = threading.Lock()


def _embed_cache_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


def _cache_get(key: str) -> list[float] | None:
    with _EMBED_LOCK:
        if key in _EMBED_CACHE:
            _EMBED_CACHE.move_to_end(key)
            return _EMBED_CACHE[key]
    return None


def _cache_set(key: str, value: list[float]) -> None:
    with _EMBED_LOCK:
        if key in _EMBED_CACHE:
            _EMBED_CACHE.move_to_end(key)
        else:
            if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
                _EMBED_CACHE.popitem(last=False)  # 淘汰最旧
            _EMBED_CACHE[key] = value


class OllamaClient:
    def __init__(self) -> None:
        self.base = settings.ollama_base_url.rstrip("/")
        self._client = httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0))

    def close(self) -> None:
        self._client.close()

    def tags(self) -> dict[str, Any]:
        r = self._client.get(f"{self.base}/api/tags")
        r.raise_for_status()
        return r.json()

    def embed(self, text: str) -> list[float]:
        key = _embed_cache_key(settings.ollama_embed_model, text)
        cached = _cache_get(key)
        if cached is not None:
            logging.debug("[Ollama] embed cache hit (%.0f chars)", len(text))
            return cached

        t0 = time.perf_counter()
        r = self._client.post(
            f"{self.base}/api/embeddings",
            json={"model": settings.ollama_embed_model, "prompt": text},
        )
        r.raise_for_status()
        data = r.json()
        emb = data.get("embedding")
        if not isinstance(emb, list):
            raise RuntimeError("invalid embedding response")
        if len(emb) != settings.embed_dim:
            raise RuntimeError(f"embedding dim mismatch: got {len(emb)}, expected {settings.embed_dim}")
        elapsed = (time.perf_counter() - t0) * 1000
        logging.debug("[Ollama] embed %.0f chars → %.0fms", len(text), elapsed)
        telemetry.record_embed(elapsed)
        _cache_set(key, emb)
        return emb

    def chat_with_tools(self, messages: list[dict], tools: list[dict]) -> dict:
        """非流式工具调用：让 LLM 决定调用哪个工具（或直接回答）。
        返回 assistant 消息 dict，含 tool_calls 列表（可能为空）或 content 字符串。
        """
        t0 = time.perf_counter()
        r = self._client.post(
            f"{self.base}/api/chat",
            json={
                "model": settings.ollama_chat_model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "options": {"temperature": 0.0},  # 决策阶段用贪心解码
            },
        )
        r.raise_for_status()
        data = r.json()
        msg = data.get("message") or {}
        elapsed = (time.perf_counter() - t0) * 1000
        tool_calls = msg.get("tool_calls") or []
        logging.info(
            "[Ollama] chat_with_tools %.0fms | tools_called=%d",
            elapsed, len(tool_calls),
        )
        telemetry.record_chat_with_tools(elapsed)
        return msg

    def chat_complete(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        t0 = time.perf_counter()
        r = self._client.post(
            f"{self.base}/api/chat",
            json={
                "model": settings.ollama_chat_model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature},
            },
        )
        r.raise_for_status()
        data = r.json()
        msg = data.get("message") or {}
        content = msg.get("content") or ""
        result = str(content).strip()
        elapsed = (time.perf_counter() - t0) * 1000
        prompt_tokens = data.get("prompt_eval_count", "?")
        eval_tokens = data.get("eval_count", "?")
        logging.info(
            "[Ollama] chat_complete %.0fms | prompt_tokens=%s eval_tokens=%s",
            elapsed, prompt_tokens, eval_tokens,
        )
        telemetry.record_chat_complete(elapsed)
        return result

    def chat_stream(self, messages: list[dict[str, str]], temperature: float = 0.3) -> Iterator[str]:
        t0 = time.perf_counter()
        first_token = True
        ttft_ms: float | None = None
        total_tokens = 0
        with self._client.stream(
            "POST",
            f"{self.base}/api/chat",
            json={
                "model": settings.ollama_chat_model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": temperature},
            },
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("done"):
                    elapsed = (time.perf_counter() - t0) * 1000
                    eval_tokens = obj.get("eval_count", total_tokens)
                    tps = eval_tokens / (elapsed / 1000) if elapsed > 0 else 0
                    logging.info(
                        "[Ollama] stream done %.0fms | tokens=%s %.1f tok/s",
                        elapsed, eval_tokens, tps,
                    )
                    telemetry.record_stream(ttft_ms=ttft_ms, total_ms=elapsed, tokens=int(eval_tokens))
                    break
                m = obj.get("message") or {}
                piece = m.get("content") or ""
                if piece:
                    if first_token:
                        ttft_ms = (time.perf_counter() - t0) * 1000
                        logging.info("[Ollama] first token %.0fms", ttft_ms)
                        first_token = False
                    total_tokens += 1
                    yield piece


def get_ollama() -> OllamaClient:
    return OllamaClient()
