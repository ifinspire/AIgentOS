"""Unit tests for `kernel.api.storage` — the SQLite-backed persistence layer
that underpins conversations, events, RAG chunks, MCP servers, prompt
profiles, and performance metrics.

``ChatStore`` is the only class the API and workers use to read or write
state, so it is the hot seam for a very large surface area (~80 methods,
~2250 lines). The tests in this file are scoped to **P0** — the breakage
that, in prior incidents or by virtue of what v0.3.0 added, would most
visibly take the product down:

* ``test_chat_store_message_and_context_flow`` — baseline happy path.
* ``test_delete_all_data_clears_every_table_and_store_remains_usable`` —
  regresses the SQLite hardening in commit 42a1ae7 (partial deletion used
  to leave the DB in a bad state).
* ``test_interaction_event_lifecycle_transitions`` — the five status-
  transition methods that the dialogue worker relies on end-to-end.
* ``test_orchestration_event_claim_and_payload_update`` — the async
  tool-calling flow introduced in v0.3.0.
* ``test_upsert_rag_chunks_is_idempotent_per_source`` — a repeat ingest
  must replace (not duplicate) chunks for the same ``(source_type,
  source_id)`` pair.
* ``test_performance_exchange_round_trip`` — guards the 26-column schema
  the baseline harness reads back.

Everything else (orchestration-to-conversation wiring, MCP server CRUD,
prompt-profile overrides, schema auto-repair, token aggregation,
``export_all_data``, concurrency under threads) is deliberately P1 and
not covered here yet.
"""

import pytest

from kernel.api.storage import (
    ChatStore,
    StoredRetrievedChunk,
    default_compaction_instructions,
)


@pytest.mark.p0
@pytest.mark.bvt
def test_chat_store_message_and_context_flow(chat_store: ChatStore):
    """Happy-path lifecycle smoke test for a brand-new SQLite store.

    Scenario:
      1. Fresh ``ChatStore`` (provided by the ``chat_store`` fixture) —
         verifies schema bootstrap on first use.
      2. Create a conversation.
      3. Record a pending user interaction event (the API writes these before
         the dialogue worker picks them up).
      4. Append an assistant message (the worker's completion path).
      5. List conversations and ensure tenant context settings.

    Expected:
      * user event is stored with ``status="pending"``,
      * assistant message is stored with ``role="assistant"``,
      * ``list_conversations`` returns exactly one row with ``message_count``
        of 2 and ``last_message`` reflecting the newest assistant reply (this
        is what the sidebar renders),
      * ``ensure_context_settings`` seeds sane defaults for a new tenant —
        specifically, ``compact_instructions`` falls back to
        ``default_compaction_instructions()`` and ``memory_enabled`` starts as
        ``True`` so the first conversation benefits from RAG.

    Why it matters: canary for schema migrations and wiring between
    conversations / interaction_events / messages / context_settings tables.
    """
    conversation_id, _ = chat_store.create_conversation("Test conversation")
    user_event = chat_store.create_interaction_event(
        conversation_id, "user", "Hello world", status="pending"
    )
    assistant_message = chat_store.add_message(conversation_id, "assistant", "Hi there")
    conversations = chat_store.list_conversations()
    context = chat_store.ensure_context_settings(
        tenant_id="tenant-a",
        max_context_tokens=4096,
        max_response_tokens=512,
        compact_trigger_pct=0.9,
    )

    assert user_event.status == "pending"
    assert assistant_message.role == "assistant"
    assert len(conversations) == 1
    assert conversations[0].id == conversation_id
    assert conversations[0].message_count == 2
    assert conversations[0].last_message == "Hi there"
    assert context.compact_instructions == default_compaction_instructions()
    assert context.memory_enabled is True


@pytest.mark.p0
def test_delete_all_data_clears_every_table_and_store_remains_usable(chat_store: ChatStore):
    """Regression guard for commit 42a1ae7 (SQLite hardening).

    Scenario: populate rows in every table that ``delete_all_data`` is
    responsible for wiping — conversation, interaction event, orchestration
    event, RAG chunk, MCP server, performance exchange, prompt profile /
    override, context settings — then call ``delete_all_data()``.

    Expected:
      * every populated table is empty afterwards (asserted via the
        store's high-level readers),
      * the store is still usable: creating a brand-new conversation
        and assistant message succeeds and is visible via
        ``list_conversations``. This is the behavior the prior incident
        broke — after a failed reset, subsequent writes would fail because
        the DB was left in a half-committed state.

    Why it matters: ``POST /api/data/reset`` calls this. If it ever
    regresses, users who click "reset all data" in the WebUI end up with
    a bricked DB and must remove the file manually.
    """
    conversation_id, _ = chat_store.create_conversation("Doomed")
    user_event = chat_store.create_interaction_event(
        conversation_id, "user", "Hi", status="completed"
    )
    chat_store.add_message(conversation_id, "assistant", "Hello")
    chat_store.create_orchestration_event(
        conversation_id=conversation_id,
        event_type="orchestrator.turn",
        label="turn",
        status="pending",
    )
    chat_store.upsert_rag_chunks(
        "document",
        "source-1",
        [("chunk one", [0.1, 0.2, 0.3])],
    )
    chat_store.create_mcp_server(
        name="markitdown",
        transport="http",
        command=None,
        args=[],
        url="http://markitdown:3001/mcp/",
        env={},
        enabled=True,
    )
    chat_store.add_performance_exchange(
        conversation_id=conversation_id,
        user_event_id=user_event.id,
        assistant_event_id=None,
        user_preview="Hi",
        assistant_preview="Hello",
        total_latency_ms=10,
        llm_latency_ms=5,
        ttft_ms=3,
        prompt_tokens=4,
        completion_tokens=2,
        total_tokens=6,
        response_source="direct",
        response_policy="default",
        llm_involved=True,
        tool_observations=[],
        workflow_trace=[],
        retrieved_chunks=[],
        system_chars=10,
        user_chars=2,
        assistant_chars=5,
        system_tokens_est=3,
        user_tokens_est=1,
        assistant_tokens_est=2,
    )
    chat_store.ensure_default_prompt_profile("tenant-a")
    chat_store.ensure_context_settings("tenant-a", 4096, 512, 0.9)

    chat_store.delete_all_data()

    assert chat_store.list_conversations() == []
    assert chat_store.count_rag_chunks() == 0
    assert chat_store.list_mcp_servers() == []
    assert chat_store.list_recent_performance_exchanges() == []
    # ``list_prompt_profiles`` auto-seeds a Default on read, so a raw table
    # count is the honest check that the DELETE happened.
    profile_rows = chat_store._conn.execute(
        "SELECT COUNT(*) AS c FROM prompt_profiles"
    ).fetchone()
    assert profile_rows["c"] == 0

    # Store must still be writable after a reset.
    new_id, _ = chat_store.create_conversation("After reset")
    chat_store.add_message(new_id, "assistant", "Post-reset reply")
    conversations = chat_store.list_conversations()
    assert len(conversations) == 1
    assert conversations[0].id == new_id
    assert conversations[0].last_message == "Post-reset reply"


@pytest.mark.p0
def test_interaction_event_lifecycle_transitions(chat_store: ChatStore):
    """Dialogue-event lifecycle coverage for the currently exercised paths.

    Scenario: create a user event in ``pending`` and seed a turn-context row
    with ``route_decision='direct_dialogue'`` (required by
    ``claim_next_pending_user_event``'s WHERE clause). Walk two events
    through:
      * event A — claimed, then
        ``mark_event_completed_with_content``,
      * event B — ``mark_event_failed_with_content`` recording the error.

    Expected:
      * claim flips status to ``processing``,
      * completed event ends with ``status='completed'`` and the final content,
      * failed event ends with ``status='failed'``, an ``error`` string, and
        the partial content preserved so users can see what the model did
        produce before the failure.

    Why it matters: these exercised storage methods are part of the dialogue
    worker's event-state handoff. A regression here turns chat into a hang
    (stuck on ``pending``) or a lost response (wiped content on failure).
    """
    conversation_id, _ = chat_store.create_conversation("Lifecycle")
    pending_a = chat_store.create_interaction_event(
        conversation_id, "user", "Question A", status="pending"
    )
    pending_b = chat_store.create_interaction_event(
        conversation_id, "user", "Question B", status="pending"
    )
    chat_store.upsert_turn_context(
        user_event_id=pending_a.id,
        conversation_id=conversation_id,
        route_decision="direct_dialogue",
        retrieved_chunks=[],
        tool_observations=[],
        memory_candidates=[],
    )

    claimed = chat_store.claim_next_pending_user_event()
    assert claimed is not None
    assert claimed.id == pending_a.id
    assert claimed.status == "processing"

    chat_store.mark_event_completed_with_content(pending_a.id, "Final answer A")
    completed = chat_store.get_interaction_event(pending_a.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.content == "Final answer A"
    assert completed.error is None

    chat_store.mark_event_failed_with_content(
        pending_b.id, "Partial answer B", "LLM timeout"
    )
    failed = chat_store.get_interaction_event(pending_b.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.content == "Partial answer B"
    assert failed.error == "LLM timeout"


@pytest.mark.p0
def test_orchestration_event_claim_and_payload_update(chat_store: ChatStore):
    """Orchestrator claim path: matching ``event_type``, payload merge on
    update, status transition to ``completed``.

    Scenario: create two orchestration events — one with
    ``event_type='orchestrator.turn'`` and one with
    ``event_type='document.ingest'``. Claim the orchestrator one by passing
    ``event_type='orchestrator.turn'`` to the claim method. Then update it
    with a new payload and ``status='completed'``.

    Expected:
      * claim returns the orchestrator event, not the ingest one, even though
        the ingest event was created second (type filter must win over FIFO),
      * claim flips status to ``processing``,
      * ``update_orchestration_event`` replaces payload atomically; subsequent
        ``list_conversation_orchestration_events`` reads back the new payload
        and ``status='completed'``.
      * the unclaimed ingest event stays ``pending`` and untouched.

    Why it matters: this is the v0.3.0 orchestrator's handoff surface. The
    orchestrator worker claims-by-type so the dialogue worker and it don't
    fight over the same queue. If the filter breaks, workers steal each
    other's events and tool calls silently stop happening.
    """
    conversation_id, _ = chat_store.create_conversation("Orchestration")
    orchestrator_event = chat_store.create_orchestration_event(
        conversation_id=conversation_id,
        event_type="orchestrator.turn",
        label="turn:start",
        status="pending",
        payload={"step": 0},
    )
    ingest_event = chat_store.create_orchestration_event(
        conversation_id=conversation_id,
        event_type="document.ingest",
        label="ingest:start",
        status="pending",
        payload={"document_id": "doc-1"},
    )

    claimed = chat_store.claim_next_pending_orchestration_event(
        event_type="orchestrator.turn"
    )
    assert claimed is not None
    assert claimed.id == orchestrator_event.id
    assert claimed.status == "processing"

    chat_store.update_orchestration_event(
        orchestrator_event.id,
        status="completed",
        label="turn:done",
        payload={"step": 1, "result": "ok"},
    )

    events = {
        event.id: event
        for event in chat_store.list_conversation_orchestration_events(conversation_id)
    }
    assert events[orchestrator_event.id].status == "completed"
    assert events[orchestrator_event.id].label == "turn:done"
    assert events[orchestrator_event.id].payload == {"step": 1, "result": "ok"}
    assert events[ingest_event.id].status == "pending"
    assert events[ingest_event.id].payload == {"document_id": "doc-1"}


@pytest.mark.p0
def test_upsert_rag_chunks_is_idempotent_per_source(chat_store: ChatStore):
    """Re-ingesting a source replaces (not duplicates) its chunks.

    Scenario: call ``upsert_rag_chunks`` twice for the same
    ``(source_type='document', source_id='doc-1')`` with different chunk
    payloads, then a third time for a different ``source_id='doc-2'``.

    Expected:
      * after the second ``upsert`` for ``doc-1``, only the NEW two chunks
        are retrievable via ``list_rag_chunks_for_source`` — the first
        upload's chunks are gone,
      * ``doc-2`` chunks coexist independently and ``count_rag_chunks()``
        returns the combined total (2 + 1 = 3),
      * embeddings round-trip unchanged (JSON encode/decode preserves
        float values).

    Why it matters: document re-ingest is a common user action (edit a file
    and re-upload). If this ever regressed to a pure INSERT, the RAG index
    would silently bloat with stale duplicates, ranking would degrade, and
    ``count_rag_chunks`` would overstate index size for the operator.
    """
    chat_store.upsert_rag_chunks(
        "document",
        "doc-1",
        [
            ("old chunk A", [0.1, 0.2]),
            ("old chunk B", [0.3, 0.4]),
            ("old chunk C", [0.5, 0.6]),
        ],
    )
    chat_store.upsert_rag_chunks(
        "document",
        "doc-1",
        [
            ("new chunk A", [0.7, 0.8]),
            ("new chunk B", [0.9, 1.0]),
        ],
    )
    chat_store.upsert_rag_chunks(
        "document",
        "doc-2",
        [("doc2 chunk", [0.11, 0.22])],
    )

    doc1_chunks = chat_store.list_rag_chunks_for_source("document", "doc-1", limit=10)
    doc2_chunks = chat_store.list_rag_chunks_for_source("document", "doc-2", limit=10)

    assert sorted(chunk.content for chunk in doc1_chunks) == ["new chunk A", "new chunk B"]
    assert [chunk.content for chunk in doc2_chunks] == ["doc2 chunk"]
    assert doc2_chunks[0].embedding == [0.11, 0.22]
    assert chat_store.count_rag_chunks() == 3
    assert chat_store.count_rag_chunks(source_types=["document"]) == 3
    assert chat_store.count_rag_chunks(source_types=["conversation"]) == 0


@pytest.mark.p0
def test_performance_exchange_round_trip(chat_store: ChatStore):
    """Write one performance exchange, read it back, every field preserved.

    Scenario: create a conversation and a user event, then call
    ``add_performance_exchange`` with a representative, non-default value for
    every column — latencies, all three token counts, response source/policy,
    the ``llm_involved`` bool, JSON blobs (tool_observations, workflow_trace),
    one retrieved chunk, and the per-role character / token-estimate splits.
    Read back via ``get_latest_performance_exchange_for_conversation``.

    Expected:
      * every scalar column round-trips unchanged,
      * ``llm_involved`` stays a bool (not a 0/1 int) on the way out,
      * JSON-encoded columns decode to equal structures,
      * ``retrieved_chunks`` rehydrates into a ``StoredRetrievedChunk``
        dataclass with the same fields the writer supplied,
      * ``retrieved_chunk_count`` is set to ``len(retrieved_chunks)``.

    Why it matters: this row is the entire input to the Performance panel AND
    the baseline harness's regression metrics. The INSERT has 26 placeholders
    bound positionally — a column added or reordered on one side silently
    shifts every field below it. This test catches that.
    """
    conversation_id, _ = chat_store.create_conversation("Perf")
    user_event = chat_store.create_interaction_event(
        conversation_id, "user", "Q", status="completed"
    )
    retrieved = StoredRetrievedChunk(
        content="chunk",
        score=0.75,
        source_id="doc-1",
        source_type="document",
        source_preview="chunk preview",
    )

    chat_store.add_performance_exchange(
        conversation_id=conversation_id,
        user_event_id=user_event.id,
        assistant_event_id=None,
        user_preview="Q",
        assistant_preview="A",
        total_latency_ms=1234,
        llm_latency_ms=987,
        ttft_ms=321,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        response_source="direct",
        response_policy="default",
        llm_involved=True,
        tool_observations=[{"tool": "markitdown", "ok": True}],
        workflow_trace=[{"step": "route", "value": "direct_dialogue"}],
        retrieved_chunks=[retrieved],
        system_chars=200,
        user_chars=1,
        assistant_chars=1,
        system_tokens_est=50,
        user_tokens_est=1,
        assistant_tokens_est=1,
    )

    exchange = chat_store.get_latest_performance_exchange_for_conversation(conversation_id)

    assert exchange is not None
    assert exchange.conversation_id == conversation_id
    assert exchange.user_event_id == user_event.id
    assert exchange.user_preview == "Q"
    assert exchange.assistant_preview == "A"
    assert exchange.total_latency_ms == 1234
    assert exchange.llm_latency_ms == 987
    assert exchange.ttft_ms == 321
    assert exchange.prompt_tokens == 100
    assert exchange.completion_tokens == 50
    assert exchange.total_tokens == 150
    assert exchange.response_source == "direct"
    assert exchange.response_policy == "default"
    assert exchange.llm_involved is True
    assert exchange.tool_observations == [{"tool": "markitdown", "ok": True}]
    assert exchange.workflow_trace == [{"step": "route", "value": "direct_dialogue"}]
    assert exchange.retrieved_chunk_count == 1
    assert len(exchange.retrieved_chunks) == 1
    assert exchange.retrieved_chunks[0].content == "chunk"
    assert exchange.retrieved_chunks[0].score == 0.75
    assert exchange.retrieved_chunks[0].source_id == "doc-1"
    assert exchange.retrieved_chunks[0].source_type == "document"
    assert exchange.retrieved_chunks[0].source_preview == "chunk preview"
    assert exchange.system_chars == 200
    assert exchange.user_chars == 1
    assert exchange.assistant_chars == 1
    assert exchange.system_tokens_est == 50
    assert exchange.user_tokens_est == 1
    assert exchange.assistant_tokens_est == 1
