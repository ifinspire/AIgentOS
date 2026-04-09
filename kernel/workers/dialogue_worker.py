from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from pathlib import Path

import httpx

from kernel.api.llm import ChatMessageIn, OllamaClient, OllamaEmbeddingClient
from kernel.api.prompts import compose_system_prompt, load_prompt_components
from kernel.api.settings import get_settings
from kernel.api.storage import ChatStore, StoredInteractionEvent, StoredRetrievedChunk
from kernel.shared.metrics import estimate_tokens_for_messages, allocate_estimated_tokens
from kernel.shared.text import cosine_similarity, preview_text


settings = get_settings()
repo_root = Path(__file__).resolve().parents[2]
store = ChatStore(settings.chat_db_path)
llm_client = OllamaClient(settings.ollama_base_url, settings.ollama_model)
embedding_client = OllamaEmbeddingClient(settings.embedding_base_url, settings.embedding_model)

_CALCULATION_DIRECT_REQUEST_RE = re.compile(r"^[0-9+\-*/().%\s]+$")
_DOCUMENT_REFERENCE_RE = re.compile(r"\b(document|attachment|file|pdf)\b", re.IGNORECASE)


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


async def _retrieve_context_chunks(
    query: str,
    exclude_source_id: str | None = None,
    limit: int = 5,
) -> list[StoredRetrievedChunk]:
    """Embed the query and return the top-N most similar memory chunks.

    RAG retrieval belongs to the dialogue worker: it runs independently of
    and in parallel with the orchestrator's tool-routing pass.
    """
    try:
        query_embedding = await embedding_client.embed(query)
    except Exception:
        return []
    scored: list[StoredRetrievedChunk] = []
    for chunk in store.iter_rag_chunks():
        if exclude_source_id and chunk.source_id == exclude_source_id:
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


def _direct_tool_response(user_message: str, tool_observations: list[dict]) -> str | None:
    if len(tool_observations) != 1:
        return None
    observation = tool_observations[0]
    tool_name = str(observation.get("tool", "")).strip().lower()
    if tool_name == "count_occurrences":
        needle = str(observation.get("needle", "")).strip()
        haystack = str(observation.get("haystack", "")).strip()
        result = str(observation.get("result", "")).strip()
        if needle and haystack and result:
            suffix = "" if result == "1" else "s"
            return f'There {"is" if result == "1" else "are"} {result} occurrence{suffix} of the letter "{needle}" in "{haystack}".'
        return None
    if tool_name == "math_subagent":
        result = str(observation.get("result", "")).strip()
        if not result:
            return None
        reference = str(observation.get("reference", "")).strip()
        unit = str(observation.get("unit", "")).strip()
        if unit:
            return f'{reference or "Computed result"}: {result} {unit}.'
        return f'{reference or "Computed result"}: {result}.'
    if tool_name != "calculate":
        return None

    normalized = " ".join((user_message or "").strip().lower().split())
    for prefix in ("what is ", "what's ", "calculate ", "compute "):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
            break
    normalized = normalized.rstrip(" ?")
    if not normalized or not _CALCULATION_DIRECT_REQUEST_RE.fullmatch(normalized):
        return None

    expression = str(observation.get("expression", normalized)).strip()
    result = str(observation.get("result", "")).strip()
    if not result:
        return None
    return f"The result of {expression} is {result}."


def _safe_local_calculation_response(user_message: str) -> str | None:
    normalized = " ".join((user_message or "").strip().lower().split())
    for prefix in ("what is ", "what's ", "calculate ", "compute "):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
            break
    normalized = normalized.rstrip(" ?")
    if not normalized or not _CALCULATION_DIRECT_REQUEST_RE.fullmatch(normalized):
        return None
    try:
        from kernel.workers.orchestrator_worker import _calculate_expression  # local import to avoid circular module load at import time
    except Exception:
        return None
    result = _calculate_expression(normalized)
    if result is None:
        return None
    return f"The result of {normalized} is {result}."


def _should_include_recent_documents(user_message: str) -> bool:
    text = (user_message or "").strip()
    if not text:
        return False
    return bool(_DOCUMENT_REFERENCE_RE.search(text)) or "this?" in text.lower() or "this one" in text.lower()


def _recent_document_reference(conversation_id: str, user_message: str) -> tuple[str | None, str | None]:
    if not _should_include_recent_documents(user_message):
        return None, None
    documents = store.list_recent_document_imports_for_conversation(conversation_id, limit=2)
    if not documents:
        return None, None

    primary = documents[0]
    if primary.status in {"pending", "processing"}:
        return (
            None,
            f'The most recent imported document "{primary.filename}" is still being processed. Wait for indexing to finish before answering questions about that document.',
        )

    lines = [f'Primary referenced document: "{primary.filename}"']
    primary_chunks = store.list_rag_chunks_for_source("document_import", primary.id, limit=2)
    for chunk in primary_chunks:
        excerpt = chunk.content.strip()
        if len(excerpt) > 260:
            excerpt = f"{excerpt[:260].rstrip()}..."
        if excerpt:
            lines.append(f"- {excerpt}")

    if len(documents) > 1:
        lines.append("Other recent imported documents:")
        for document in documents[1:]:
            lines.append(f"- {document.filename} ({document.status})")

    lines.append("If the user refers to 'this document' or 'the attachment', assume they mean the primary referenced document above unless they specify otherwise.")
    return "\n".join(lines), None


def _pending_document_response(conversation_id: str, user_message: str) -> str | None:
    _, pending_message = _recent_document_reference(conversation_id, user_message)
    return pending_message


def _recent_document_context(conversation_id: str, user_message: str) -> str | None:
    context, pending_message = _recent_document_reference(conversation_id, user_message)
    if pending_message:
        return None
    return context


def _workflow_trace(
    *,
    response_source: str,
    llm_involved: bool,
    retrieved_chunks: list[dict],
    tool_observations: list[dict],
) -> list[dict]:
    orchestrator_detail = "No tool dispatched; orchestrator routed this turn to dialogue."
    trace: list[dict] = [
        {
            "step": "dialogue_ingest",
            "layer": "interaction",
            "where": "api",
            "llm_involved": False,
            "detail": "User message accepted and queued for async processing.",
        },
        {
            "step": "dialogue_rag_retrieval",
            "layer": "dialogue",
            "where": "dialogue-worker",
            "llm_involved": False,
            "detail": f"Retrieved {len(retrieved_chunks)} memory hit(s) via cosine similarity.",
        },
        {
            "step": "orchestrator_tool_routing",
            "layer": "orchestrator",
            "where": "orchestrator-worker",
            "llm_involved": True,
            "detail": orchestrator_detail,
        },
    ]
    for observation in tool_observations:
        trace.append(
            {
                "step": "tool_observation",
                "layer": "tool",
                "where": "orchestrator-worker",
                "llm_involved": False,
                "detail": f"{observation.get('label', observation.get('tool', 'tool'))}: {observation.get('result', '')}",
            }
        )
    trace.append(
        {
            "step": "response_generation",
            "layer": "dialogue",
            "where": "dialogue-worker",
            "llm_involved": llm_involved,
            "detail": "Response generated by the dialogue model."
            if response_source == "llm"
            else "Response emitted directly from a deterministic tool result without calling the model.",
        }
    )
    trace.append(
        {
            "step": "post_turn_finalize",
            "layer": "orchestrator",
            "where": "orchestrator-worker",
            "llm_involved": False,
            "detail": "Queued post-turn memory selection and compaction work.",
        }
    )
    return trace


async def _process_event(event: StoredInteractionEvent) -> None:
    context_settings = store.ensure_context_settings(
        settings.aigent_tenant_id,
        settings.ollama_context_window,
        settings.ollama_max_response_tokens,
        0.9,
    )
    effective_prompt = _effective_prompt()
    context = store.get_turn_context(event.id)
    if context is None or context.route_decision != "direct_dialogue":
        store.mark_event_failed(
            event.id,
            "Dialogue worker received a turn that was not routed to direct dialogue.",
        )
        return

    # Dialogue worker retrieves its own RAG context after the orchestrator has routed
    # the turn to direct dialogue. Tool-routed turns never reach this worker.
    retrieved_chunks: list[StoredRetrievedChunk] = []
    if context_settings.memory_enabled:
        retrieved_chunks = await _retrieve_context_chunks(event.content, exclude_source_id=event.id, limit=5)

    tool_observations: list[dict] = context.tool_observations

    history_messages = _conversation_history_messages(event.conversation_id, event.id)

    llm_messages: list[ChatMessageIn] = [ChatMessageIn(role="system", content=effective_prompt)]
    if retrieved_chunks:
        llm_messages.append(
            ChatMessageIn(
                role="system",
                content="Relevant remembered context:\n" + "\n".join(f"- {chunk.content}" for chunk in retrieved_chunks),
            )
        )
    if tool_observations:
        llm_messages.append(
            ChatMessageIn(
                role="system",
                content="Tool observations:\n" + "\n".join(
                    f"- {str(item.get('label', 'tool'))}: {str(item.get('result', ''))}"
                    for item in tool_observations
                ),
            )
        )
    recent_document_context = _recent_document_context(event.conversation_id, event.content)
    if recent_document_context:
        llm_messages.append(ChatMessageIn(role="system", content=recent_document_context))
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

    direct_tool_response = _direct_tool_response(event.content, tool_observations)
    if direct_tool_response is None:
        direct_tool_response = _pending_document_response(event.conversation_id, event.content)
    response_source = "deterministic_tool" if direct_tool_response is not None else "llm"
    response_policy = (
        "deterministic_direct_response"
        if direct_tool_response is not None
        else "dialogue_prompt_with_rag_context"
    )
    llm_involved = direct_tool_response is None
    started = time.perf_counter()
    try:
        if direct_tool_response is not None:
            await _handle_chunk(direct_tool_response, direct_tool_response)
            completion_content = direct_tool_response
            completion_latency_ms = 0
            completion_ttft_ms = 0
            completion_prompt_tokens = None
            completion_completion_tokens = estimate_tokens_for_messages(
                [ChatMessageIn(role="assistant", content=direct_tool_response)]
            )
            completion_total_tokens = completion_completion_tokens
        else:
            completion = await llm_client.chat(
                llm_messages,
                max_tokens=context_settings.max_response_tokens,
                on_chunk=_handle_chunk,
            )
            completion_content = completion.content
            completion_latency_ms = completion.latency_ms
            completion_ttft_ms = completion.ttft_ms
            completion_prompt_tokens = completion.prompt_tokens
            completion_completion_tokens = completion.completion_tokens
            completion_total_tokens = completion.total_tokens
    except Exception as exc:
        current_events = store.get_conversation_events(event.conversation_id)
        current_assistant = next((item for item in current_events if item.id == assistant_event.id), None)
        partial_content = current_assistant.content if current_assistant is not None else ""
        store.mark_event_failed_with_content(assistant_event.id, partial_content, f"{exc.__class__.__name__}: {exc}")
        raise
    total_latency_ms = int((time.perf_counter() - started) * 1000)

    store.mark_event_completed_with_content(assistant_event.id, completion_content)
    store.mark_event_completed(event.id)
    store.create_orchestration_event(
        event_type="finalize_turn",
        label="Writing memory",
        detail="Queued post-turn orchestration",
        status="pending",
        conversation_id=event.conversation_id,
        parent_event_id=event.id,
        payload={"assistant_event_id": assistant_event.id},
    )

    system_tokens_est, user_tokens_est, assistant_tokens_est = allocate_estimated_tokens(
        completion_prompt_tokens,
        system_chars,
        user_chars,
        assistant_chars,
    )
    store.add_performance_exchange(
        conversation_id=event.conversation_id,
        user_event_id=event.id,
        assistant_event_id=assistant_event.id,
        user_preview=event.content.strip()[:160],
        assistant_preview=completion_content.strip()[:160],
        total_latency_ms=total_latency_ms,
        llm_latency_ms=completion_latency_ms,
        ttft_ms=completion_ttft_ms,
        prompt_tokens=completion_prompt_tokens,
        completion_tokens=completion_completion_tokens,
        total_tokens=completion_total_tokens,
        response_source=response_source,
        response_policy=response_policy,
        llm_involved=llm_involved,
        tool_observations=tool_observations,
        workflow_trace=_workflow_trace(
            response_source=response_source,
            llm_involved=llm_involved,
            retrieved_chunks=[{"source_id": chunk.source_id} for chunk in retrieved_chunks],
            tool_observations=tool_observations,
        ),
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
        try:
            store.update_worker_heartbeat()
        except sqlite3.OperationalError as exc:
            print(f"dialogue-worker heartbeat skipped due to sqlite lock: {exc}")
            await asyncio.sleep(settings.worker_poll_interval_ms / 1000.0)
            continue
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
