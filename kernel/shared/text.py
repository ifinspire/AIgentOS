from __future__ import annotations

import math
import re


def chunk_text(text: str, max_chars: int = 500) -> list[str]:
    """Split text into word-boundary chunks of at most *max_chars* characters."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    words = cleaned.split()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        candidate_len = current_len + len(word) + (1 if current else 0)
        if current and candidate_len > max_chars:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len = candidate_len
    if current:
        chunks.append(" ".join(current))
    return chunks


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return -1.0
    dot = 0.0
    left_mag = 0.0
    right_mag = 0.0
    for lval, rval in zip(left, right):
        dot += lval * rval
        left_mag += lval * lval
        right_mag += rval * rval
    if left_mag <= 0.0 or right_mag <= 0.0:
        return -1.0
    return dot / (math.sqrt(left_mag) * math.sqrt(right_mag))


def extract_visible_text(text: str) -> str:
    """Strip ``<think>...</think>`` blocks from model output."""
    no_think = re.sub(r"<think>[\s\S]*?</think>", "", text or "", flags=re.IGNORECASE)
    no_tags = re.sub(r"</?think>", "", no_think, flags=re.IGNORECASE)
    return no_tags.strip()


def preview_text(text: str, max_chars: int = 120) -> str:
    cleaned = " ".join((text or "").split()).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[:max_chars].rstrip()}..."
