# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-04-29

Major scheduler upgrades, a new packaged CLI, lazy providers for tier-1
lean imports, full docs depth pass.

### Added
- **`operonx-pack`** — packaged CLI (`pip install operonx` registers it)
  for serialising `@graph` factories to the JSON spec the Rust runtime
  loads. Pytest-style `module.path::symbol` positionals, optional
  `=customkey` to rename the bundle key, default-stdout / `-o PATH` for
  file output, `--no-bootstrap` for pure-compute graphs. Replaces the
  previous standalone `tools/dump-graph.py`.
- **`operonx.core.types.ChatMessage`** + `ChatRole` Literal — provider-
  neutral chat-message TypedDict. Landing pad for the v0.7+ LLMOp
  converter layer; today's providers still emit `openai.types.chat.*`
  for back-compat.
- **`scripts/bench/`** — Python ↔ Rust e2e bench: `generate.py` dumps 22
  shared `graph.json` patterns, `main.py` runs Python, `cargo run` runs
  Rust. Final headline: Rust wins every pattern. **3.2×** on linear,
  **1.5–1.7×** on fan-out and pure-noop nested @graph, **2.0–2.7×** on
  `if_()`-routed branching, **11–12×** on production-shape, **15–20×**
  under mixed CPU contention, **17–38×** on pure-compute matmul.
- **`examples/{python,rust}/exNN_*/`** — standalone project templates.
  Per-example `pyproject.toml` / `Cargo.toml`, single-file `main.py` /
  `main.rs`, per-example `.env.example` + `resources.yaml` where
  relevant. `examples/rust/.cargo/config.toml` patches `operonx` to
  the workspace path for in-repo development; users copying an example
  out of the repo pick up the registry version.
- Per-language indexes — `examples/python/README.md`,
  `examples/rust/README.md` — extras / feature mapping per example,
  cd-and-run command, runtime-status caveats per Rust example.
- `docs/guide/00b-patterns.md` — public Patterns reference page lifted
  out of CLAUDE.md (decorators, edges, refs, output mapping,
  iteration, `@graph.loop`, `if_()` routing, end-to-end composition).
- Mermaid diagrams across `docs/architecture/` — overview /
  execution-flow / state-model / streaming / rust-python pages each
  carry one diagram. Mkdocs wires the mermaid loader via
  `extra_javascript` plus a tiny init script that re-renders on
  Material's light/dark palette toggle.
- `published-smoke` CI workflow — installs each tier-1 example
  (ex01, ex02, ex13) from its own `pyproject.toml` against the
  published wheel, asserts `operonx` resolves to site-packages and
  no provider SDKs leak in, runs `python main.py`. Triggers on
  `workflow_dispatch`, `workflow_run` after `Publish` succeeds, and
  `push` to `main` (when example or workflow files change). Does
  **not** gate `publish.yaml`.

### Changed
- **Rust scheduler — sync-op inline fast-path.** `OpBound::Sync` ops
  bypass `tokio::spawn` + semaphore + await; events go onto the queue
  via `try_send`. Per-op floor dropped from 44 µs to 15 µs.
- **Rust scheduler — nested `@graph` precompute + fast-path
  dispatch.** `GraphScheduler::new` recursively builds a child
  `GraphScheduler` for every nested `OpType::Graph` op at parent
  construction time (no more process-wide static cache). New
  `GraphScheduler::run_collect` runs the sub-scheduler inline in the
  caller's task with a tap-only `FrameSender` — no `tokio::spawn`, no
  `mpsc::channel(64)` allocation, no `pump_loop`, no UUID gen, no
  middleware. Mirrors Python's `child._scheduler.run(state, ctx)`
  shape. Pure-noop nested patterns are now **1.5×** Rust-faster (was
  parity); production-shape jumped from 7.8× to **11×**.
- **Rust scheduler — real `if_()` branch routing.** New ref-transform
  evaluator in `resolve_ref` covering `eq` / `ne` / `lt` / `le` / `gt`
  / `ge` / `contains` / `getitem` / `getattr` / boolean (`and_` /
  `or_` / `not_`) / arithmetic (`add` / `sub` / `mul` / `truediv` /
  `floordiv` / `mod` / `pow` and r-variants) / unary (`neg` / `pos` /
  `abs`). Truthiness matches Python. New `OpType::Branch` dispatch
  evaluates each case's condition Ref, picks the first truthy
  `target` (or `default`), emits `{"__branch_target__": "<name>"}`;
  the existing scheduler edge router fires only the matching
  `EdgeType::Condition` edge. `branching_*` is now 2.0–2.7× faster
  AND semantically correct (was firing every branch with soft-edge
  merge picking by coincidence).
- **`#[op]` macro hygiene.** `operonx` now re-exports `inventory`
  (`pub use ::inventory;`); `#[op]` and `#[resource]` macros emit
  `::operonx::inventory::submit!` instead of bare `::inventory::`.
  Consumer crates no longer need `inventory = "0.3"` as a direct dep.
- **Lazy provider exports.** `operonx/providers/__init__.py` is now
  fully `_LAZY_BACKENDS` (configs + factories + base classes + ops
  + heavy backends). The eager `from operonx.providers.auth/.../...
  import …` lines are gone. `import operonx.providers` on a tier-1
  install no longer pulls `httpx` / `openai` / `numpy`.
  `auth/factory.py` defers the `keycloak.py` import (which pulls
  `httpx`) inside `create_auth()` with a typed missing-dep
  `ImportError`.
- **`__version__` source of truth.** `operonx/__init__.py` reads
  `importlib.metadata.version("operonx")` with a
  `PackageNotFoundError` fallback to `"0.0.0+unknown"`. The
  `pyproject.toml` `version` is now the single source of truth.
- **API docs rendering.** mkdocstrings options switched to richer
  rendering: `docstring_section_style: table`,
  `members_order: source`, `group_by_category: true`,
  `show_category_heading: true`, `show_root_full_path: false`,
  `show_symbol_type_heading: true` /
  `show_symbol_type_toc: true`. Each provider op now surfaces its
  `Op.of()` classmethod; `Operon` shows all public methods
  (`run` / `start` / `use` / `batch` / etc.); state markers
  (`START` / `END` / `PARENT` / `PENDING`) are documented in a
  dedicated table.
- **Outdated runtime-parity caveats** in `examples/README.md` —
  nested `@graph` moved to "recently closed"; `if_()` bullet now
  reflects the partial-deserialise + every-branch-fires reality
  pre-this-release (now superseded by real branch routing above).

### Fixed
- `operonx/__init__.py:51` no longer hardcodes `0.6.1` — the
  long-standing drift from `pyproject.toml` is gone.
- `examples/python/{ex07,ex12}/resources.yaml` — added
  `dimensions: 1536` so the OpenAI-flavoured embedding config passes
  VLLMEmbedding's runtime validation at serialise time.
- `examples/rust/ex07_embeddings_and_rag/src/main.rs` — handles a
  missing `rerank` bundle entry gracefully (no longer panics on
  `.expect`).
- `examples/rust/ex09_agent_workflow/src/main.rs` — refactored to
  load the single `agent` graph once and run it against three
  scenario inputs.
- `docs/api/providers.md` — fixed a stale mkdocstrings reference
  (`operonx.providers.{chat,ask}` → `operonx.providers.ops.{chat,ask}`)
  surfaced by the lazy-providers refactor.

### Removed
- `tools/dump-graph.py` — replaced by `operonx-pack`. The `tools/`
  directory is gone.
- `cpu_chain_*` patterns and the `bench_hash` op from `scripts/bench/`
  — `hashlib.sha256` is OpenSSL C and Rust `sha2` is pure Rust, so
  hash-chain benches measured the hash library, not the engine.
  `matrix_chain_*` (naive O(n³) mat-mul, no library shortcut on
  either side) covers CPU-chain stress fairly. Same swap for
  `cpu_contention_*` (heavy branches now use `bench_matrix(30)`
  instead of `bench_hash`).

## [0.6.3]

Unreleased — folded into 0.7.0 above.

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

[Unreleased]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.6.2...v0.7.0
[0.6.2]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/batman1m2001-cyber/Operonx/releases/tag/v0.6.0
