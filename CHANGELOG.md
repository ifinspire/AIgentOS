# Changelog

All notable changes to this project are documented in this file.

## [0.2.3-oss] - 2026-04-08

### Added — Direct vs E2E Baseline Modes
- Baseline runs now support `Direct model` and `End-to-end AIgentOS` modes.
- E2E mode measures the real async `POST /api/chat -> dialogue-worker -> assistant completion` path for `AIgentOS-GH`.
- Baseline markdown exports now include mode metadata, and E2E exports use a `baseline-e2e-...` filename.

### Changed — Version Labels
- Product/version labels updated to `v0.2.3-oss` across the kernel metadata, WebUI, and package metadata.

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
- Version labels updated to `v0.2.0`.

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
