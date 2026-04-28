import json
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
        return emb

    def chat_complete(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
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
        return str(content).strip()

    def chat_stream(self, messages: list[dict[str, str]], temperature: float = 0.3) -> Iterator[str]:
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
                    break
                m = obj.get("message") or {}
                piece = m.get("content") or ""
                if piece:
                    yield piece


def get_ollama() -> OllamaClient:
    return OllamaClient()
