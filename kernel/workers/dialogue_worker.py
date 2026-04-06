from __future__ import annotations

import asyncio
import time
from pathlib import Path
import uuid

import httpx

from kernel.api.llm import ChatMessageIn, OllamaClient, OllamaEmbeddingClient
from kernel.api.prompts import compose_system_prompt, load_prompt_components
from kernel.api.settings import get_settings
from kernel.api.storage import ChatStore, StoredInteractionEvent, StoredRetrievedChunk
from kernel.shared.text import chunk_text, cosine_similarity, preview_text
from kernel.shared.metrics import estimate_tokens_for_messages, allocate_estimated_tokens


settings = get_settings()
repo_root = Path(__file__).resolve().parents[2]
store = ChatStore(settings.chat_db_path)
llm_client = OllamaClient(settings.ollama_base_url, settings.ollama_model)
embedding_client = OllamaEmbeddingClient(settings.embedding_base_url, settings.embedding_model)


def _effective_prompt() -> str:
    profile = store.get_active_prompt_profile(settings.aigent_tenant_id)
    defaults = load_prompt_components(repo_root=repo_root)
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
    return compose_system_prompt(merged)


async def _store_chunks_for_source(source_id: str, content: str) -> None:
    chunks = chunk_text(content)
    if not chunks:
        return
    embedded_chunks: list[tuple[str, list[float]]] = []
    for chunk in chunks:
        try:
            embedding = await embedding_client.embed(chunk)
        except Exception:
            continue  # skip failed chunks, store the rest
        embedded_chunks.append((chunk, embedding))
    if embedded_chunks:
        store.upsert_rag_chunks("interaction_event", source_id, embedded_chunks)


async def _summarize_memory_chunks(chunks: list[str]) -> str:
    joined = "\n".join(f"- {chunk}" for chunk in chunks if chunk.strip()).strip()
    if not joined:
        return ""
    if len(joined) > 6000:
        joined = joined[:6000]
    try:
        result = await llm_client.chat(
            [
                ChatMessageIn(
                    role="system",
                    content=(
                        "Summarize older conversational memory into compact reusable notes. "
                        "Preserve user goals, stable facts, preferences, decisions, unresolved questions, and important corrections. "
                        "Use concise bullet points. Do not invent facts."
                    ),
                ),
                ChatMessageIn(
                    role="user",
                    content=f"Summarize these old memory chunks into concise reusable memory:\n{joined}",
                ),
            ],
            max_tokens=min(256, settings.ollama_max_response_tokens),
        )
        return result.content.strip()
    except Exception:
        previews = [f"- {preview_text(chunk, max_chars=180)}" for chunk in chunks if chunk.strip()]
        return "\n".join(previews[:12]).strip()


async def _compact_memory_if_needed() -> None:
    total_chunks = store.count_rag_chunks()
    if total_chunks <= settings.memory_chunk_limit:
        return

    overflow = total_chunks - settings.memory_chunk_limit
    batch_size = max(settings.memory_compaction_batch_size, overflow)
    oldest = store.list_oldest_rag_chunks(limit=batch_size)
    if len(oldest) < 2:
        return

    summary_text = await _summarize_memory_chunks([chunk.content for chunk in oldest])
    if not summary_text:
        return

    summary_source_id = str(uuid.uuid4())
    stored = await _store_summary_chunks(summary_source_id, summary_text)
    if not stored:
        return
    store.delete_rag_chunks([chunk.id for chunk in oldest])


async def _store_summary_chunks(source_id: str, content: str) -> bool:
    chunks = chunk_text(content)
    if not chunks:
        return False
    embedded_chunks: list[tuple[str, list[float]]] = []
    for chunk in chunks:
        try:
            embedding = await embedding_client.embed(chunk)
        except Exception:
            continue  # skip failed chunks, store the rest
        embedded_chunks.append((chunk, embedding))
    if not embedded_chunks:
        return False
    store.upsert_rag_chunks("memory_summary", source_id, embedded_chunks)
    return True


async def _retrieve_context_chunks(query: str, exclude_source_id: str, limit: int = 5) -> list[StoredRetrievedChunk]:
    try:
        query_embedding = await embedding_client.embed(query)
    except Exception:
        return []
    scored: list[StoredRetrievedChunk] = []
    for chunk in store.iter_rag_chunks():
        if chunk.source_id == exclude_source_id:
            continue
        score = cosine_similarity(query_embedding, chunk.embedding)
        if score <= 0:
            continue
        source_event = store.get_interaction_event(chunk.source_id)
        source_preview = preview_text(source_event.content if source_event is not None else chunk.content)
        scored.append(
            StoredRetrievedChunk(
                content=chunk.content,
                score=score,
                source_id=chunk.source_id,
                source_type=chunk.source_type,
                source_preview=source_preview,
            )
        )
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]


def _conversation_history_messages(conversation_id: str, current_event_id: str) -> list[ChatMessageIn]:
    events = store.get_conversation_events(conversation_id)
    history: list[ChatMessageIn] = []
    for event in events:
        if event.id == current_event_id:
            continue
        if event.role not in {"user", "assistant"}:
            continue
        if event.status != "completed":
            continue
        history.append(ChatMessageIn(role=event.role, content=event.content))
    return history


def _apply_context_window(messages: list[ChatMessageIn], max_context_tokens: int, compact_instructions: str, compact_trigger_pct: float) -> list[ChatMessageIn]:
    est_prompt_tokens_before = estimate_tokens_for_messages(messages)
    compact_threshold = int(max_context_tokens * compact_trigger_pct)
    result = list(messages)
    if compact_instructions.strip() and est_prompt_tokens_before >= compact_threshold:
        result.insert(1, ChatMessageIn(role="system", content=compact_instructions.strip()))
    while len(result) > 3 and estimate_tokens_for_messages(result) > max_context_tokens:
        result.pop(2)
    return result


async def _process_event(event: StoredInteractionEvent) -> None:
    context_settings = store.ensure_context_settings(
        settings.aigent_tenant_id,
        settings.ollama_context_window,
        settings.ollama_max_response_tokens,
        0.9,
    )
    effective_prompt = _effective_prompt()
    retrieved_chunks = (
        await _retrieve_context_chunks(event.content, exclude_source_id=event.id, limit=5)
        if context_settings.memory_enabled
        else []
    )
    history_messages = _conversation_history_messages(event.conversation_id, event.id)

    llm_messages: list[ChatMessageIn] = [ChatMessageIn(role="system", content=effective_prompt)]
    if retrieved_chunks:
        llm_messages.append(
            ChatMessageIn(
                role="system",
                content="Relevant remembered context:\n" + "\n".join(f"- {chunk.content}" for chunk in retrieved_chunks),
            )
        )
    llm_messages.extend(history_messages)
    llm_messages.append(ChatMessageIn(role="user", content=event.content))
    llm_messages = _apply_context_window(
        llm_messages,
        context_settings.max_context_tokens,
        context_settings.compact_instructions,
        context_settings.compact_trigger_pct,
    )

    system_chars = sum(len(m.content) for m in llm_messages if m.role == "system")
    user_chars = sum(len(m.content) for m in llm_messages if m.role == "user")
    assistant_chars = sum(len(m.content) for m in llm_messages if m.role == "assistant")

    assistant_event = store.create_interaction_event(
        conversation_id=event.conversation_id,
        role="assistant",
        content="",
        status="processing",
        causation_event_id=event.id,
    )

    async def _handle_chunk(_chunk: str, accumulated: str) -> None:
        store.update_interaction_event_content(assistant_event.id, accumulated)

    started = time.perf_counter()
    try:
        completion = await llm_client.chat(
            llm_messages,
            max_tokens=context_settings.max_response_tokens,
            on_chunk=_handle_chunk,
        )
    except Exception as exc:
        current_events = store.get_conversation_events(event.conversation_id)
        current_assistant = next((item for item in current_events if item.id == assistant_event.id), None)
        partial_content = current_assistant.content if current_assistant is not None else ""
        store.mark_event_failed_with_content(assistant_event.id, partial_content, f"{exc.__class__.__name__}: {exc}")
        raise
    total_latency_ms = int((time.perf_counter() - started) * 1000)

    store.mark_event_completed_with_content(assistant_event.id, completion.content)
    if context_settings.memory_enabled:
        await _store_chunks_for_source(event.id, event.content)
        await _store_chunks_for_source(assistant_event.id, completion.content)
        await _compact_memory_if_needed()
    store.mark_event_completed(event.id)

    system_tokens_est, user_tokens_est, assistant_tokens_est = allocate_estimated_tokens(
        completion.prompt_tokens,
        system_chars,
        user_chars,
        assistant_chars,
    )
    store.add_performance_exchange(
        conversation_id=event.conversation_id,
        user_preview=event.content.strip()[:160],
        assistant_preview=completion.content.strip()[:160],
        total_latency_ms=total_latency_ms,
        llm_latency_ms=completion.latency_ms,
        ttft_ms=completion.ttft_ms,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        total_tokens=completion.total_tokens,
        retrieved_chunks=retrieved_chunks,
        system_chars=system_chars,
        user_chars=user_chars,
        assistant_chars=assistant_chars,
        system_tokens_est=system_tokens_est,
        user_tokens_est=user_tokens_est,
        assistant_tokens_est=assistant_tokens_est,
    )


async def run_worker() -> None:
    while True:
        store.update_worker_heartbeat()
        event = store.claim_next_pending_user_event()
        if event is None:
            await asyncio.sleep(settings.worker_poll_interval_ms / 1000.0)
            continue
        try:
            await _process_event(event)
        except httpx.HTTPStatusError as exc:
            store.mark_event_failed(event.id, f"LLM request failed with status {exc.response.status_code}")
        except httpx.HTTPError as exc:
            store.mark_event_failed(event.id, f"Failed to connect to Ollama: {exc}")
        except Exception as exc:
            store.mark_event_failed(event.id, f"{exc.__class__.__name__}: {exc}")
        await asyncio.sleep(0)


if __name__ == "__main__":
    asyncio.run(run_worker())
