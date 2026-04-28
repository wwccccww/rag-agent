import json
import logging
import time
from typing import Any, Iterator

import httpx

from app.config import settings


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
        logging.debug("[Ollama] embed %.0f chars → %.0fms", len(text), (time.perf_counter() - t0) * 1000)
        return emb

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
        return result

    def chat_stream(self, messages: list[dict[str, str]], temperature: float = 0.3) -> Iterator[str]:
        t0 = time.perf_counter()
        first_token = True
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
                    break
                m = obj.get("message") or {}
                piece = m.get("content") or ""
                if piece:
                    if first_token:
                        logging.info("[Ollama] first token %.0fms", (time.perf_counter() - t0) * 1000)
                        first_token = False
                    total_tokens += 1
                    yield piece


def get_ollama() -> OllamaClient:
    return OllamaClient()
