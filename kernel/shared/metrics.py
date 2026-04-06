from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kernel.api.llm import ChatMessageIn


def estimate_tokens_for_messages(messages: list[ChatMessageIn]) -> int:
    # TODO: char/4 is a rough heuristic (~3.5 for SmolLM3 English).
    # Consider using Ollama's /api/tokenize endpoint for accuracy.
    return max(1, math.ceil(sum(len(m.content or "") for m in messages) / 4))


def estimate_tokens_for_text(text: str) -> int:
    return max(1, math.ceil(len(text or "") / 4))


def allocate_estimated_tokens(
    total: int | None,
    system_chars: int,
    user_chars: int,
    assistant_chars: int,
) -> tuple[int | None, int | None, int | None]:
    if total is None:
        return None, None, None
    total_chars = system_chars + user_chars + assistant_chars
    if total_chars <= 0:
        return 0, 0, 0
    system_est = round(total * system_chars / total_chars)
    user_est = round(total * user_chars / total_chars)
    assistant_est = total - system_est - user_est
    return system_est, user_est, assistant_est
