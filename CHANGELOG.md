# Changelog

All notable changes to this project are documented in this file.

## [0.3.0-oss] - 2026-04-08

### Hardened — Pre-Release Stability
- Docker services now use `restart: unless-stopped` to recover more gracefully from transient worker crashes.
- End-to-end baseline now fails fast when required workers are missing or stale, instead of waiting for a long assistant timeout.
- Dialogue and orchestrator workers now treat transient SQLite heartbeat lock errors as retryable, preventing the whole worker process from exiting on `sqlite3.OperationalError: locking protocol`.
- WebUI package metadata normalized to `0.3.0-oss` for release consistency.

### Added — Orchestrator Worker
- Background orchestrator worker (`kernel/workers/orchestrator_worker.py`) as a separate Docker service (`orchestrator-worker` in `docker-compose.yml`).
- `orchestration_events` table for tracking orchestrator work items (`prepare_turn`, `finalize_turn`, `document_import`, `mcp_call`) with full status lifecycle.
- `turn_contexts` table: orchestrator writes tool routing results per turn; dialogue worker reads `tool_observations` before generating a response.
- Orchestration event timeline visible in the WebUI with per-step status and detail.

### Added — MCP Integration
- `kernel/api/mcp.py`: MCP client supporting both `stdio` and `streamable_http` transports with proper MCP spec (2025-03-26) initialize handshake.
- `mcp_servers` table for persistent MCP server registration (name, transport, command/URL, enabled state, discovered tools with full metadata).
- Runtime tool discovery: on startup, both the kernel API and orchestrator worker discover tools from all enabled MCP servers via `tools/list`, storing name, description, and inputSchema.
- Dynamic routing prompt: discovered MCP tools are appended to `routing.md` at runtime with descriptions and parameter schemas, so the routing LLM can dispatch to MCP tools without manual prompt editing.
- MCP tool execution: orchestrator calls `tools/call` on the matched server and injects the result as a `tool_observation` for the dialogue worker.
- MCP server CRUD API: `GET/POST /api/mcp/servers`, `PATCH/DELETE /api/mcp/servers/{id}`, `POST /api/mcp/servers/{id}/refresh`.
- MCP dashboard section in WebUI: register, inspect, enable/disable, refresh, and delete MCP servers. Shows discovered tools, connection status, and errors.
- Bundled MarkItDown MCP server (`Dockerfile.markitdown-mcp`, `markitdown-mcp` service in `docker-compose.yml`): document conversion runs as a sidecar MCP service rather than an in-process dependency.
- MCP bootstrap logic consolidated in `kernel/api/mcp.py` (`ensure_default_markitdown_server`, `discover_enabled_servers`) — shared between kernel API and orchestrator worker.

### Added — Deterministic Tools
- Calculator tool: safe AST-based expression evaluation (operand values capped at 1,000,000 to prevent DoS).
- Math subagent: LLM-assisted arithmetic intent resolution with conversation context carry-forward, backed by `agent-prompts/orchestrator/math_subagent.md`.
- Letter-counter tool: deterministic character count responses.
- Tool routing via LLM call: the orchestrator uses `agent-prompts/orchestrator/routing.md` as the routing prompt to decide which tool (if any) to dispatch for a given user message.
- After execution, the orchestrator posts a `role: "tool"` interaction event to the conversation for full auditability in the event stream.
- `agent-prompts/orchestrator/routing.md`: declarative tool registry loaded at runtime — edit to add, remove, or reconfigure native tools without touching Python code.

### Added — Document Import
- `POST /api/imports` endpoint accepting file uploads (PDF, Markdown, plaintext) up to 50 MB.
- Document conversion via the bundled MarkItDown MCP sidecar service; converted Markdown is chunked and embedded into `rag_chunks` for retrieval.
- `document_imports` table tracking import status, stored path, file hash, and conversion result.
- Duplicate detection by SHA-256 hash per conversation.
- `GET /api/imports` and `GET /api/imports/{id}` for listing and polling import status.
- LLM-generated assistant messages on import lifecycle: "started processing" (before conversion begins) and "done"/"failed" (after completion), stored as real interaction events.

### Changed — Worker Responsibilities
- **Dialogue worker** owns RAG retrieval: embeds the user message and performs cosine similarity search directly, independent of the orchestrator. The orchestrator no longer retrieves memory chunks.
- **Orchestrator worker** `prepare_turn` uses an LLM routing call to decide tool dispatch. Routing prompt is dynamically extended with discovered MCP tools at runtime. `turn_contexts.retrieved_chunks` is always written as `[]`; the column is retained for schema compatibility.
- `GET /api/worker/health` now reports status for both workers: `dialogue_worker` and `orchestrator_worker` as separate keys.
- Orchestrator worker writes its heartbeat under `worker_id = "orchestrator-worker"`.
- Workflow trace: `orchestrator_prepare` step renamed to `orchestrator_tool_routing` (`llm_involved: true`); `dialogue_rag_retrieval` step added (`llm_involved: false`).

### Changed — Infrastructure
- Kernel and worker Dockerfiles updated to Python 3.12 (was 3.11).
- `markitdown[pdf]` removed from kernel `requirements.txt` — document conversion is now handled by the MarkItDown MCP sidecar.
- MCP server identifier in routing prompts uses slugified server names (e.g. `mcp::markitdown-mcp::convert_to_markdown`) instead of truncated UUIDs for readability.
- Routing LLM `max_tokens` increased from 64 to 192 to accommodate MCP tool parameter JSON.

### Fixed
- `get_active_prompt_profile` (storage.py) now raises `ValueError` if no active profile row is found, instead of crashing with an unguarded `None` access.
- `_safe_number_node` in the orchestrator calculator now validates that `ast.Constant` values are numeric and `abs(value) <= 1_000_000`, preventing CPU/memory DoS from expressions like `9**9**9`.
- File upload (`POST /api/imports`) now enforces a 50 MB cap before reading into memory, preventing OOM on oversized files.
- Dialogue worker now sets `context_timed_out = True` when the orchestrator routing signal is not available within 1.5 s, surfacing a step in the workflow trace instead of silently proceeding.
- MCP stdio transport: `_read_stdio_response_for_id` now caps at 50 non-matching messages to prevent infinite loops.
- MCP HTTP transport: added proper initialize handshake per MCP spec (was sending method calls without initialization).
- Document import "started"/"done" assistant messages are now stored in the DB before the import status transitions, fixing a race condition where the frontend would poll and miss the messages.

### Future Direction
- **True parallel sub-agents**: today the orchestrator routes and executes tools sequentially within the `prepare_turn` event. Genuine parallelism would require spawning a separate worker per tool call backed by a distributed task queue (e.g. NATS or Redis Streams).
- **Tool result in conversation history**: `role: "tool"` events are currently excluded from the dialogue LLM's conversation history. Future work could include these in the message sequence (OpenAI function-calling style) to let the model reason over prior tool results across turns.
- **MCP connection pooling**: stdio and HTTP MCP connections are currently per-call. For high-volume use cases, persistent connections or process pools could reduce latency, but this is deferred as premature optimization for the single-user self-host target.
- **Richer MCP provenance**: capture per-tool latency, token cost attribution, and failure rates in workflow traces and debug logs.

## [0.2.0-oss] - 2026-04-06

### Added — Async Worker Architecture
- Background dialogue worker (`kernel/workers/dialogue_worker.py`) that polls for pending user events and streams LLM responses asynchronously.
- `dialogue-worker` service in `docker-compose.yml` running as a separate container.
- `POST /api/chat` now returns `202 Accepted` with `conversation_id` and `event_id` (was synchronous blocking response).

### Added — Interaction Events Model
- `interaction_events` table replaces the `messages` table. Events track `status` (`pending` → `processing` → `completed` / `failed`), `causation_event_id`, and `processed_at` timestamps.
- `GET /api/conversations/{id}/events` endpoint returning full event list with status and causation data.
- `InteractionEventResponse` and `ConversationEventsResponse` Pydantic models.

### Added — Conversation Memory (RAG)
- `rag_chunks` table for storing text chunks with JSON-serialized embedding vectors.
- `OllamaEmbeddingClient` (`kernel/api/llm.py`) for generating embeddings via Ollama's `/api/embed` endpoint (with `/api/embeddings` fallback).
- Semantic memory retrieval: cosine similarity search over stored chunks to inject relevant context into LLM prompts.
- Automatic memory compaction: when chunk count exceeds the configured limit, oldest chunks are summarized via LLM and re-embedded.
- `GET /api/memory/chunks` and `DELETE /api/memory/chunks/{id}` endpoints for memory inspection and management.
- `memory_enabled` toggle on context settings.
- Environment variables: `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `MEMORY_CHUNK_LIMIT`, `MEMORY_COMPACTION_BATCH_SIZE`.

### Added — SSE Streaming
- `GET /api/conversations/{id}/stream` endpoint for real-time Server-Sent Events.
- Worker updates assistant event content on each streamed token, enabling live response rendering in the WebUI.
- Idle timeout (~5 minutes) and `asyncio.CancelledError` handling to prevent resource leaks.

### Added — TTFT and Extended Metrics
- Time-to-first-token (TTFT) tracking in `OllamaClient`, excluding `<think>...</think>` blocks.
- `ttft_ms` field on performance exchanges.
- `retrieved_chunk_count` and `retrieved_chunks` fields on performance exchanges for memory retrieval visibility.
- Per-turn latency, TTFT, prompt tokens, and completion tokens on multi-turn baseline cases.
- `completion_time_ms` on baseline case results.

### Added — Infrastructure
- `kernel/shared/` module with shared text processing (`text.py`) and token estimation (`metrics.py`) utilities used by both kernel API and dialogue worker.
- `kernel/__init__.py` to make kernel a proper Python package.
- `GET /api/worker/health` endpoint for dialogue worker heartbeat monitoring.
- Worker heartbeat: dialogue worker writes a heartbeat each poll cycle; kernel checks staleness.
- `.dockerignore` to exclude `.git`, `__pycache__`, `.env`, `node_modules`, `dist`, and other build artifacts from Docker images.
- `.ai-instructions.md` with current architecture documentation.
- `VITE_API_URL` environment variable wired through `docker-compose.yml` to the WebUI.
- `baseline/` volume mount on kernel and worker containers.

### Changed — Database
- Denormalized `message_count` and `last_message_preview` columns on `conversations` table (replaces correlated subqueries in `list_conversations`).
- `ON DELETE CASCADE` on `interaction_events` and `performance_exchanges` foreign keys to `conversations`.
- Simplified `delete_conversation` to rely on CASCADE (only `rag_chunks` deleted manually).
- Reuse single SQLite connection per `ChatStore` instance (was opening a new connection per method call).

### Changed — LLM Client
- Reuse `httpx.AsyncClient` instances in `OllamaClient` and `OllamaEmbeddingClient` (was creating a new client per request).
- `<think>...</think>` tag stripping consolidated into shared `extract_visible_text`.

### Changed — Defaults and Validation
- Default `OLLAMA_MAX_RESPONSE_TOKENS` increased from `512` to `1024`.
- `ChatRequest.message` max length increased from `10,000` to `100,000` characters.

### Changed — WebUI
- Chat flow updated for async event-based architecture (polls `/api/conversations/{id}/events` instead of receiving synchronous response).
- SSE stream support for real-time response rendering.
- Memory management UI (view and delete memory chunks).
- Context settings now include `memory_enabled` toggle.
- Version labels updated to `v0.2.0` at the time of that release.

### Removed
- Synchronous `ChatResponse` model (replaced by `ChatAcceptedResponse` + async events).
- `messages` table (replaced by `interaction_events`).
- Unused `python-dotenv` dependency from `requirements.txt`.

## [0.1.0-oss] - 2026-02-19

Initial public OSS baseline release.

### Added
- Kernel-first public architecture (`kernel/` as core product).
- Optional WebUI (`agent-webui/`) and prompt pack (`agent-prompts/`) layers.
- Baseline performance artifacts under `baseline/perf/`.
- UI baseline asset under `baseline/ui/`.
- Split-license map at repository root (`LICENSE`).
- OSS community starter files:
  - `CONTRIBUTING.md`
  - `CODE_OF_CONDUCT.md`
  - `SECURITY.md`
  - `.github/` issue and PR templates
  - `.github/workflows/ci.yml`

### Changed
- Runtime stack trimmed to currently implemented services only.
- README aligned to implemented scope and baseline data.
- Licensing docs updated to reflect per-directory licensing.
