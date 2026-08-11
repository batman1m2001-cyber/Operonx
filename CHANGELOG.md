# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **A `Ref` nested inside a dict or list is now rejected at construction
  instead of silently degrading to a literal.** Operonx wires one param
  to one state cell holding one pull-ref, so `my_op(cfg={"a": src["x"]})`
  had no cell to put `src["x"]` in. Three things broke at once, none of
  them noisily: the op received the `Ref` object rather than the value,
  no dependency edge was created (so ordering was unguaranteed), and
  `GraphOp._validate`'s cross-graph scope check never saw it — a `Ref`
  to an op in another graph passed hermeticity validation. It now raises
  `TypeError` naming the param, the exact path, and the source var.

  Only the *value* side is scanned. `my_op(inputs={"a": src["x"]})` is
  the params mapping, not a container holding a ref, and is unaffected.

  Found while probing `InterruptOp(payload={"tool": …, "args": …})` for
  the agent work — a human approving a destructive tool call was shown
  `Ref` objects instead of the tool name and arguments. It was never an
  `InterruptOp` bug; every op behaved this way.

  Supporting the nested form properly (hoisting each buried ref into its
  own cell and reassembling the container at read time) is a larger
  change that also alters the `serialize()` wire format operonx-rs
  consumes. This release makes the broken form loud; it does not yet
  make it work.

## [1.2.0] - 2026-08-11

Removes the two backend-named ops. See
[MIGRATION.md](MIGRATION.md#migrating-to-operonx-120) for recipes.

### Removed (BREAKING)

- `OnnxOp` — write a bare `@op` around
  `operonx.providers._utils.onnx.load_onnx_session`. ONNX remains a
  backend for `EmbeddingOp` and `RerankOp` via `api_type: onnx`.
- `TritonOp` — write a bare `@op` around
  `operonx.providers.triton.TritonClient.get(url)`, which supplies the
  pooled gRPC client, dtype translation and output decoding.

Both named their *transport* rather than a semantic: the op name told
you the runtime instead of the intent, so every backend needed its own
op and callers had to know the transport to pick one.

> **Removal was brought forward.** The warnings shipped in 1.1.0 said
> "removed in 2.0.0". If you are pinned `operonx>=1.x` and use either op,
> upgrading to 1.2.0 **will** break you — pin `operonx<1.2.0` until you
> have migrated.

### Changed (BREAKING)

- `OpType` cleanup. Removed `for`, `while`, `stream` (superseded in 1.0.0
  by back-edge loops, generator ops and `Ref.parallel()`), `parser`
  (`ParserOp` went in 1.0.0), and `milvus`, `mongo`, `s3` (named
  backends, never had ops). Added `interrupt` and `emit`, which
  `InterruptOp` / `EmitOp` have set since 1.0.0 without the Literal
  listing them — the drift this ends. Only affects code reading the
  Literal directly.
- `ParserError` now reports `op_type="code"` rather than `"parser"`.

## [1.1.0] - 2026-08-11

Retrieval release: a two-store RAG stack, plus the first step of the op
taxonomy cleanup. See [OP_TAXONOMY_REFACTOR_PLAN.md](OP_TAXONOMY_REFACTOR_PLAN.md).

### Added — retrieval

- `VectorSearchOp` — vector similarity search returning `ids`, `scores`,
  and `metadata`, index-aligned and best-match first. Backend-native
  filters, never translated.
- `DocFetchOp` — fetch records by primary key from a store of record.
  Returns rows **in the order of the ids given**, and reports ids that
  matched nothing in `missing` rather than silently returning a shorter
  list.
- `operonx.providers.vector_stores` — `BaseVectorStore` (with a
  per-backend `bound` hint), config, lazy-import factory, and the
  **FAISS** (no server, `bound="cpu"`), **pgvector**, and **Qdrant**
  backends.
- `operonx.providers.doc_stores` — `BaseDocStore` (order restoration
  lives in the base so no backend can reintroduce misalignment), config,
  factory, and the **Postgres** and **memory** backends.
- `reorder_by_ids` / `partition_by_ids` — the ordering helpers
  `DocFetchOp` uses internally, exported for custom fetch ops.
- `operonx.providers.triton` — `TritonClient` with a process-cached gRPC
  channel and dict-in/dict-out `infer()`, plus pure dtype/decode helpers.
- New extras: `operonx[faiss]`, `operonx[pgvector]`, `operonx[postgres]`,
  `operonx[qdrant]`.
- `OpType` gains `"vector-search"` and `"doc-fetch"` (additive only).
- Example [`ex16_rag_pipeline`](examples/python/ex16_rag_pipeline/) — the
  full pipeline, runnable with no servers.

### Design notes

- **The vector index is derived data.** It holds vectors, ids, and small
  *filterable* metadata — never document content, which lives in the
  store of record. This avoids the most common silent RAG bug: a document
  updated in your database but stale inside the index's payload. It also
  makes hydration its own trace span, where it usually costs more
  wall-clock than the search itself.
- **Filters are backend-native, with no portable DSL.** A DSL leaks, and
  a mistranslation is silent — a filter that fails to apply returns
  *more* rows, which in a multi-tenant system is a data leak rather than
  a warning. Every backend validates its own dialect and raises on
  shapes it does not recognise; nothing degrades to "no filter".
- **FAISS refuses filters** rather than post-filtering in Python, which
  would silently return fewer than `top_k` hits.

### Deprecated

- `OnnxOp` — removed in 2.0.0. Write a bare `@op` around
  `operonx.providers._utils.onnx.load_onnx_session`. ONNX remains
  available as a backend for `EmbeddingOp` and `RerankOp`.
- `TritonOp` — removed in 2.0.0. Use `VectorSearchOp` where it applies,
  or a bare `@op` around `operonx.providers.triton.TritonClient.get(url)`
  (~15 lines, same pooled client).

Both still work in 1.1.x and emit a `DeprecationWarning` naming the
replacement.

### Fixed

- `docs/guide/04-rag.md` documented a `resources.yaml` format that does
  not exist (nested `embeddings:` / `llms:` blocks instead of flat
  `embedding:<name>:` keys) — copying it produced a load error.
- Removed the stale `ask` export from `operonx.providers`; the helper was
  deleted in 1.0.0, so `from operonx.providers import ask` raised
  `AttributeError`.
- Provider tests are auto-marked `integration` by their conftest, which
  CI's `-m "not integration"` selector excludes. Mock-only suites now
  carry the `unit` marker the conftest honours — 83 previously
  never-executed tests now run on every PR.

## [1.0.0] - 2026-08-10

Milestone release: state observability, HITL primitives, LangGraph-style
back-edge loops, structured-output LLMOp, and cleanup of deprecated
surfaces. See [MIGRATION.md](MIGRATION.md) for the upgrade recipe.

### Added — Phase 1 (state)
- `PARENT.declare(**vars, reducers={...})` — shared cells with optional
  fan-in reducers. Replaces `PARENT.shared()`.
- `operonx.reducers` — `add_messages` (LangGraph-compatible id-upsert
  + `RemoveMessage` / `REMOVE_ALL_MESSAGES` sentinels), `dict_merge`,
  plus support for `operator.add` / `operator.or_` from stdlib.

### Added — Phase 2 (observability + HITL)
- `Checkpointer` protocol + `InMemoryCheckpointer` with per-step delta
  storage; `get_state(step)` folds, `get_updates(step)` returns delta,
  `list_steps()` enumerates. Zero overhead when no checkpointer bound.
- `MemoryState._write_cell` funnel + write-observer bus. All cell
  mutations (including `SCRATCH[k] = v`) flow through it.
- `@op(exclude=..., include=..., observe_max=...)` — filter observability
  at emission source. Polymorphic list-or-dict; mutual-exclusion of
  include/exclude. `ObserveBudgetExceeded` (inherits `BaseException`)
  as a circuit breaker for runaway generator ops.
- `InterruptOp` — HITL suspend/resume via `asyncio.Future`.
- `EmitOp` — fire-and-forget custom events.
- `engine.stream(mode="updates" | "values" | "frames" | "custom")` +
  `engine.invoke()` alias.

### Added — Phase 3 (loops)
- Build-time cycle rewrite: write a back-edge inside `@graph` and the
  Phase 3 pass compiles it into a hidden `_GraphLoop`. LangGraph-style
  agent loops without the visible loop wrapper.
- `@graph(strict_dag=True)` opt-out for fail-fast on accidental cycles.

### Added — LLMOp structured-output layer
- `LLMOp.of(fields=..., parser=..., validators=..., max_retries=...,
  retry_hint=True)` — inline parse + validate + error-guided semantic
  retry on the same resource (Instructor-style).
- `LLMRefusalError` and `ValidatorError` exception classes; refusal
  detected via `finish_reason` in `{content_filter, safety}` or
  non-empty `extras.refusal` (structural, not content-heuristic).
- `operonx.providers.parsing` — pure functions
  (`parse_json` / `parse_xml` / `parse_yaml`, `ExtractField`,
  `extract_value_by_path`, `convert_type`, `apply_validators`,
  `parse_and_extract`) for text-only parsing without an LLM call.

### Changed
- Fallback trigger narrowed: `fallback=[...]` fires only on refusals,
  content-filter blocks, or exhausted transport (SDK-side). Parse /
  validator failures NO LONGER trigger fallback — they use
  `max_retries` on the same resource.
- Transport-level retries (429 / 5xx / timeout) are delegated to the
  underlying SDK; LLMOp does not add its own transport-retry knob to
  avoid double-retry surprises.

### Removed (BREAKING)
- `PARENT.shared(**vars)` — use `PARENT.declare(**vars)`.
- `GraphOp.loop(name=..., until=..., **initial_state)` constructor —
  use a back-edge inside `@graph` and let the rewrite synthesize the
  loop. The `_GraphLoop` type still exists internally.
- `@graph(until=..., max_iterations=...)` decorator surface — replaced
  by (a) back-edge for control-flow loops or (b) `LLMOp(max_retries=N)`
  for LLM parse/validate retry.
- `ParserOp` class + `ParserType` alias — parsing lives inline in
  `LLMOp(fields=..., parser=..., validators=...)`; pure functions for
  standalone use are in `operonx.providers.parsing`.
- `ask()` helper (`operonx.providers.ops.ask`) — subsumed by
  `LLMOp.of(fields=..., max_retries=...)`.

### Fixed
- Shared-cell reducer degraded to LWW on nested-ctx writes (from
  Phase 3 loop iterations) — `_write_cell` now reads `old` via
  `Cell.__getitem__` which handles the shared→DEFAULT_CONTEXT mapping.

## [0.11.0] - 2026-07-30

Branch-graph ergonomics release. Two additive, non-breaking features
that eliminate a class of silent-deadlock bugs and a class of
target-named-twice boilerplate at every branch-fan-in site in real
callbot / agent-workflow graphs. Both compose cleanly with the existing
`if_/else_` primitive and require zero migration for existing code.

### Added
- **Auto-soften branch-merge edges** at `GraphOp.build()`. When two
  predecessors of a merge op trace back to a common `BranchOp` ancestor
  via disjoint first-hop children, the incoming edges are automatically
  flipped to `soft` — the runtime pattern users had to remember to write
  with `~` on every branch fan-in (e.g. `denoise >> ~picker`,
  `skip_stt >> ~picker`) is now inferred from graph shape. Missing the
  `~` used to cause a silent deadlock at runtime; the build-time pass
  kills that whole bug class. Escape hatches: `GraphOp(auto_soft=False)`
  per-graph, `graph.add_edge(src, dst, hard=True)` per-edge. See
  [`docs/design/AUTO_SOFT_BRANCH_MERGE.md`](docs/design/AUTO_SOFT_BRANCH_MERGE.md).

- **Inline `if_/else_` branch API** — branch declarations can now drop
  directly into `>>` chains with op-instance targets, and the framework
  auto-adds the `branch → target` condition edges. The old `route = if_(cond, "name")`
  standalone form still works for forward-reference and named-branch
  cases. Auto-name resolves via `auto_name()` LHS → `route_N` per-graph
  counter fallback (with a source-parser false-positive guard to prevent
  a nearby `m = _mk(...)` line from being incorrectly captured). Example:
  ```python
  # Before
  stt_route = if_(cond, asr).else_(skip_stt)
  START >> source >> stt_route
  stt_route >> asr >> denoise >> picker
  stt_route >> skip_stt >> picker

  # After
  START >> source >> if_(cond, asr).else_(skip_stt)
  asr >> denoise >> picker
  skip_stt >> picker
  ```
  Combined with auto-soften above, the callbot's branch/merge wiring
  block dropped by 18 lines with zero behavioural change. See
  [`docs/design/BRANCH_INLINE_API.md`](docs/design/BRANCH_INLINE_API.md).

- **`EdgeConfig.auto_soft` and `EdgeConfig.pinned_hard`** — Python-side
  debug fields on edges. Not serialized to Rust; useful for tooling and
  build-time log analysis.

### Docs
- `docs/design/AUTO_SOFT_BRANCH_MERGE.md` — full spec, algorithm,
  limitations, empirical validation on the callbot.
- `docs/design/BRANCH_INLINE_API.md` — full spec, name-resolution rules,
  backward-compat guarantees.
- `AGENT_EXTENSION_PLAN.md` — op-native design for extending operonx
  into a full agent framework, grounded in a deep inspection of
  hermes-agent, opencode, agent-harness, smolagents, openclaw.

### Notes
- Zero Rust runtime changes. `EdgeConfig.soft` is serialized verbatim
  after the auto-soften pass mutates it; auto-added branch edges look
  identical to user-authored ones in the exported graph JSON.
- Backward compatibility: all 971 pre-existing tests pass. All existing
  manual `~` marks continue to work (the pass skips already-soft edges).
  All string-target `if_(cond, "name")` call sites work unchanged.
- Callbot dev branch verifies end-to-end: all 5 manual `~` marks
  removed, all 4 branch declarations inlined, 204 tests still pass.

## [0.8.5] - 2026-05-31

Bug-fix release. Closes a SCRATCH-propagation bug in nested @graph
dispatch that broke every workflow using subgraphs with SCRATCH-backed
inputs (educa_reminder callbot, ahamove_hr, every agent that reads
`current_state` / `intent_retry_counts` / `last_agent_response` inside
its agent_turn subgraph).

### Fixed — Rust
- **Nested @graph dispatch now inherits parent's SCRATCH**
  (`core/ops/graph/task_scheduler.rs::run_collect`). Previously the
  child sub-scheduler was started with a fresh empty
  `Arc<Mutex<HashMap>>` so every `SCRATCH[key]` read inside the subgraph
  returned `Null`, breaking every educa_reminder-style agent whose
  state machine inputs are SCRATCH refs at the subgraph level. The fix
  threads the parent's SCRATCH Arc through `execute_op` →
  `run_collect`, matching Python's `child._scheduler.run` shared-Arc
  semantics. `run_collect` keeps a fresh-SCRATCH fallback for the
  legacy standalone-test callers that pass `None`.

## [0.8.4] - 2026-05-31

Bug-fix release. No API changes; behavioural parity with Python tightened
on conditional branch inputs.

### Fixed — Rust
- **Conditional-branch input refs now respect `default` fallback**
  (`core/ops/graph/task_scheduler.rs::resolve_inputs`). When a graph
  routes through a branch (`if_/else_`), downstream ops have refs that
  point at BOTH branches' outputs; only the taken branch fires, the
  other op's outputs are never set. Previously the resolver would error
  out the moment it hit a missing ref on the untaken branch, dropping
  the entire downstream chain. The resolver now falls back to `default`
  (or `Null` for non-required inputs) when the ref's source op produced
  no value at the runtime ctx — matching the Python parity behaviour
  the educa_reminder callbot graph relies on for `picker.audio_text`
  (asr branch) and every merge-style op (`merge_response`, `merge_turn`,
  `merge_intent`, `merge_overlap`, `merge_pending`).

## [0.8.3] - 2026-05-31

Rust-side Phase-1 sync release. Python crate stays at 0.8.2 — the
Rust crate version bumps to 0.8.3 to mark the merged sync work
shipped on top of the 0.8.2 parity baseline. No Python API changes.

### Added — Rust
- **Structured exception hierarchy** (`core::exceptions`) mirroring
  Python `OpError` / `ParserError` / `CodeError` / `BranchError` /
  `ConditionError` / `IterationError` / `PromptError` /
  `EmbeddingError` / `RerankError`, each carrying op_name + context +
  original_error. 21 new internal tests.
- **Ref evaluator gaps closed** — `Apply` / `Call` / `MatMul` /
  `RMatMul` variants + a real `GetAttr` separate from `GetItem`.
  AttributeError parity for missing keys. 11 new internal tests +
  shared spec fixture `core/refs/getattr_dict`.
- **Frame / Interrupt API parity** — typed `Interrupt` struct with
  canonical JSON shape (`__interrupt__: { ctx_to_cancel, reason }`),
  `ExecutionHandle::scratch()` + `ExecutionHandle::interrupts()`
  accessors backed by a shared `Arc<Mutex<HashMap>>` SCRATCH.
- **Sequential-edge cancel fix** — port of Python 0.8.1's
  `_sweep_ctx` fix that advances `seq_queues` on Interrupt cancel.
  Regression test included.
- **Event-stream tracing pipeline** (`core::tracing::{events,
  emitter, pipeline, processors, legacy, exporters::local_file}`) —
  full port of Python 0.8.0's tracing redesign. Replaces the legacy
  `collector` + `flush_worker` + `labels` modules (kept compiling
  during the migration). Processors: drop / redact / sample /
  truncate / group. Flush strategies: AtScheduledExit /
  FlushOnSize. JSON file exporter writes
  `~/.operonx/traces/<request_id>.json`.
- **LangfuseExporter** (`telemetry::exporters::langfuse`) — full
  port behind `langfuse` feature. trace-create + span-create batches,
  generation-create on LlmUsage events, Basic-auth Langfuse public
  ingestion endpoint, parent_observation_id walking via ctx tuple
  longest-prefix-first.
- **ParserOp** (`core::ops::transform::parser_op`) — port of
  Python's parse_json / parse_xml / parse_yaml + ExtractField path
  walker + @DEFAULT validators + convert_type coercion. quick-xml
  state machine for XML. Scheduler dispatch routes `OpType::Parser`.
  14 unit + 10 integration tests + 3 shared spec fixtures.
- **TritonOp gRPC client** (`providers::ops::triton`) — full port
  behind `triton` feature. tonic + vendored KServe v2 proto, pooled
  `Channel` per `TRITON_URL`, FP32 / FP64 / INT32 / INT64 / BYTES
  tensor codec, ResourceHub.get_config integration. End-to-end test
  with in-process tonic mock server.
- **OpenAI SSE streaming** (`providers::llms::openai::stream`) +
  `LLMOp` stream-mode wiring through `ExecutionHandle`. Match Python
  chunk schema.
- **Anthropic Messages API** (`providers::llms::anthropic`) — full
  port with SSE streaming, system-prompt split, stop_reason map,
  cache_read / cache_creation token surface.
- **Azure OpenAI** (`providers::llms::azure`) — reuses the OpenAI
  body+parser, URL = `<base>/openai/deployments/<model>/chat/
  completions?api-version=<v>`, api-key header auth.
- **TEI embedder** (`providers::embeddings::tei`) — POST `/embed`
  with `{inputs, truncate}` body.
- **TEI / vLLM / Pinecone rerankers** — POST `/rerank` family with
  Cohere-shape body, sorts desc + truncates, vendor-specific auth.
- **Keycloak token provider** (`providers::auth::keycloak`) — OIDC
  client_credentials grant, dot-path token extraction, background
  refresh task (abort-on-drop), cached lazy-fetch on first call.

### Test footprint
136 → 254 tests across the workspace (with `--features triton`).
13 → 16 shared spec fixtures consumed by both Python and Rust
runners. Parity contract maintained — every new Rust feature ships
with a fixture covering its user-visible behavior.

### Deferred
- Gemini LLM, OpenAI Batch coordinator, ONNX shared backend, and
  Langfuse prompt-manager remain Phase-5b stubs. None are callbot
  blockers; they error with a clear "not yet implemented" message.

## [0.7.1] - 2026-04-29

Follow-up to v0.7.0 — bug fixes + perf precompute work, all Python ↔
Rust parity preserved (22 / 22 bench patterns byte-equal). No public
API changes.

### Fixed
- **Rust LLM examples returned empty `{}`** (ex03 / ex04 / ex07 / ex08
  / ex09 / ex10 / ex12). `operonx::bootstrap()` didn't call
  `providers::registry::register_all()`, so the resource hub failed
  every `llm:gpt-4o-mini` lookup with `no factory registered for
  category 'llm'` before any HTTP request. Bootstrap now registers
  every built-in provider plugin idempotently, mirroring Python's
  "import triggers registration" pattern. Verified end-to-end against
  real OpenAI calls.
- **Generator ops collapsed to a single frame on Rust.** `is_generator:
  true` ops were treated as regular code ops; the `for_loop` /
  `map_op` scenarios in ex05, both `ex11.iteration` /
  `partial_failure`, ex14, and ex15 all returned `{}` because
  downstream per-item ops never dispatched. New `fan_out_value()`
  helper plus a switch in both the inline-sync fast-path and the
  spawn (io/cpu) path: generator ops now return `Value::Array` and
  the scheduler emits one Frame per element on a fresh `(parent_ctx,
  "yield_N")` sub-context. Empty array = zero frames (matches
  Python's skipped `yield`, used by ex15's `vad`).
- **Stale fixtures + pin** in the rust example bundles. `ex01`'s
  `inputs.json` and `main.rs` op params used `name` while the current
  Python factory takes `who`; `ex13` used `input` instead of `val`.
  Every `examples/rust/*/Cargo.toml` was pinned to `operonx =
  "0.6.2"` — semver-incompatible with the workspace's `0.7.x`, so
  `[patch.crates-io]` silently dropped through to the published 0.6.3
  wheel and the older `#[op]` macro errored on bare
  `::inventory::submit!`. Bumped to `0.7.1`.
- **`extras smoke (anthropic)` regression on PR #1.**
  `operonx/providers/llms/response.py` did a top-level `import
  aiohttp` that the anthropic extra (which ships `httpx`) didn't
  provide; deferred via `TYPE_CHECKING` + lazy import inside the demo
  helpers.
- **Anthropic + rerank integration tests no longer fail when their
  config is unavailable.** Anthropic `*_real` tests treat the
  `ci-dummy-…` key the providers conftest plants as "no key" and skip;
  the rerank `with_hub` test probes `hub.get("reranking:bge-m3-onnx")`
  up front and skips with the real reason instead of letting
  `_process()` swallow the model-dir-missing error.

### Changed
- **Rust scheduler — 5 precompute wins on the hot path.** All four
  pieces moved from per-frame work to `GraphScheduler::new` (per-engine
  build):
  1. `initial_ready_count` pre-converted from `BTreeMap` to `HashMap`
     — clones once per never-seen `ContextId` instead of converting.
  2. `RuntimeState::with_capacity(n)` — slot map pre-sized to
     `Σ (inputs + outputs)` across ops, eliminating the resize cycle.
  3. `seq_queues` / `seq_active` / `collect_bufs` pre-sized to the
     graph's edge counts.
  4. `edge_policies: HashMap<(src, dst), StreamPolicy>` cached at
     construction; `route_edge_async` does an O(1) lookup instead of
     re-walking `dst.inputs`.
  5. **Compiled ref pipeline.** Walks every `RefConfig` (op-input
     refs + branch case conditions, recursively into nested refs) and
     produces an enum-tagged `CompiledRef` / `CompiledOp` /
     `TransformKind` chain at construction. `__PARENT__` already
     substituted with the graph key. Per-op `Vec<InputSlot>` plan
     classifies each input as `Ref / Lit / Default / RequiredMissing
     / Null` up front. Runtime drops `transform.name.as_str()` matches
     for enum dispatch and replaces `for (var, param) in
     op_cfg.inputs` + per-input `match param.ref_config` with a tight
     slice walk.
- Net bench delta: scheduler-bound patterns (linear chains, branching,
  small parallel) ~5–12 % faster vs the v0.7.0 baseline; CPU-bound
  patterns (matrix chain) unchanged because they're dominated by
  matmul time. Numbers within run-to-run noise on the existing
  bench set; transform-heavy graphs (long ref chains) should see the
  bigger win from the compiled-ref dispatch.

### Added
- **`scripts/bench/parity.py` + `--probe` mode on the bench binary.**
  Runs every `<name>.graph.json` / `<name>.inputs.json` pair through
  both runtimes (Python `@graph` factory + Rust `operonx-bench
  --probe`) and diffs the output dicts key-by-key. Reports
  `PASS <name>` / `FAIL <name>` per pattern. Used to verify the
  precompute changes don't perturb output.

### Removed
- `published-smoke` CI workflow (was racy by design — fired on
  push-to-main before `Publish` had pushed the wheel; site-packages
  assertion picked up the source tree from CWD anyway). Regular
  tests + extras-smoke matrix already cover the surface.

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

[Unreleased]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.7.0...v1.0.0
[0.7.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.6.2...v0.7.0
[0.6.2]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/batman1m2001-cyber/Operonx/releases/tag/v0.6.0
