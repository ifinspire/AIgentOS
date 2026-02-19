# Changelog

All notable changes to this project are documented in this file.

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
