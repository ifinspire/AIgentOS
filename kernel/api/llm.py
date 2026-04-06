from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Awaitable, Callable
from typing import Protocol
import time

import httpx

from kernel.shared.text import extract_visible_text


@dataclass
class ChatMessageIn:
    role: str
    content: str


@dataclass
class ChatCompletionResult:
    content: str
    latency_ms: int
    ttft_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


class LLMClient(Protocol):
    async def chat(
        self,
        messages: list[ChatMessageIn],
        max_tokens: int | None = None,
        on_chunk: Callable[[str, str], Awaitable[None] | None] | None = None,
    ) -> ChatCompletionResult:
        ...


class OllamaClient:
    def __init__(self, base_url: str, model: str):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=90)

    async def chat(
        self,
        messages: list[ChatMessageIn],
        max_tokens: int | None = None,
        on_chunk: Callable[[str, str], Awaitable[None] | None] | None = None,
    ) -> ChatCompletionResult:
        options: dict[str, int | float] = {"temperature": 0.7}
        if isinstance(max_tokens, int) and max_tokens > 0:
            options["num_predict"] = max_tokens
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": options,
        }
        url = f"{self._base_url}/api/chat"

        started = time.perf_counter()
        content_parts: list[str] = []
        ttft_ms: int | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        total_tokens: int | None = None
        async with self._client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for raw_line in resp.aiter_lines():
                line = (raw_line or "").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = data.get("message") or {}
                chunk = message.get("content")
                if isinstance(chunk, str) and chunk:
                    content_parts.append(chunk)
                    if ttft_ms is None and extract_visible_text("".join(content_parts)):
                        ttft_ms = int((time.perf_counter() - started) * 1000)
                    if on_chunk is not None:
                        maybe_result = on_chunk(chunk, "".join(content_parts))
                        if maybe_result is not None:
                            await maybe_result
                p_tok = data.get("prompt_eval_count")
                c_tok = data.get("eval_count")
                if isinstance(p_tok, int):
                    prompt_tokens = p_tok
                if isinstance(c_tok, int):
                    completion_tokens = c_tok
                if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
                    total_tokens = prompt_tokens + completion_tokens
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        content = "".join(content_parts).strip()
        if not content:
            raise RuntimeError("Invalid Ollama response format")

        return ChatCompletionResult(
            content=content,
            latency_ms=elapsed_ms,
            ttft_ms=ttft_ms,
            prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
            completion_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
            total_tokens=total_tokens,
        )


class OllamaEmbeddingClient:
    def __init__(self, base_url: str, model: str):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=60)

    async def embed(self, text: str) -> list[float]:
        payload = {"model": self._model, "input": text}
        resp = await self._client.post(f"{self._base_url}/api/embed", json=payload)
        if resp.status_code == 404:
            resp = await self._client.post(
                f"{self._base_url}/api/embeddings",
                json={"model": self._model, "prompt": text},
            )
        resp.raise_for_status()
        data = resp.json()

        vec_raw = None
        embeddings = data.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            vec_raw = embeddings[0]
        if vec_raw is None:
            embedding = data.get("embedding")
            if isinstance(embedding, list):
                vec_raw = embedding
        if not isinstance(vec_raw, list) or not vec_raw:
            raise RuntimeError("Invalid embedding response format")
        try:
            return [float(v) for v in vec_raw]
        except Exception as exc:
            raise RuntimeError("Embedding vector contains non-numeric values") from exc
