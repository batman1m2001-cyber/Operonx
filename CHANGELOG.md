# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.2] - 2026-04-28

### Fixed
- Publish workflow: added a `force` input on `workflow_dispatch` so a release
  can be re-run when a version-bump commit and a follow-up commit land in the
  same push (the diff-based detector otherwise sees the version as unchanged
  at `HEAD~1` and skips both publish jobs). Recovery path:
  `gh workflow run publish.yaml -f force=true`.
- README badges pinned to `?branch=main` so the shields endpoint resolves
  correctly; added a Docs badge linking to the published GitHub Pages site.

## [0.6.1] - 2026-04-28

### Added
- Repository readiness: pre-commit hooks (ruff + cargo fmt + advisory clippy;
  `-D warnings` flips on once the ~25 outstanding port-era lint debts clear),
  codecov configuration, CHANGELOG, CODE_OF_CONDUCT, public-facing docs site (mkdocs
  Material with mkdocstrings, full guide + architecture + API reference).
- `[standard]` extra — recommended production install (OpenAI + Langfuse + OTEL + serve).
- `[all]` extra now includes Anthropic, Gemini, Bedrock, ONNX, Langfuse, OTEL, serve
  (was previously missing the LLM provider extras).
- `[docs]` extra (mkdocs + mkdocs-material + mkdocstrings) for local doc development.
- `extras-smoke` CI matrix verifies each `pip install operonx[X]` works in a fresh venv.

### Changed
- All optional providers are now lazy-loaded via module-level `__getattr__`.
  Installing only `operonx[anthropic]` no longer requires numpy / onnxruntime / torch.
- Tests under `tests/internal/providers/` are auto-marked `integration` and skipped
  unless API credentials are configured.
- README, CONTRIBUTING, SECURITY, and CLAUDE.md rewritten for the single-package layout.
- `[project.urls]` in pyproject.toml fixed to point at the renamed Operonx repo.
- `env.example` corrected: stale `OPERON_TRACES_DB` replaced with `OPERON_TRACES_DIR`
  (the env var the local tracer actually reads), and the `.env` loading note updated to
  reflect the explicit `operonx.bootstrap()` model.

### Fixed
- Provider extras no longer fail at import time when their non-shared dependencies
  are missing — error surfaces only on actual backend instantiation.
- Removed leftover `_is_hush_builder` flags, `hush_current_*` ContextVar names, and
  `test_hush_*` test names from the Hush-ai migration (now `_is_operonx_builder`,
  `operonx_current_*`, `test_operon_*`).
- Stale `chain` references in CLAUDE.md, README, and docs replaced with the actual
  helper name `chat` (renamed during the original migration but missed in user-facing
  docs).

## [0.6.0] - 2026-04-26

### Added
- `operonx.bootstrap()` — explicit, idempotent setup for `.env` + `resources.yaml`.
  Replaces implicit auto-load behaviour from earlier versions.
- `ResourceHub.auto()` classmethod — discover and install a hub from CWD.
- Disambiguated error model:
  - `ResourceHubWarning` when `resources.yaml` is absent or `${VAR}` interpolations
    can't be resolved at startup.
  - `EnvVarUnsetError` (subclass of `RuntimeError`) at resolve time, naming the
    variable, source path, and `.env` paths searched.
  - `RuntimeError("ResourceHub not initialized. ...")` at engine init when a graph
    references a resource without a hub installed.
- Rust mirror of the Resource Hub refactor (`OperonError::EnvVarUnset` typed
  variant, `bootstrap_state` module, `tracing::warn!` for missing `resources.yaml`).
- Single-package Python layout (`operonx`) and single-crate Rust layout (`operonx`).
  Migrated from the previous Hush-ai four-package / six-crate split.

### Changed
- `Operon(graph)` no longer auto-loads `.env` or `resources.yaml`. It is a pure
  orchestrator. Pure-compute graphs work hub-free; provider graphs require an
  explicit `bootstrap()` (or `ResourceHub.set_instance(...)`) before engine init.
- `ResourceHub.set_instance(hub)` is authoritative — `bootstrap()` and `auto()`
  respect a pre-installed hub and are idempotent.
- Repository renamed from `Operon` to `Operonx` (PyPI/crates.io name conflict
  with an unrelated project under the shorter name).

### Removed
- Implicit `.env` / `resources.yaml` loading from `Operon.__init__`.
- `Operon(graph, resources=...)` keyword argument — use `bootstrap(resources=...)`
  before constructing the engine.

[Unreleased]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.6.1...HEAD
[0.6.1]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/batman1m2001-cyber/Operonx/releases/tag/v0.6.0
