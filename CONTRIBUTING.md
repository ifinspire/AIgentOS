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

## Running Tests

Unit tests live in `tests/` and run inside the `kernel-test` Docker service,
so there is no dependency on a local Python environment.

```bash
# Full suite — run before opening a PR.
docker compose run --rm kernel-test

# BVT only — fast gate (~1s). Happy-path canaries of critical components.
docker compose run --rm kernel-test pytest -v tests -m bvt

# Deep P0 layer — everything critical that isn't a smoke test.
docker compose run --rm kernel-test pytest -v tests -m "p0 and not bvt"
```

Tests are tagged with two pytest markers (registered in `pytest.ini`):

- **`bvt`** — Build Verification Tests. Happy-path smoke signals that answer
  "did this build produce a working kernel?" — FastAPI boots, storage schema
  is alive, MCP/text/prompt happy paths work. Sub-second and flake-free, so
  they can run as an early gate on every commit.
- **`p0`** — Priority 0. The release-blocking suite: every BVT plus edge
  cases, defensive behaviors, regression guards for prior incidents, and
  multi-step correctness walks.

The containment is `bvt ⊆ p0`: every BVT is also P0, but most P0s are not
BVTs. When adding a test, mark it `p0` if its failure should block a release;
additionally mark it `bvt` only if it is a happy-path smoke test for a
critical component.

## Pull Requests

Before opening a PR:
1. Confirm the app starts with `docker compose up -d`.
2. Confirm kernel health responds at `GET /health`.
3. Confirm worker health responds at `GET /api/worker/health`.
4. Run the full unit test suite: `docker compose run --rm kernel-test`. For a quick sanity check during development, `pytest -v tests -m bvt` runs only the BVT lane.
5. If changing WebUI, confirm it builds in `agent-webui/` with `npm run build`.
6. Update `README.md` for user-facing behavior changes.
7. Update `CHANGELOG.md` for any notable changes.
8. Add/update baseline evidence if perf behavior is changed.

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
