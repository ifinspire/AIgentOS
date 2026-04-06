# Contributing

Thanks for contributing to AIgentOS.

## Scope

This repository is kernel-first:
- `kernel/` is the core engine.
- `agent-webui/` and `agent-prompts/` are optional layers.

Keep pull requests focused and scoped to one change when possible.

## Project Structure

```
kernel/
├── api/          # FastAPI endpoints, storage, LLM client, models
├── workers/      # Background dialogue worker
└── shared/       # Shared utilities used by both api and workers
```

**Shared utilities** (`kernel/shared/`): If you add a function needed by both `kernel/api/` and `kernel/workers/`, put it in `kernel/shared/`. Current modules:
- `text.py` — text chunking, cosine similarity, visible text extraction, preview text
- `metrics.py` — token estimation and allocation

Do not import directly between `kernel/api/` and `kernel/workers/` for utility logic — route through `kernel/shared/` instead.

## Local Setup

Prerequisites:
- Docker + Docker Compose
- Ollama running on host

Start stack:

```bash
docker compose up -d
```

Endpoints:
- WebUI: `http://localhost:5500`
- Kernel: `http://localhost:5501`

Optional kernel-only run:

```bash
docker compose up -d kernel
```

Verify the dialogue worker is running:

```bash
curl http://localhost:5501/api/worker/health
```

## Pull Requests

Before opening a PR:
1. Confirm the app starts with `docker compose up -d`.
2. Confirm kernel health responds at `GET /health`.
3. Confirm worker health responds at `GET /api/worker/health`.
4. If changing WebUI, confirm it builds in `agent-webui/` with `npm run build`.
5. Update `README.md` for user-facing behavior changes.
6. Update `CHANGELOG.md` for any notable changes.
7. Add/update baseline evidence if perf behavior is changed.

PR checklist:
1. What changed and why
2. Risks / compatibility notes
3. Validation steps + output

## Commit Style

Recommended prefixes:
- `feat:`
- `fix:`
- `docs:`
- `refactor:`
- `chore:`

## Branching

Recommended branch naming:
- `feat/<short-name>`
- `fix/<short-name>`
- `docs/<short-name>`

## Licensing

This repo uses split licensing. See `LICENSE` for the directory map.
