from __future__ import annotations

from pathlib import Path
import time
from datetime import datetime, timezone
import asyncio
import json
import uuid

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from kernel.shared.text import chunk_text, cosine_similarity, extract_visible_text
from kernel.shared.metrics import estimate_tokens_for_messages, estimate_tokens_for_text, allocate_estimated_tokens

from .llm import ChatMessageIn, OllamaClient, OllamaEmbeddingClient
from .models import (
    BaselineJobStartResponse,
    BaselineStartRequest,
    BaselineJobStatusResponse,
    BaselineCaseResult,
    BaselineCategoryResult,
    BaselineRunResponse,
    ChatAcceptedResponse,
    ChatRequest,
    ConversationDetail,
    ConversationEventsResponse,
    ConversationSummary,
    CreateConversationRequest,
    ContextSettingsResponse,
    ContextSettingsUpdateRequest,
    DeleteAllDataRequest,
    DeleteAllDataResponse,
    DebugLogResponse,
    HealthResponse,
    PerformanceMetrics,
    PerformanceExchange,
    PerformanceSummaryResponse,
    MemoryChunkListResponse,
    MemoryChunkResponse,
    PromptComponentUpdateRequest,
    PromptProfileCreateRequest,
    PromptProfileResponse,
    PromptResetResponse,
    PromptComponentResponse,
    PromptBreakdown,
    SystemPromptResponse,
    TokenWindowStats,
    WarmupResponse,
    InteractionEventResponse,
    MessageResponse,
)
from .prompts import compose_system_prompt, load_prompt_bundle, load_prompt_components
from .settings import get_settings
from .storage import ChatStore, StoredInteractionEvent


settings = get_settings()
repo_root = Path(__file__).resolve().parents[2]
prompt_bundle = load_prompt_bundle(repo_root=repo_root, default_agent_id=settings.default_agent_id)
store = ChatStore(settings.chat_db_path)
llm_client = OllamaClient(settings.ollama_base_url, settings.ollama_model)
embedding_client = OllamaEmbeddingClient(settings.embedding_base_url, settings.embedding_model)
_warmup_lock = asyncio.Lock()
_warmup_completed_at: datetime | None = None
_baseline_jobs: dict[str, dict] = {}


def _tenant_id() -> str:
    return settings.aigent_tenant_id


def _default_compact_trigger() -> float:
    agent_file = repo_root / "agent-prompts" / "basic" / "agent.yaml"
    if not agent_file.exists():
        return 0.9
    try:
        import yaml
        config = yaml.safe_load(agent_file.read_text(encoding="utf-8")) or {}
        strategy = config.get("context_strategy") or {}
        compact_pct = float(strategy.get("pruning_threshold", 0.9))
        return compact_pct
    except Exception:
        return 0.9


def _get_context_settings():
    compact_pct = _default_compact_trigger()
    current = store.ensure_context_settings(
        _tenant_id(),
        settings.ollama_context_window,
        settings.ollama_max_response_tokens,
        compact_pct,
    )
    if (
        current.max_context_tokens != settings.ollama_context_window
        or current.max_response_tokens != settings.ollama_max_response_tokens
    ):
        current = store.update_context_settings(
            tenant_id=_tenant_id(),
            max_context_tokens=settings.ollama_context_window,
            max_response_tokens=settings.ollama_max_response_tokens,
        )
    return current


def _effective_prompt_components():
    defaults = load_prompt_components(repo_root=repo_root)
    profile = store.get_active_prompt_profile(_tenant_id())
    overrides = store.get_prompt_overrides(profile.id)
    merged = []
    for item in defaults:
        override = overrides.get(item.id)
        if override is None:
            merged.append(item)
            continue
        merged.append(
            item.__class__(
                id=item.id,
                name=item.name,
                content=override["content"] if override.get("content") is not None else item.content,
                order=item.order,
                enabled=override["enabled"] if override.get("enabled") is not None else item.enabled,
                is_system=item.is_system,
            )
        )
    return profile, merged


def _window_stats(window: tuple[int, int, int, int]) -> TokenWindowStats:
    total_tokens, prompt_tokens, completion_tokens, exchange_count = window
    avg = 0.0 if exchange_count == 0 else float(total_tokens) / float(exchange_count)
    return TokenWindowStats(
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        exchange_count=exchange_count,
        avg_tokens_per_exchange=avg,
    )

app = FastAPI(
    title="AIgentOS Kernel API",
    version="0.2.4-oss",
    description="Minimal OSS chat API wired to Ollama",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    )



# Utility aliases — implementations live in kernel.shared.text / kernel.shared.metrics
_estimate_tokens_for_messages = estimate_tokens_for_messages
_estimate_tokens_for_text = estimate_tokens_for_text
_chunk_text = chunk_text
_cosine_similarity = cosine_similarity


async def _store_chunks_for_source(source_type: str, source_id: str, content: str) -> None:
    chunks = _chunk_text(content)
    if not chunks:
        return
    embedded_chunks: list[tuple[str, list[float]]] = []
    for chunk in chunks:
        try:
            embedding = await embedding_client.embed(chunk)
        except Exception:
            return
        embedded_chunks.append((chunk, embedding))
    store.upsert_rag_chunks(source_type, source_id, embedded_chunks)


async def _retrieve_context_chunks(query: str, exclude_source_id: str | None = None, limit: int = 5) -> list[str]:
    try:
        query_embedding = await embedding_client.embed(query)
    except Exception:
        return []
    scored: list[tuple[float, str]] = []
    for chunk in store.iter_rag_chunks():
        if exclude_source_id and chunk.source_id == exclude_source_id:
            continue
        score = _cosine_similarity(query_embedding, chunk.embedding)
        if score <= 0:
            continue
        scored.append((score, chunk.content))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [content for _, content in scored[:limit]]


def _event_to_response(event: StoredInteractionEvent) -> InteractionEventResponse:
    return InteractionEventResponse(
        id=event.id,
        conversation_id=event.conversation_id,
        role=event.role,  # type: ignore[arg-type]
        event_type=event.event_type,
        content=event.content,
        status=event.status,  # type: ignore[arg-type]
        timestamp=event.created_at,
        processed_at=event.processed_at,
        error=event.error,
        causation_event_id=event.causation_event_id,
    )


def _message_from_event(event: StoredInteractionEvent) -> MessageResponse:
    return MessageResponse(
        id=event.id,
        role=event.role,  # type: ignore[arg-type]
        content=event.content,
        timestamp=event.created_at,
    )


def _conversation_events_payload(conversation_id: str) -> dict | None:
    detail = store.get_conversation_detail(conversation_id)
    if detail is None:
        return None
    title, updated_at, _ = detail
    events = store.get_conversation_events(conversation_id)
    latest_exchange = store.get_latest_performance_exchange_for_conversation(conversation_id)
    summary = store.summarize_performance()
    return {
        "id": conversation_id,
        "title": title,
        "updated_at": updated_at.isoformat(),
        "events": [
            {
                "id": event.id,
                "conversation_id": event.conversation_id,
                "role": event.role,
                "event_type": event.event_type,
                "content": event.content,
                "status": event.status,
                "timestamp": event.created_at.isoformat(),
                "processed_at": event.processed_at.isoformat() if event.processed_at else None,
                "error": event.error,
                "causation_event_id": event.causation_event_id,
            }
            for event in events
        ],
        "latest_performance": (
            {
                "id": latest_exchange.id,
                "conversation_id": latest_exchange.conversation_id,
                "created_at": latest_exchange.created_at.isoformat(),
                "user_preview": latest_exchange.user_preview,
                "assistant_preview": latest_exchange.assistant_preview,
                "metrics": {
                    "total_latency_ms": latest_exchange.total_latency_ms,
                    "llm_latency_ms": latest_exchange.llm_latency_ms,
                    "ttft_ms": latest_exchange.ttft_ms,
                    "prompt_tokens": latest_exchange.prompt_tokens,
                    "completion_tokens": latest_exchange.completion_tokens,
                    "total_tokens": latest_exchange.total_tokens,
                    "retrieved_chunk_count": latest_exchange.retrieved_chunk_count,
                    "retrieved_chunks": [
                        {
                            "content": chunk.content,
                            "score": chunk.score,
                            "source_id": chunk.source_id,
                            "source_type": chunk.source_type,
                            "source_preview": chunk.source_preview,
                        }
                        for chunk in latest_exchange.retrieved_chunks
                    ],
                    "prompt_breakdown": {
                        "system_chars": latest_exchange.system_chars,
                        "user_chars": latest_exchange.user_chars,
                        "assistant_chars": latest_exchange.assistant_chars,
                        "system_tokens_est": latest_exchange.system_tokens_est,
                        "user_tokens_est": latest_exchange.user_tokens_est,
                        "assistant_tokens_est": latest_exchange.assistant_tokens_est,
                    },
                },
            }
            if latest_exchange is not None
            else None
        ),
        "performance_summary": {
            "exchange_count": summary["exchange_count"],
            "latency_min_ms": summary["latency_min_ms"],
            "latency_max_ms": summary["latency_max_ms"],
            "latency_avg_ms": summary["latency_avg_ms"],
            "tokens_day": {
                "total_tokens": summary["tokens_day"][0],
                "prompt_tokens": summary["tokens_day"][1],
                "completion_tokens": summary["tokens_day"][2],
                "exchange_count": summary["tokens_day"][3],
                "avg_tokens_per_exchange": 0.0 if summary["tokens_day"][3] == 0 else float(summary["tokens_day"][0]) / float(summary["tokens_day"][3]),
            },
            "tokens_week": {
                "total_tokens": summary["tokens_week"][0],
                "prompt_tokens": summary["tokens_week"][1],
                "completion_tokens": summary["tokens_week"][2],
                "exchange_count": summary["tokens_week"][3],
                "avg_tokens_per_exchange": 0.0 if summary["tokens_week"][3] == 0 else float(summary["tokens_week"][0]) / float(summary["tokens_week"][3]),
            },
            "tokens_month": {
                "total_tokens": summary["tokens_month"][0],
                "prompt_tokens": summary["tokens_month"][1],
                "completion_tokens": summary["tokens_month"][2],
                "exchange_count": summary["tokens_month"][3],
                "avg_tokens_per_exchange": 0.0 if summary["tokens_month"][3] == 0 else float(summary["tokens_month"][0]) / float(summary["tokens_month"][3]),
            },
            "tokens_all_time": {
                "total_tokens": summary["tokens_all_time"][0],
                "prompt_tokens": summary["tokens_all_time"][1],
                "completion_tokens": summary["tokens_all_time"][2],
                "exchange_count": summary["tokens_all_time"][3],
                "avg_tokens_per_exchange": 0.0 if summary["tokens_all_time"][3] == 0 else float(summary["tokens_all_time"][0]) / float(summary["tokens_all_time"][3]),
            },
        },
    }


_extract_visible_assistant_text = extract_visible_text


def _should_refresh_conversation_summary(exchange_count: int) -> bool:
    return exchange_count == 1 or (exchange_count > 0 and exchange_count % 10 == 0)


async def _refresh_conversation_summary(conversation_id: str) -> None:
    messages = store.get_messages(conversation_id)
    exchange_count = sum(1 for m in messages if m.role == "assistant")
    if not _should_refresh_conversation_summary(exchange_count):
        return

    dialogue: list[str] = []
    for m in messages:
        if m.role == "user":
            dialogue.append(f"User: {m.content.strip()}")
        elif m.role == "assistant":
            visible = _extract_visible_assistant_text(m.content)
            dialogue.append(f"Assistant: {visible}")

    transcript = "\n".join(line for line in dialogue if line).strip()
    if not transcript:
        return
    if len(transcript) > 8000:
        transcript = transcript[-8000:]

    summary_messages = [
        ChatMessageIn(
            role="system",
            content=(
                "Generate a short conversation title for a sidebar list. "
                "Return one plain line, 4-10 words, no markdown, no quotes."
            ),
        ),
        ChatMessageIn(
            role="user",
            content=f"Conversation transcript:\n{transcript}",
        ),
    ]
    try:
        result = await llm_client.chat(summary_messages, max_tokens=64)
    except Exception:
        return

    title = _extract_visible_assistant_text(result.content).splitlines()[0].strip() if result.content else ""
    title = title.strip(" \"'`")
    if not title:
        return
    if len(title) > 96:
        title = title[:96].rstrip()
    store.update_conversation_title(conversation_id, title)


def _build_user_payload(target_tokens: int, seed: str) -> str:
    fragment = (
        f"{seed}. Keep this coherent, factual, and concise. "
        "Include clear constraints, concrete details, and specific wording. "
    )
    text = ""
    while _estimate_tokens_for_text(text) < target_tokens:
        text += fragment
    return text


def _build_system_payload(target_tokens: int, seed: str) -> str:
    fragment = (
        f"{seed}. Preserve instruction fidelity, avoid hallucination, and remain concise. "
        "Use explicit constraints and deterministic formatting cues. "
    )
    text = ""
    while _estimate_tokens_for_text(text) < target_tokens:
        text += fragment
    return text


def _enqueue_chat_message(conversation_id: str, message: str) -> StoredInteractionEvent:
    user_event = store.create_interaction_event(
        conversation_id=conversation_id,
        role="user",
        content=message,
        status="pending",
    )
    store.maybe_set_title_from_message(conversation_id, message)
    return user_event


async def _await_end_to_end_turn(
    conversation_id: str,
    user_event_id: str,
    timeout_s: float = 120.0,
) -> tuple[int | None, int]:
    started = time.perf_counter()
    ttft_ms: int | None = None
    while time.perf_counter() - started < timeout_s:
        events = store.get_conversation_events(conversation_id)
        assistant_event = next(
            (
                event
                for event in events
                if event.role == "assistant" and event.causation_event_id == user_event_id
            ),
            None,
        )
        if assistant_event is not None:
            if ttft_ms is None:
                visible_text = extract_visible_text(assistant_event.content).strip()
                if visible_text:
                    ttft_ms = int((time.perf_counter() - started) * 1000)
            if assistant_event.status == "failed":
                raise RuntimeError(assistant_event.error or f"Assistant event {assistant_event.id} failed")
            user_event = store.get_interaction_event(user_event_id)
            if assistant_event.status == "completed" and user_event is not None and user_event.status == "completed":
                return ttft_ms, int((time.perf_counter() - started) * 1000)
            if user_event is not None and user_event.status == "failed":
                raise RuntimeError(user_event.error or f"User event {user_event_id} failed")
        user_event = store.get_interaction_event(user_event_id)
        if user_event is not None and user_event.status == "failed":
            raise RuntimeError(user_event.error or f"User event {user_event_id} failed")
        await asyncio.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for assistant completion for {user_event_id}")

async def _await_performance_exchange(user_event_id: str, timeout_s: float = 20.0):
    started = time.perf_counter()
    while time.perf_counter() - started < timeout_s:
        perf = store.get_performance_exchange_for_user_event(user_event_id)
        if perf is not None:
            return perf
        await asyncio.sleep(0.05)
    raise RuntimeError(f"Missing performance exchange for baseline user event {user_event_id}")


async def _run_single_turn_case(
    effective_prompt: str,
    case_id: str,
    label: str,
    task_instruction: str,
    input_tokens: int,
    max_response_tokens: int | None = None,
    on_progress=None,
) -> BaselineCaseResult:
    if on_progress is not None:
        on_progress(label, 0)
    user_payload = _build_user_payload(input_tokens, f"{label} input")
    messages = [
        ChatMessageIn(role="system", content=effective_prompt),
        ChatMessageIn(role="system", content=task_instruction),
        ChatMessageIn(role="user", content=user_payload),
    ]
    started = time.perf_counter()
    completion = await llm_client.chat(messages, max_tokens=max_response_tokens)
    latency_ms = int((time.perf_counter() - started) * 1000)
    prompt_tokens = completion.prompt_tokens if completion.prompt_tokens is not None else _estimate_tokens_for_messages(messages)
    completion_tokens = completion.completion_tokens if completion.completion_tokens is not None else _estimate_tokens_for_text(completion.content)
    total_tokens = completion.total_tokens if completion.total_tokens is not None else prompt_tokens + completion_tokens
    if on_progress is not None:
        on_progress(label, 1)
    return BaselineCaseResult(
        id=case_id,
        label=label,
        calls=1,
        input_tokens_est=_estimate_tokens_for_text(user_payload),
        ttft_ms=completion.ttft_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        total_latency_ms=latency_ms,
        avg_latency_ms=float(latency_ms),
        min_latency_ms=latency_ms,
        max_latency_ms=latency_ms,
        completion_time_ms=latency_ms,
    )


async def _run_system_prompt_pressure_case(
    effective_prompt: str,
    case_id: str,
    label: str,
    system_tokens: int,
    user_tokens: int,
    max_response_tokens: int | None = None,
    on_progress=None,
) -> BaselineCaseResult:
    if on_progress is not None:
        on_progress(label, 0)
    system_pressure = _build_system_payload(system_tokens, f"{label} system context")
    user_payload = _build_user_payload(user_tokens, f"{label} user input")
    messages = [
        ChatMessageIn(role="system", content=effective_prompt),
        ChatMessageIn(role="system", content=system_pressure),
        ChatMessageIn(role="user", content=user_payload),
    ]
    started = time.perf_counter()
    completion = await llm_client.chat(messages, max_tokens=max_response_tokens)
    latency_ms = int((time.perf_counter() - started) * 1000)
    prompt_tokens = completion.prompt_tokens if completion.prompt_tokens is not None else _estimate_tokens_for_messages(messages)
    completion_tokens = completion.completion_tokens if completion.completion_tokens is not None else _estimate_tokens_for_text(completion.content)
    total_tokens = completion.total_tokens if completion.total_tokens is not None else prompt_tokens + completion_tokens
    if on_progress is not None:
        on_progress(label, 1)
    return BaselineCaseResult(
        id=case_id,
        label=label,
        calls=1,
        input_tokens_est=_estimate_tokens_for_text(user_payload),
        ttft_ms=completion.ttft_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        total_latency_ms=latency_ms,
        avg_latency_ms=float(latency_ms),
        min_latency_ms=latency_ms,
        max_latency_ms=latency_ms,
        completion_time_ms=latency_ms,
    )


async def _run_single_turn_case_end_to_end(
    case_id: str,
    label: str,
    task_instruction: str,
    input_tokens: int,
    on_progress=None,
) -> BaselineCaseResult:
    if on_progress is not None:
        on_progress(label, 0)
    conversation_id, _ = store.create_conversation(title=label)
    user_payload = _build_user_payload(input_tokens, f"{label} input")
    user_event = _enqueue_chat_message(conversation_id, f"{task_instruction}\n\n{user_payload}")
    ttft_ms, total_latency_ms = await _await_end_to_end_turn(conversation_id, user_event.id)
    perf = await _await_performance_exchange(user_event.id)
    if on_progress is not None:
        on_progress(label, 1)
    prompt_tokens = perf.prompt_tokens or 0
    completion_tokens = perf.completion_tokens or 0
    total_tokens = perf.total_tokens or (prompt_tokens + completion_tokens)
    return BaselineCaseResult(
        id=case_id,
        label=label,
        calls=1,
        input_tokens_est=_estimate_tokens_for_text(user_payload),
        ttft_ms=ttft_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        total_latency_ms=total_latency_ms,
        avg_latency_ms=float(total_latency_ms),
        min_latency_ms=total_latency_ms,
        max_latency_ms=total_latency_ms,
        completion_time_ms=total_latency_ms,
    )


async def _run_multi_turn_case(
    effective_prompt: str,
    case_id: str,
    label: str,
    task_instruction: str,
    turn_targets: list[int],
    max_response_tokens: int | None = None,
    on_progress=None,
) -> BaselineCaseResult:
    messages: list[ChatMessageIn] = [
        ChatMessageIn(role="system", content=effective_prompt),
        ChatMessageIn(role="system", content=task_instruction),
    ]
    prompt_total = 0
    completion_total = 0
    token_total = 0
    latency_total = 0
    per_turn_latency_ms: list[int] = []
    per_turn_ttft_ms: list[int] = []
    per_turn_prompt_tokens: list[int] = []
    per_turn_completion_tokens: list[int] = []
    input_total = 0

    for idx, target in enumerate(turn_targets):
        if on_progress is not None:
            on_progress(f"{label} (turn {idx + 1}/{len(turn_targets)})", 0)
        user_payload = _build_user_payload(target, f"{label} turn {idx + 1}")
        input_total += _estimate_tokens_for_text(user_payload)
        messages.append(ChatMessageIn(role="user", content=user_payload))
        started = time.perf_counter()
        completion = await llm_client.chat(messages, max_tokens=max_response_tokens)
        latency_ms = int((time.perf_counter() - started) * 1000)
        latency_total += latency_ms
        per_turn_latency_ms.append(latency_ms)
        if completion.ttft_ms is not None:
            per_turn_ttft_ms.append(completion.ttft_ms)
        else:
            per_turn_ttft_ms.append(latency_ms)
        prompt_tokens = completion.prompt_tokens if completion.prompt_tokens is not None else _estimate_tokens_for_messages(messages)
        completion_tokens = completion.completion_tokens if completion.completion_tokens is not None else _estimate_tokens_for_text(completion.content)
        total_tokens = completion.total_tokens if completion.total_tokens is not None else prompt_tokens + completion_tokens
        per_turn_prompt_tokens.append(prompt_tokens)
        per_turn_completion_tokens.append(completion_tokens)
        prompt_total += prompt_tokens
        completion_total += completion_tokens
        token_total += total_tokens
        messages.append(ChatMessageIn(role="assistant", content=completion.content))
        if on_progress is not None:
            on_progress(f"{label} (turn {idx + 1}/{len(turn_targets)})", 1)

    calls = len(turn_targets)
    return BaselineCaseResult(
        id=case_id,
        label=label,
        calls=calls,
        input_tokens_est=input_total,
        ttft_ms=min(per_turn_ttft_ms) if per_turn_ttft_ms else None,
        prompt_tokens=prompt_total,
        completion_tokens=completion_total,
        total_tokens=token_total,
        total_latency_ms=latency_total,
        avg_latency_ms=(float(latency_total) / float(calls)) if calls > 0 else 0.0,
        min_latency_ms=min(per_turn_latency_ms) if per_turn_latency_ms else None,
        max_latency_ms=max(per_turn_latency_ms) if per_turn_latency_ms else None,
        completion_time_ms=max(per_turn_latency_ms) if per_turn_latency_ms else None,
        per_turn_latency_ms=per_turn_latency_ms,
        per_turn_ttft_ms=per_turn_ttft_ms,
        per_turn_prompt_tokens=per_turn_prompt_tokens,
        per_turn_completion_tokens=per_turn_completion_tokens,
    )


async def _run_multi_turn_case_end_to_end(
    case_id: str,
    label: str,
    task_instruction: str,
    turn_targets: list[int],
    on_progress=None,
) -> BaselineCaseResult:
    conversation_id, _ = store.create_conversation(title=label)
    prompt_total = 0
    completion_total = 0
    token_total = 0
    latency_total = 0
    per_turn_latency_ms: list[int] = []
    per_turn_ttft_ms: list[int] = []
    per_turn_prompt_tokens: list[int] = []
    per_turn_completion_tokens: list[int] = []
    input_total = 0

    for idx, target in enumerate(turn_targets):
        step = f"{label} (turn {idx + 1}/{len(turn_targets)})"
        if on_progress is not None:
            on_progress(step, 0)
        user_payload = _build_user_payload(target, f"{label} turn {idx + 1}")
        input_total += _estimate_tokens_for_text(user_payload)
        user_event = _enqueue_chat_message(conversation_id, f"{task_instruction}\n\nTurn {idx + 1}:\n{user_payload}")
        ttft_ms, latency_ms = await _await_end_to_end_turn(conversation_id, user_event.id)
        perf = await _await_performance_exchange(user_event.id)
        prompt_tokens = perf.prompt_tokens or 0
        completion_tokens = perf.completion_tokens or 0
        total_tokens = perf.total_tokens or (prompt_tokens + completion_tokens)
        latency_total += latency_ms
        per_turn_latency_ms.append(latency_ms)
        per_turn_ttft_ms.append(ttft_ms if ttft_ms is not None else latency_ms)
        per_turn_prompt_tokens.append(prompt_tokens)
        per_turn_completion_tokens.append(completion_tokens)
        prompt_total += prompt_tokens
        completion_total += completion_tokens
        token_total += total_tokens
        if on_progress is not None:
            on_progress(step, 1)

    calls = len(turn_targets)
    return BaselineCaseResult(
        id=case_id,
        label=label,
        calls=calls,
        input_tokens_est=input_total,
        ttft_ms=min(per_turn_ttft_ms) if per_turn_ttft_ms else None,
        prompt_tokens=prompt_total,
        completion_tokens=completion_total,
        total_tokens=token_total,
        total_latency_ms=latency_total,
        avg_latency_ms=(float(latency_total) / float(calls)) if calls > 0 else 0.0,
        min_latency_ms=min(per_turn_latency_ms) if per_turn_latency_ms else None,
        max_latency_ms=max(per_turn_latency_ms) if per_turn_latency_ms else None,
        completion_time_ms=max(per_turn_latency_ms) if per_turn_latency_ms else None,
        per_turn_latency_ms=per_turn_latency_ms,
        per_turn_ttft_ms=per_turn_ttft_ms,
        per_turn_prompt_tokens=per_turn_prompt_tokens,
        per_turn_completion_tokens=per_turn_completion_tokens,
    )


def _make_baseline_status(job_id: str) -> BaselineJobStatusResponse:
    job = _baseline_jobs[job_id]
    return BaselineJobStatusResponse(
        job_id=job_id,
        status=job["status"],
        model=job["model"],
        total_calls=job["total_calls"],
        completed_calls=job["completed_calls"],
        current_step=job.get("current_step"),
        started_at=job["started_at"],
        updated_at=job["updated_at"],
        completed_at=job.get("completed_at"),
        duration_ms=job.get("duration_ms"),
        events=job.get("events", []),
        error=job.get("error"),
        result=job.get("result"),
    )


def _append_baseline_event(job_id: str, message: str) -> None:
    job = _baseline_jobs[job_id]
    events = job.setdefault("events", [])
    events.append(message)
    if len(events) > 100:
        del events[:-100]
    job["updated_at"] = datetime.now(timezone.utc)


def _baseline_progress(job_id: str, step: str, completed_inc: int) -> None:
    job = _baseline_jobs[job_id]
    job["current_step"] = step
    if completed_inc > 0:
        job["completed_calls"] = min(job["total_calls"], int(job["completed_calls"]) + completed_inc)
        _append_baseline_event(job_id, f"Completed: {step}")
    else:
        _append_baseline_event(job_id, f"Running: {step}")
    job["updated_at"] = datetime.now(timezone.utc)


def _baseline_total_calls() -> int:
    # simple QA 3 + summarization 4 + multi-turn 20 + extraction 1 + system prompt pressure 6
    return 34


async def _execute_baseline(job_id: str, enforce_max_response_tokens: bool, mode: str = "direct_model") -> BaselineRunResponse:
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    _, effective_components = _effective_prompt_components()
    effective_prompt = compose_system_prompt(effective_components)
    context_settings = _get_context_settings()
    baseline_max_tokens = context_settings.max_response_tokens if enforce_max_response_tokens else None
    if mode == "end_to_end_aigentos":
        simple_qa_cases = [
            await _run_single_turn_case_end_to_end(
                case_id="qa_100",
                label="Simple Q/A (100 user tokens)",
                task_instruction="Answer directly in 6-10 concise sentences.",
                input_tokens=100,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
            await _run_single_turn_case_end_to_end(
                case_id="qa_250",
                label="Simple Q/A (250 user tokens)",
                task_instruction="Answer directly in 6-10 concise sentences.",
                input_tokens=250,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
            await _run_single_turn_case_end_to_end(
                case_id="qa_500",
                label="Simple Q/A (500 user tokens)",
                task_instruction="Answer directly in 6-10 concise sentences.",
                input_tokens=500,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
        ]

        summarization_cases = [
            await _run_single_turn_case_end_to_end(
                case_id="sum_200",
                label="Summarization (200 user tokens)",
                task_instruction="Summarize the content in 5 bullet points.",
                input_tokens=200,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
            await _run_single_turn_case_end_to_end(
                case_id="sum_500",
                label="Summarization (500 user tokens)",
                task_instruction="Summarize the content in 5 bullet points.",
                input_tokens=500,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
            await _run_single_turn_case_end_to_end(
                case_id="sum_1000",
                label="Summarization (1000 user tokens)",
                task_instruction="Summarize the content in 8 bullet points.",
                input_tokens=1000,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
            await _run_single_turn_case_end_to_end(
                case_id="sum_2000",
                label="Summarization (2000 user tokens)",
                task_instruction="Summarize the content in 10 bullet points.",
                input_tokens=2000,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
        ]

        multi_turn_targets = [50 + ((i * 17) % 151) for i in range(20)]
        multi_turn_cases = [
            await _run_multi_turn_case_end_to_end(
                case_id="mt_20x_50_200",
                label="20-turn conversation (50-200 user tokens/turn)",
                task_instruction=(
                    "Maintain consistency across turns and preserve key decisions while answering each turn concisely."
                ),
                turn_targets=multi_turn_targets,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            )
        ]

        extraction_cases = [
            await _run_single_turn_case_end_to_end(
                case_id="extract_400",
                label="Structured Extraction (400 user tokens)",
                task_instruction=(
                    "Extract entities into JSON with keys: people, organizations, dates, locations, and actions."
                ),
                input_tokens=400,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            )
        ]
    else:
        simple_qa_cases = [
            await _run_single_turn_case(
                effective_prompt,
                case_id="qa_100",
                label="Simple Q/A (100 user tokens)",
                task_instruction="Answer directly in 6-10 concise sentences.",
                input_tokens=100,
                max_response_tokens=baseline_max_tokens,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
            await _run_single_turn_case(
                effective_prompt,
                case_id="qa_250",
                label="Simple Q/A (250 user tokens)",
                task_instruction="Answer directly in 6-10 concise sentences.",
                input_tokens=250,
                max_response_tokens=baseline_max_tokens,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
            await _run_single_turn_case(
                effective_prompt,
                case_id="qa_500",
                label="Simple Q/A (500 user tokens)",
                task_instruction="Answer directly in 6-10 concise sentences.",
                input_tokens=500,
                max_response_tokens=baseline_max_tokens,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
        ]

        summarization_cases = [
            await _run_single_turn_case(
                effective_prompt,
                case_id="sum_200",
                label="Summarization (200 user tokens)",
                task_instruction="Summarize the content in 5 bullet points.",
                input_tokens=200,
                max_response_tokens=baseline_max_tokens,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
            await _run_single_turn_case(
                effective_prompt,
                case_id="sum_500",
                label="Summarization (500 user tokens)",
                task_instruction="Summarize the content in 5 bullet points.",
                input_tokens=500,
                max_response_tokens=baseline_max_tokens,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
            await _run_single_turn_case(
                effective_prompt,
                case_id="sum_1000",
                label="Summarization (1000 user tokens)",
                task_instruction="Summarize the content in 8 bullet points.",
                input_tokens=1000,
                max_response_tokens=baseline_max_tokens,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
            await _run_single_turn_case(
                effective_prompt,
                case_id="sum_2000",
                label="Summarization (2000 user tokens)",
                task_instruction="Summarize the content in 10 bullet points.",
                input_tokens=2000,
                max_response_tokens=baseline_max_tokens,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            ),
        ]

        multi_turn_targets = [50 + ((i * 17) % 151) for i in range(20)]
        multi_turn_cases = [
            await _run_multi_turn_case(
                effective_prompt,
                case_id="mt_20x_50_200",
                label="20-turn conversation (50-200 user tokens/turn)",
                task_instruction=(
                    "Maintain consistency across turns and preserve key decisions while answering each turn concisely."
                ),
                turn_targets=multi_turn_targets,
                max_response_tokens=baseline_max_tokens,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            )
        ]

        extraction_cases = [
            await _run_single_turn_case(
                effective_prompt,
                case_id="extract_400",
                label="Structured Extraction (400 user tokens)",
                task_instruction=(
                    "Extract entities into JSON with keys: people, organizations, dates, locations, and actions."
                ),
                input_tokens=400,
                max_response_tokens=baseline_max_tokens,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            )
        ]

    system_prompt_targets = [200, 500, 1000, 2000, 5000, 10000]
    system_prompt_cases: list[BaselineCaseResult] = []
    if mode == "end_to_end_aigentos":
        _append_baseline_event(job_id, "System Prompt Pressure uses direct model mode in 0.2.4-oss")
    for idx, target in enumerate(system_prompt_targets):
        user_tokens = 100 + ((idx * 37) % 201)
        system_prompt_cases.append(
            await _run_system_prompt_pressure_case(
                effective_prompt,
                case_id=f"sys_{target}",
                label=f"System Prompt Pressure ({target} system tokens)",
                system_tokens=target,
                user_tokens=user_tokens,
                max_response_tokens=baseline_max_tokens,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            )
        )

    categories = [
        BaselineCategoryResult(id="simple_qa", label="Simple Q/A", cases=simple_qa_cases),
        BaselineCategoryResult(id="summarization", label="Summarization Tasks", cases=summarization_cases),
        BaselineCategoryResult(id="multi_turn", label="20-Turn Conversation", cases=multi_turn_cases),
        BaselineCategoryResult(id="structured_extraction", label="Structured Extraction (Extra)", cases=extraction_cases),
        BaselineCategoryResult(id="system_prompt_pressure", label="System Prompt Pressure", cases=system_prompt_cases),
    ]

    completed_at = datetime.now(timezone.utc)
    duration_ms = int((time.perf_counter() - started) * 1000)
    total_calls = sum(case.calls for category in categories for case in category.cases)
    return BaselineRunResponse(
        model=settings.ollama_model,
        mode=mode,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        total_calls=total_calls,
        categories=categories,
    )


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        tenant_id=settings.aigent_tenant_id,
        model=settings.ollama_model,
        ollama_base_url=settings.ollama_base_url,
        embedding_base_url=settings.embedding_base_url,
        is_warm=_warmup_completed_at is not None,
    )


@app.get("/api/worker/health")
async def worker_health() -> dict:
    last_seen = store.get_worker_heartbeat()
    if last_seen is None:
        return {"status": "unknown", "last_seen": None, "message": "Worker has not reported in yet"}
    age_seconds = (datetime.now(timezone.utc) - last_seen).total_seconds()
    healthy = age_seconds < 30
    return {
        "status": "healthy" if healthy else "stale",
        "last_seen": last_seen.isoformat(),
        "age_seconds": round(age_seconds, 1),
    }


@app.post("/api/llm/warmup", response_model=WarmupResponse)
async def llm_warmup() -> WarmupResponse:
    global _warmup_completed_at
    started = time.perf_counter()
    async with _warmup_lock:
        status_text = "warmed"
        if _warmup_completed_at is not None:
            status_text = "already_warmed"
        else:
            try:
                await llm_client.chat(
                    [
                        ChatMessageIn(
                            role="user",
                            content="hello",
                        )
                    ],
                    max_tokens=min(64, settings.ollama_max_response_tokens),
                )
                _warmup_completed_at = datetime.now(timezone.utc)
            except httpx.HTTPStatusError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Ollama warmup failed with status {exc.response.status_code}",
                ) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail="Failed to connect to Ollama for warmup") from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    return WarmupResponse(
        ok=True,
        status=status_text,
        latency_ms=latency_ms,
        model=settings.ollama_model,
        warmed_at=_warmup_completed_at or datetime.now(timezone.utc),
    )


@app.post("/api/admin/delete-all-data", response_model=DeleteAllDataResponse)
async def delete_all_data(payload: DeleteAllDataRequest) -> DeleteAllDataResponse:
    global _warmup_completed_at
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")
    store.delete_all_data()
    _warmup_completed_at = None
    return DeleteAllDataResponse(ok=True, deleted_at=datetime.now(timezone.utc))


@app.get("/api/admin/export")
async def export_all_data() -> dict:
    snapshot = store.export_all_data(_tenant_id())
    return {
        "version": "aigentos-export-v1",
        "model": settings.ollama_model,
        "ollama_base_url": settings.ollama_base_url,
        "embedding_base_url": settings.embedding_base_url,
        "data": snapshot,
    }


@app.get("/api/prompts/context-settings", response_model=ContextSettingsResponse)
async def get_context_settings() -> ContextSettingsResponse:
    current = _get_context_settings()
    return ContextSettingsResponse(
        max_context_tokens=current.max_context_tokens,
        max_response_tokens=current.max_response_tokens,
        compact_trigger_pct=current.compact_trigger_pct,
        compact_instructions=current.compact_instructions,
        memory_enabled=current.memory_enabled,
        updated_at=current.updated_at,
    )


@app.patch("/api/prompts/context-settings", response_model=ContextSettingsResponse)
async def update_context_settings(payload: ContextSettingsUpdateRequest) -> ContextSettingsResponse:
    current = store.update_context_settings(
        tenant_id=_tenant_id(),
        max_context_tokens=settings.ollama_context_window,
        max_response_tokens=payload.max_response_tokens,
        compact_trigger_pct=payload.compact_trigger_pct,
        compact_instructions=payload.compact_instructions,
        memory_enabled=payload.memory_enabled,
    )
    return ContextSettingsResponse(
        max_context_tokens=current.max_context_tokens,
        max_response_tokens=current.max_response_tokens,
        compact_trigger_pct=current.compact_trigger_pct,
        compact_instructions=current.compact_instructions,
        memory_enabled=current.memory_enabled,
        updated_at=current.updated_at,
    )


@app.get("/api/memory/chunks", response_model=MemoryChunkListResponse)
async def list_memory_chunks(limit: int = 200) -> MemoryChunkListResponse:
    safe_limit = max(1, min(limit, 1000))
    context = _get_context_settings()
    chunks = store.list_rag_chunks(limit=safe_limit)
    return MemoryChunkListResponse(
        memory_enabled=context.memory_enabled,
        chunks=[
            MemoryChunkResponse(
                id=chunk.id,
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                content=chunk.content,
                created_at=chunk.created_at,
                embedding_dimensions=len(chunk.embedding),
                content_tokens_est=_estimate_tokens_for_text(chunk.content),
            )
            for chunk in chunks
        ],
    )


@app.delete("/api/memory/chunks/{chunk_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory_chunk(chunk_id: str) -> Response:
    ok = store.delete_rag_chunk(chunk_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory chunk not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/prompts/system", response_model=SystemPromptResponse)
async def get_system_prompt() -> SystemPromptResponse:
    profile, components = _effective_prompt_components()
    bundle = load_prompt_bundle(repo_root=repo_root, default_agent_id=settings.default_agent_id)
    prompt = compose_system_prompt(components)
    overrides = store.get_prompt_overrides(profile.id)
    return SystemPromptResponse(
        agent_id=bundle.agent_id,
        prompt=prompt,
        component_count=len(components),
        profile_name=profile.name,
        is_custom=len(overrides) > 0,
    )


@app.get("/api/prompts/components", response_model=list[PromptComponentResponse])
async def get_prompt_components() -> list[PromptComponentResponse]:
    profile, components = _effective_prompt_components()
    overrides = store.get_prompt_overrides(profile.id)
    return [
            PromptComponentResponse(
                id=item.id,
                name=item.name,
                file_path=item.file_path,
                content=item.content,
                order=item.order,
                enabled=item.enabled,
                is_system=item.is_system,
            is_custom=item.id in overrides,
        )
        for item in components
    ]


@app.get("/api/prompts/profiles", response_model=list[PromptProfileResponse])
async def get_prompt_profiles() -> list[PromptProfileResponse]:
    profiles = store.list_prompt_profiles(_tenant_id())
    return [
        PromptProfileResponse(
            id=item.id,
            name=item.name,
            is_active=item.is_active,
            is_default=item.is_default,
        )
        for item in profiles
    ]


@app.post("/api/prompts/profiles", response_model=PromptProfileResponse)
async def create_prompt_profile(payload: PromptProfileCreateRequest) -> PromptProfileResponse:
    created = store.create_prompt_profile(_tenant_id(), payload.name)
    return PromptProfileResponse(
        id=created.id,
        name=created.name,
        is_active=created.is_active,
        is_default=created.is_default,
    )


@app.post("/api/prompts/profiles/{profile_id}/activate", response_model=PromptProfileResponse)
async def activate_prompt_profile(profile_id: str) -> PromptProfileResponse:
    ok = store.activate_prompt_profile(_tenant_id(), profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Prompt profile not found")
    active = store.get_active_prompt_profile(_tenant_id())
    return PromptProfileResponse(
        id=active.id,
        name=active.name,
        is_active=active.is_active,
        is_default=active.is_default,
    )


@app.patch("/api/prompts/components/{component_id}", response_model=PromptComponentResponse)
async def update_prompt_component(component_id: str, payload: PromptComponentUpdateRequest) -> PromptComponentResponse:
    default_ids = {c.id for c in load_prompt_components(repo_root=repo_root)}
    if component_id not in default_ids:
        raise HTTPException(status_code=404, detail="Prompt component not found")
    profile = store.get_active_prompt_profile(_tenant_id())
    store.upsert_prompt_override(
        profile_id=profile.id,
        component_id=component_id,
        content=payload.content,
        enabled=payload.enabled,
    )
    _, components = _effective_prompt_components()
    component = next((item for item in components if item.id == component_id), None)
    if component is None:
        raise HTTPException(status_code=404, detail="Prompt component not found")
    return PromptComponentResponse(
        id=component.id,
        name=component.name,
        file_path=component.file_path,
        content=component.content,
        order=component.order,
        enabled=component.enabled,
        is_system=component.is_system,
        is_custom=True,
    )


@app.post("/api/prompts/reset", response_model=PromptResetResponse)
async def reset_prompts() -> PromptResetResponse:
    profile = store.get_active_prompt_profile(_tenant_id())
    store.reset_prompt_profile(profile.id)
    return PromptResetResponse(ok=True, profile_id=profile.id, profile_name=profile.name)


@app.post("/api/conversations", response_model=ConversationDetail)
async def create_conversation(payload: CreateConversationRequest) -> ConversationDetail:
    conversation_id, updated_at = store.create_conversation(title=payload.title)
    return ConversationDetail(
        id=conversation_id,
        title=payload.title or "New Conversation",
        updated_at=updated_at,
        messages=[],
    )


@app.get("/api/conversations", response_model=list[ConversationSummary])
async def list_conversations() -> list[ConversationSummary]:
    conversations = store.list_conversations()
    return [
        ConversationSummary(
            id=item.id,
            title=item.title,
            last_message=item.last_message,
            updated_at=item.updated_at,
            message_count=item.message_count,
        )
        for item in conversations
    ]


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str) -> ConversationDetail:
    detail = store.get_conversation_detail(conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    title, updated_at, messages = detail
    return ConversationDetail(
        id=conversation_id,
        title=title,
        updated_at=updated_at,
        messages=[MessageResponse(id=m.id, role=m.role, content=m.content, timestamp=m.created_at) for m in messages],  # type: ignore[list-item]
    )


@app.get("/api/conversations/{conversation_id}/events", response_model=ConversationEventsResponse)
async def get_conversation_events(conversation_id: str) -> ConversationEventsResponse:
    detail = store.get_conversation_detail(conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    title, updated_at, _ = detail
    events = store.get_conversation_events(conversation_id)
    return ConversationEventsResponse(
        id=conversation_id,
        title=title,
        updated_at=updated_at,
        events=[_event_to_response(event) for event in events],
    )


@app.get("/api/conversations/{conversation_id}/stream")
async def stream_conversation_events(conversation_id: str) -> StreamingResponse:
    if not store.ensure_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def event_generator():
        last_payload = ""
        idle_pings = 0
        max_idle_pings = 1500  # ~5 minutes at 0.2s intervals
        try:
            while idle_pings < max_idle_pings:
                payload = _conversation_events_payload(conversation_id)
                if payload is None:
                    yield "event: error\ndata: {\"detail\":\"Conversation not found\"}\n\n"
                    return
                serialized = json.dumps(payload)
                if serialized != last_payload:
                    last_payload = serialized
                    idle_pings = 0
                    yield f"event: conversation\ndata: {serialized}\n\n"
                else:
                    idle_pings += 1
                    yield "event: ping\ndata: {}\n\n"
                await asyncio.sleep(0.2)
            yield "event: timeout\ndata: {\"detail\":\"Stream idle timeout\"}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/api/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str) -> Response:
    deleted = store.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/performance/recent", response_model=list[PerformanceExchange])
async def recent_performance(limit: int = 5) -> list[PerformanceExchange]:
    safe_limit = max(1, min(limit, 50))
    rows = store.list_recent_performance_exchanges(safe_limit)
    return [
        PerformanceExchange(
            id=row.id,
            conversation_id=row.conversation_id,
            created_at=row.created_at,
            user_preview=row.user_preview,
            assistant_preview=row.assistant_preview,
            metrics=PerformanceMetrics(
                total_latency_ms=row.total_latency_ms,
                llm_latency_ms=row.llm_latency_ms,
                ttft_ms=row.ttft_ms,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                total_tokens=row.total_tokens,
                prompt_breakdown=PromptBreakdown(
                    system_chars=row.system_chars,
                    user_chars=row.user_chars,
                    assistant_chars=row.assistant_chars,
                    system_tokens_est=row.system_tokens_est,
                    user_tokens_est=row.user_tokens_est,
                    assistant_tokens_est=row.assistant_tokens_est,
                ),
            ),
        )
        for row in rows
    ]


@app.get("/api/performance/summary", response_model=PerformanceSummaryResponse)
async def performance_summary() -> PerformanceSummaryResponse:
    summary = store.summarize_performance()
    return PerformanceSummaryResponse(
        exchange_count=summary["exchange_count"],
        latency_min_ms=summary["latency_min_ms"],
        latency_max_ms=summary["latency_max_ms"],
        latency_avg_ms=summary["latency_avg_ms"],
        tokens_day=_window_stats(summary["tokens_day"]),
        tokens_week=_window_stats(summary["tokens_week"]),
        tokens_month=_window_stats(summary["tokens_month"]),
        tokens_all_time=_window_stats(summary["tokens_all_time"]),
    )


async def _run_baseline_background(job_id: str) -> None:
    try:
        job = _baseline_jobs[job_id]
        result = await _execute_baseline(
            job_id,
            enforce_max_response_tokens=bool(job.get("enforce_max_response_tokens", True)),
            mode=str(job.get("mode", "direct_model")),
        )
        job = _baseline_jobs[job_id]
        job["status"] = "completed"
        job["result"] = result
        job["completed_at"] = datetime.now(timezone.utc)
        job["duration_ms"] = result.duration_ms
        job["current_step"] = "Completed"
        job["updated_at"] = datetime.now(timezone.utc)
        _append_baseline_event(job_id, "Baseline run completed")
    except Exception as exc:
        job = _baseline_jobs[job_id]
        job["status"] = "failed"
        msg = str(exc).strip()
        job["error"] = f"{exc.__class__.__name__}: {msg}" if msg else exc.__class__.__name__
        job["completed_at"] = datetime.now(timezone.utc)
        job["updated_at"] = datetime.now(timezone.utc)
        _append_baseline_event(job_id, f"Baseline failed: {job['error']}")


@app.post("/api/baseline/start", response_model=BaselineJobStartResponse)
async def start_baseline(payload: BaselineStartRequest | None = None) -> BaselineJobStartResponse:
    payload = payload or BaselineStartRequest()
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    _baseline_jobs[job_id] = {
        "status": "running",
        "model": settings.ollama_model,
        "total_calls": _baseline_total_calls(),
        "completed_calls": 0,
        "current_step": "Initializing",
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "duration_ms": None,
        "events": ["Baseline run started"],
        "error": None,
        "result": None,
        "enforce_max_response_tokens": payload.enforce_max_response_tokens,
        "mode": payload.mode,
    }
    _append_baseline_event(
        job_id,
        f"Baseline mode: {'End-to-end AIgentOS' if payload.mode == 'end_to_end_aigentos' else 'Direct model'}",
    )
    if payload.enforce_max_response_tokens:
        _append_baseline_event(job_id, "Mode: enforcing max response tokens")
    else:
        _append_baseline_event(job_id, "Mode: no max response token cap")
    asyncio.create_task(_run_baseline_background(job_id))
    return BaselineJobStartResponse(job_id=job_id, status="running")


@app.get("/api/baseline/status/{job_id}", response_model=BaselineJobStatusResponse)
async def baseline_status(job_id: str) -> BaselineJobStatusResponse:
    if job_id not in _baseline_jobs:
        raise HTTPException(status_code=404, detail="Baseline job not found")
    return _make_baseline_status(job_id)


@app.post("/api/baseline/run", response_model=BaselineRunResponse)
async def run_baseline(payload: BaselineStartRequest | None = None) -> BaselineRunResponse:
    payload = payload or BaselineStartRequest()
    job_id = f"direct-{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    _baseline_jobs[job_id] = {
        "status": "running",
        "model": settings.ollama_model,
        "total_calls": _baseline_total_calls(),
        "completed_calls": 0,
        "current_step": "Initializing",
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "duration_ms": None,
        "events": ["Baseline run started (direct)"],
        "error": None,
        "result": None,
        "enforce_max_response_tokens": payload.enforce_max_response_tokens,
        "mode": payload.mode,
    }
    _append_baseline_event(
        job_id,
        f"Baseline mode: {'End-to-end AIgentOS' if payload.mode == 'end_to_end_aigentos' else 'Direct model'}",
    )
    if payload.enforce_max_response_tokens:
        _append_baseline_event(job_id, "Mode: enforcing max response tokens")
    else:
        _append_baseline_event(job_id, "Mode: no max response token cap")
    result = await _execute_baseline(
        job_id,
        enforce_max_response_tokens=payload.enforce_max_response_tokens,
        mode=payload.mode,
    )
    _baseline_jobs[job_id]["status"] = "completed"
    _baseline_jobs[job_id]["result"] = result
    _baseline_jobs[job_id]["completed_at"] = datetime.now(timezone.utc)
    _baseline_jobs[job_id]["duration_ms"] = result.duration_ms
    _baseline_jobs[job_id]["updated_at"] = datetime.now(timezone.utc)
    return result


@app.get("/api/debug/logs", response_model=list[DebugLogResponse])
async def debug_logs(limit: int = 50) -> list[DebugLogResponse]:
    safe_limit = max(1, min(limit, 200))
    rows = store.list_recent_performance_exchanges(safe_limit)
    result: list[DebugLogResponse] = []
    for row in rows:
        result.append(
            DebugLogResponse(
                id=row.id,
                log_type="llm_exchange",
                duration_ms=row.total_latency_ms,
                token_count=row.total_tokens,
                created_at=row.created_at,
                content={
                    "conversation_id": row.conversation_id,
                    "user_preview": row.user_preview,
                    "assistant_preview": row.assistant_preview,
                    "metrics": {
                        "total_latency_ms": row.total_latency_ms,
                        "llm_latency_ms": row.llm_latency_ms,
                        "ttft_ms": row.ttft_ms,
                        "prompt_tokens": row.prompt_tokens,
                        "completion_tokens": row.completion_tokens,
                        "total_tokens": row.total_tokens,
                        "retrieved_chunk_count": row.retrieved_chunk_count,
                        "retrieved_chunks": [
                            {
                                "content": chunk.content,
                                "score": chunk.score,
                                "source_id": chunk.source_id,
                                "source_type": chunk.source_type,
                                "source_preview": chunk.source_preview,
                            }
                            for chunk in row.retrieved_chunks
                        ],
                        "prompt_breakdown": {
                            "system_chars": row.system_chars,
                            "user_chars": row.user_chars,
                            "assistant_chars": row.assistant_chars,
                            "system_tokens_est": row.system_tokens_est,
                            "user_tokens_est": row.user_tokens_est,
                            "assistant_tokens_est": row.assistant_tokens_est,
                        },
                    },
                },
            )
        )
    return result


_allocate_estimated_tokens = allocate_estimated_tokens


@app.post("/api/chat", response_model=ChatAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def chat(payload: ChatRequest) -> ChatAcceptedResponse:
    if payload.conversation_id:
        if not store.ensure_conversation(payload.conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        conversation_id = payload.conversation_id
    else:
        conversation_id, _ = store.create_conversation()

    user_event = store.create_interaction_event(
        conversation_id=conversation_id,
        role="user",
        content=payload.message,
        status="pending",
    )
    store.maybe_set_title_from_message(conversation_id, payload.message)
    return ChatAcceptedResponse(
        conversation_id=conversation_id,
        event_id=user_event.id,
        accepted_at=user_event.created_at,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
