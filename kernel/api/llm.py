from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import time

import httpx


@dataclass
class ChatMessageIn:
    role: str
    content: str


@dataclass
class ChatCompletionResult:
    content: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


class LLMClient(Protocol):
    async def chat(self, messages: list[ChatMessageIn], max_tokens: int | None = None) -> ChatCompletionResult:
        ...


class OllamaClient:
    def __init__(self, base_url: str, model: str):
        self._base_url = base_url.rstrip("/")
        self._model = model

    async def chat(self, messages: list[ChatMessageIn], max_tokens: int | None = None) -> ChatCompletionResult:
        options: dict[str, int | float] = {"temperature": 0.7}
        if isinstance(max_tokens, int) and max_tokens > 0:
            options["num_predict"] = max_tokens
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": options,
        }
        url = f"{self._base_url}/api/chat"

        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        try:
            content = str(data["message"]["content"]).strip()
        except Exception as exc:
            raise RuntimeError("Invalid Ollama response format") from exc

        prompt_tokens = data.get("prompt_eval_count")
        completion_tokens = data.get("eval_count")
        total_tokens: int | None = None
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            total_tokens = prompt_tokens + completion_tokens

        return ChatCompletionResult(
            content=content,
            latency_ms=elapsed_ms,
            prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
            completion_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
            total_tokens=total_tokens,
        )
