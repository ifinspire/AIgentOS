# Contributing

Thanks for contributing to AIgentOS.

## Scope

This repository is kernel-first:
- `kernel/` is the core engine.
- `agent-webui/` and `agent-prompts/` are optional layers.

Keep pull requests focused and scoped to one change when possible.

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

## Pull Requests

Before opening a PR:
1. Confirm the app starts with `docker compose up -d`.
2. Confirm kernel health responds at `GET /health`.
3. If changing WebUI, confirm it builds in `agent-webui/` with `npm run build`.
4. Update `README.md` for user-facing behavior changes.
5. Add/update baseline evidence if perf behavior is changed.

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
