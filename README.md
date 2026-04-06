# AIgentOS

![AIgentOS UI Baseline](baseline/ui/home.png)

Kernel-first OSS for self-hosted AI agents.  
The kernel is the product; the WebUI is an optional add-on.

## Why This Project

There are already large open-source projects (for example, [Open WebUI](https://docs.openwebui.com/)).
AIgentOS is intentionally a lightweight baseline for running a local multi-turn chatbot in Docker, with a focus on simplicity and transparency.

This project is built to provide:
- A small, understandable codebase with clear backend behavior
- Practical visibility into prompt/runtime logic and performance data
- A kernel foundation for future agent systems, including alternatives to tool-calling workflows in later versions

This baseline comes from building multiple agents and needing an abstracted kernel to reuse across future agent experiences.

## Who This Is For

- People who want a highly personalized and consistent chatbot experience, and prefer stable, bounded behavior over constantly changing frontier features
- People who care about security/privacy and want practical local capabilities (summarization, rubber-ducking, open-model experimentation) without heavy infrastructure

## Project Scope (Current OSS Baseline)

This repository currently ships:
- A FastAPI-based kernel (`/kernel`) with Ollama chat integration
- Async dialogue worker that processes messages in the background with real-time streaming via SSE
- Lightweight conversation memory backed by SQLite chunk storage and semantic retrieval (embedding-based)
- Prompt component/profile management (`/agent-prompts`)
- Local SQLite conversation and event storage in `/models-local/chat.db`
- Performance/debug API surfaces with TTFT tracking, token breakdowns, and baseline benchmarking
- A Vite/React WebUI (`/agent-webui`) as an optional interface

## Why One Repo (for now)

We keep kernel and WebUI in one repo right now for simpler OSS onboarding, versioning, and CI.

- The kernel remains independently useful without the WebUI.
- The WebUI is treated as a reference client and bonus add-on.
- If ecosystem usage grows, splitting into `-kernel` and `-webui` repos later is straightforward.

## Why SmolLM3

Default prompt configs are centered around SmolLM3-class usage because the model family is:
- Open, with strong visibility into training methodology/data disclosures
- Practical for local/self-hosted inference footprints
- Intended to be commercially usable (Apache-2.0)

Always validate model and dataset terms for your own deployment and jurisdiction.
- https://huggingface.co/HuggingFaceTB/SmolLM3-3B
- https://ollama.com/alibayram/smollm3


## Repository Layout

```text
.
├── kernel/              # Kernel API and orchestration logic
│   ├── api/             # FastAPI endpoints, storage, LLM client, models
│   ├── workers/         # Background dialogue worker
│   └── shared/          # Shared utilities (text processing, token metrics)
├── agent-prompts/       # Prompt bundle + components (default agent profile)
├── agent-webui/         # Optional WebUI client
├── models-local/        # Local runtime data (e.g., SQLite chat DB)
├── docker-compose.yml
└── LICENSE
```

## Quick Start

### Prerequisites
- [Docker + Docker Compose](https://www.docker.com/) 
- [Ollama](https://ollama.com/) running on the host

### Run full stack (kernel + optional WebUI)

```bash
docker compose up -d
```

Default endpoints:
- WebUI: `http://localhost:5500`
- Kernel API: `http://localhost:5501`

### Run kernel only

```bash
docker compose up -d kernel
```

## API Snapshot

Current kernel endpoints include:
- `GET /health`
- `GET /api/worker/health` — dialogue worker heartbeat status
- `POST /api/chat`
- `POST /api/llm/warmup`
- `GET /api/conversations/{id}/stream` — SSE stream for real-time event updates
- Conversation CRUD under `/api/conversations`
- Memory management under `/api/memory/chunks`
- Prompt settings/profiles/components under `/api/prompts/*`
- Performance/debug surfaces under `/api/performance/*` and `/api/debug/logs`
- Baseline benchmarking under `/api/baseline/*`
- Admin export/reset endpoints under `/api/admin/*`

## Memory and Thinking Notes
![AIgentOS Memory Controls](baseline/ui/memory.png)

- `Memory` in the current OSS baseline means conversation memory, not a general-purpose knowledge base or structured profile/entity system.
- Memory stores chunks derived from prior user/assistant turns and may retrieve them to influence future responses.
- Retrieved memory can improve continuity, but it can also reinforce earlier mistakes if incorrect content gets remembered and reused later.
- To keep the system lightweight, older memory chunks are periodically rolled up into summarized memory when the store grows past its configured limit.
- Thinking-capable models may emit visible `<think>...</think>` content. AIgentOS currently keeps that behavior as-is and does not expose a separate runtime toggle to disable thinking output.

## Architecture Notes

- **Shared utilities** live in `kernel/shared/` (text processing, token estimation). Both the kernel API and the dialogue worker import from here to avoid duplication.
- **Token estimation** currently uses a `char_count / 4` heuristic. This is a known approximation; future versions may use Ollama's `/api/tokenize` endpoint for accuracy.
- **RAG retrieval** computes cosine similarity against all stored memory chunks. This is acceptable at the default chunk limit (160) but will not scale to thousands of chunks without indexing.
- **SSE streaming** (`/api/conversations/{id}/stream`) includes an idle timeout (~5 minutes) and handles client disconnection gracefully.
- **Worker health** is tracked via a heartbeat written to the database each poll cycle. The kernel exposes `GET /api/worker/health` to check if the dialogue worker is alive.

## Baseline Perf Data

Baseline runs are tracked in `/baseline/perf/` (timestamped markdown reports).

Benchmark environment:
- Apple M3 Pro
- 18 GB RAM
- macOS Tahoe 26.3

### v0.2.0 Baseline (2026-04-06)

3 runs on `alibayram/smollm3` with `max_response_tokens=1024` (doubled from v0.1.0's 512).

v0.2.0 adds TTFT (time-to-first-token) and per-turn throughput tracking. Reports now include token generation rates (~43-55 tok/s observed, declining as context grows).

| Report | Completed (local) | Calls | Simple Q/A min-max | Summarization min-max | 20-turn min-max | Structured extraction | System prompt pressure min-max | TTFT min-max | 20-turn tok/s |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| [baseline-20260406-130826.md](baseline/perf/baseline-20260406-130826.md) | 2026-04-06 13:08:13 | 34 | 11.11s-18.59s | 2.91s-4.37s | 4.53s-9.04s | 3.30s | 2.27s-16.24s | 340ms-12.22s | 43-56 |
| [baseline-20260406-131132.md](baseline/perf/baseline-20260406-131132.md) | 2026-04-06 13:11:24 | 34 | 6.15s-18.74s | 2.30s-5.28s | 2.38s-3.71s | 3.45s | 2.60s-13.28s | 338ms-12.21s | 44-53 |
| [baseline-20260406-131642.md](baseline/perf/baseline-20260406-131642.md) | 2026-04-06 13:16:21 | 34 | 2.91s-13.67s | 3.83s-4.72s | 3.44s-6.40s | 2.83s | 1.84s-14.24s | 350ms-12.21s | 43-54 |

Observed ranges:
- 20-turn per-turn completion: `2.38s` to `9.04s` (throughput: `43-55 tok/s`)
- TTFT for first turn: `295ms` to `350ms`
- TTFT at ~10k system tokens: `~12.2s` (consistent across all 3 runs)
- System prompt pressure at ~10k system tokens: `13.28s` to `16.24s`
- Summarization: `2.30s` to `5.28s`

### v0.1.0 Baseline (2026-02-19)

3 runs on `alibayram/smollm3` with `max_response_tokens=512`.

| Report | Completed (local) | Calls | Simple Q/A min-max | Summarization min-max | 20-turn min-max | Structured extraction | System prompt pressure min-max | 20-turn tok/s |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| [baseline-20260219-022655.md](baseline/perf/baseline-20260219-022655.md) | 2026-02-19 02:26:33 | 34 | 1.91s-9.52s | 3.55s-5.55s | 2.81s-5.04s | 2.61s | 2.37s-14.40s | 43-55 |
| [baseline-20260219-025528.md](baseline/perf/baseline-20260219-025528.md) | 2026-02-19 02:30:54 | 34 | 6.98s-24.82s | 3.16s-10.51s | 2.10s-16.23s | 3.69s | 2.41s-15.09s | 46-52 |
| [baseline-20260219-030717.md](baseline/perf/baseline-20260219-030717.md) | 2026-02-19 03:07:04 | 34 | 7.58s-8.97s | 3.41s-7.04s | 2.29s-3.15s | 5.04s | 2.03s-22.80s | 41-54 |

### Comparison Notes

- **`max_response_tokens` change**: v0.2.0 runs with 1024 (vs 512 in v0.1.0). This allows longer completions, which increases latency for cases where the model generates more tokens. Simple Q/A ranges are wider in v0.2.0 because the model can now produce up to 1024 completion tokens per turn.
- **Summarization** is comparable across versions since summaries naturally stay short regardless of the token cap.
- **System prompt pressure** at 10k tokens is slightly improved in v0.2.0 (`13.28s-16.24s` vs `14.40s-22.80s`), likely due to the model warming from prior test cases in the same run and run-to-run variance.
- **20-turn throughput** degrades from ~55 tok/s (turn 1) to ~43 tok/s (turn 20) as the context window fills — consistent with expected prefill cost scaling.
- **TTFT** is a new metric in v0.2.0. First-turn TTFT is under 400ms; at 10k system tokens it stabilizes around 12.2s, confirming that prefill cost dominates time-to-first-token at large context sizes.

### Interpretation

- Latency is most sensitive to effective prompt size and completion length.
- The 20-turn test shows context growth impact over time.
- When a model emits visible thinking text, baseline `completion tokens` reflect the model's full generated output budget, which can include both `<think>...</think>` content and the final visible answer.
- The baseline `Enforce max response tokens` option uses the current runtime `max_response_tokens` setting. If that cap is increased, baseline completion lengths and latencies may increase too.

### General Perf Dashboard (for chats)
![AIgentOS Perf Dashboard](baseline/ui/performance.png)

### Privacy-First Data Deletion Option
![AIgentOS Settings](baseline/ui/settings.png)

## License

AIgentOS uses a split-license model:
- `kernel/` -> MPL-2.0
- `agent-webui/` -> Apache-2.0
- `agent-prompts/` -> Apache-2.0

See `/LICENSE` for the directory-level map and rationale, and the license files inside each directory for full legal text.
