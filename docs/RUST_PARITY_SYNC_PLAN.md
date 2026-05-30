# Phase 1 — Rust operonx ↔ Python operonx Sync (revised)

**Goal:** the Rust `operonx` crate at `/home/thanglq/Operon/rust/operonx/` runs every Python `operonx` feature with **identical observable behavior** — same JSON in, same JSON out, same emitted spans, same error messages. Faster + lighter is a verified *requirement* on top.

**Constraint:** only fix the Rust side. Python is canonical. Any Python↔Rust divergence is a Rust bug.

---

## 0. Framing — the real "outdated" gap

The literal gap is the **version delta**:

- Python: [/home/thanglq/Operon/pyproject.toml:7](../../../Operon/pyproject.toml#L7) → `version = "0.8.2"`
- Rust: [/home/thanglq/Operon/rust/Cargo.toml:8](../../../Operon/rust/Cargo.toml#L8) → `version = "0.7.1"`

`git log --oneline 23667fe..HEAD -- rust/` shows **0 Rust file changes since Python 0.8.0**. So the Rust crate is exactly **the 0.7.1 wire** plus whatever landed in the 0.8.0 commit ([bcac42f](../../../Operon/)) for SCRATCH + Interrupt mirrors. Nothing else.

**What's actually in the delta** (verified by reading [CHANGELOG.md](../../../Operon/CHANGELOG.md) + `git show bcac42f --stat` + `git log 16503c9..HEAD`):

| Change | Where it shipped | Rust mirror status |
|---|---|---|
| Tracing pipeline rewrite (events + emitter + pipeline + processors + legacy + exporters/local_file) | Python 0.8.0 | **MISSING** — Rust still has old `tracing/{collector, flush_worker, labels, local}.rs` |
| `LangfuseGroupedTimelineExporter` (805 LOC `telemetry/exporters/langfuse.py`) | Python 0.8.0 | **MISSING** — Rust has only basic `client.rs` |
| Removed old tracers: `LocalTracer`, `OperonEyesTracer`, `OTelTracer`, abstract `Tracer`, `labels.py` | Python 0.8.0 | **Still present in Rust** — need to delete to match |
| SCRATCH primitive | Python 0.8.0 | ✅ Present in Rust (`states/scratch_ref.rs` + scheduler integration) |
| Interrupt event API | Python 0.8.0 | ✅ Present in Rust (`SchedulerEvent::Interrupt` + `sweep_ctx`) |
| Sequential-edge cancel fix | Python 0.8.1 | **Unverified** — needs parity test |
| `format_log_data` short-circuit ndarray/tensor | Python 0.8.2 | N/A on Rust (no ndarray formatter) |

**What's still stub from earlier phases** (verified by `grep "not yet implemented"`):

- Provider ops: TritonOp, OpenAI streaming, ParserOp execution + scheduler dispatch
- Non-callbot providers: Anthropic, Azure, Gemini, OpenAI Batch, vLLM/TEI/ONNX embed/rerank, Pinecone, Keycloak refresh

**What is NOT a gap (verified)** — drop from earlier plan:

- ❌ "BaseOp::run() lifecycle on the trait" — the scheduler-inlined approach is intentional precompute design. CHANGELOG 0.7.0 documents **3.2× linear / 11–12× production / 17–38× pure-compute** wins from this architecture. Refactoring would regress benchmarks.
- ❌ "MemoryState port to replace RuntimeState" — `RuntimeState` is a `with_capacity`-presized hot-path layer per CHANGELOG 0.7.1 ("eliminating the resize cycle"). `MemoryState` exists as an external-observer surface; the two are correctly separate.
- ❌ "Ref evaluator port" — already 27/30 transforms shipped in 0.7.1 compiled-ref pipeline ([task_scheduler.rs:1502-1577](../../../Operon/rust/operonx/src/core/ops/graph/task_scheduler.rs#L1502)). Real work is 4 small transforms (apply/call/matmul/rmatmul) + a `getattr` semantics fix.

---

## 1. Baseline (verified before planning)

```
cd /home/thanglq/Operon
cargo build --workspace   → ok, 9 warnings, 0 errors
cargo test --workspace    → 136 tests, all pass
```

13 shared spec fixtures pass on both runtimes. Building on this.

---

## 2. Test contract (the architecture-parity guarantee)

User rule: "verify the architecture of rust is exactly the same like python side."

The contract has two pillars: **shared spec fixtures** + **mirrored internal tests**.

### 2.1 — Shared spec fixtures (the parity contract)

**Location:** `/home/thanglq/Operon/tests/spec/<area>/<name>/`

**Files per fixture:**
- `graph.json` — wire-format graph (committed)
- `inputs.json` — input bundle (committed)
- `expected.json` — expected output dict (committed)
- `builder.py` — Python factory `build_graph() -> GraphOp` (committed)
- `scratch.json` — optional, seeds engine.run(scratch=...)

**Python runner:** [tests/spec/test_fixtures.py](../../../Operon/tests/spec/test_fixtures.py) auto-discovers via `rglob("graph.json")`, imports `builder.py` via importlib, runs `Operon(graph).run(inputs, scratch)`, strips timing keys, asserts == expected.

**Rust runner:** [rust/operonx/tests/common/mod.rs::run_fixture()](../../../Operon/rust/operonx/tests/common/mod.rs) reads the same JSON files, runs `Operon::builder(json).auto_register().build()` + `run_json_async_with_scratch()`, strips timing keys, asserts == expected. Wired via `rust/operonx/tests/spec_<area>.rs` entry binaries that explicitly call `run_fixture("<area>/<scenario>")` per case.

**Rule:** every new Rust feature ships with a fixture covering its user-visible behavior. The same fixture is consumed by both runtimes. CI parity workflow fails on divergence.

**Today:** 13 fixtures (4 ops + 3 scheduler + 5 state + 1 iteration).
**Target:** ~30 fixtures by end of Phase 1.

### 2.2 — Mirrored internal tests

**Python:** `/home/thanglq/Operon/tests/internal/` — 71 files, pytest-driven. Covers behavior the public surface tests can't reach (private helpers, error edges, multi-thread races).

**Rust:** `/home/thanglq/Operon/rust/operonx/tests/internal/` — currently 5 files (resource_hub_setup.rs, stream_policy.rs, macros.rs, interrupt.rs). Each Rust internal test covers the same code path as the matching Python internal test.

**Rule:** for every Rust feature added/changed, ship matching internal tests under `rust/operonx/tests/internal/<area>/`. Mirror the Python test path when one exists.

### 2.3 — CI gate

[.github/workflows/parity.yaml](../../../Operon/.github/workflows/parity.yaml) already runs both runtimes against `tests/spec/`. Keep it green. Any divergence = stop Phase 1, fix Rust.

---

## 3. Stages (ordered, dependency-driven)

Each stage = one PR/commit batch. Each ends with: (a) green `cargo test --workspace`, (b) green `uv run pytest tests/ -m "not integration"`, (c) green parity workflow.

### Stage 1 — Identity + baseline status doc (½ day)

- Set git identity to Bruce Win on operonx repo per [CLAUDE.md](../../../Operon/CLAUDE.md)
- Commit `docs/RUST_BASELINE_STATUS.md` — captures: workspace builds, 136 tests pass, 13 fixtures green, version delta documented

### Stage 2 — Exception hierarchy port (1 day)

**Why:** Python [operonx/core/exceptions.py](../../../Operon/operonx/core/exceptions.py) (387 lines) defines `OpError`, `ParserError`, `CodeError`, `BranchError`, `ConditionError`, `IterationError`, `PromptError`, `EmbeddingError`, `RerankError` with structured fields. Rust [exceptions.rs](../../../Operon/rust/operonx/src/core/exceptions.rs) (107 lines) collapses to stringly-typed `OperonError`. Lossy — callers can't pattern-match the specific failure mode.

**Tasks:**
- Mirror Python's full variant tree in `rust/operonx/src/core/exceptions.rs`
- Each variant carries the same structured fields (op_name, context, original_message)
- Update every Rust `return Err(...)` site to use the structured variant
- Add `Display` + `Error` impls

**Tests:**
- `rust/operonx/tests/internal/core/test_exceptions.rs` — pattern-match each variant
- Shared fixtures `tests/spec/core/errors/{missing_input, parser_invalid_json, branch_no_match, condition_unbound}` — both runtimes emit the same `error_kind` field shape

### Stage 3 — Ref evaluator gaps: getattr semantics + named unsupported errors (½ day)

**Why (revised after audit):** Rust already covers 27/30 Python ref transforms. Of the 3 "missing":

- `apply` — Python [`_serialize_transforms`](../../../Operon/operonx/core/states/ref.py#L611) **explicitly refuses to serialize** a Python-callable arg to JSON for the Rust backend (raises ValueError pointing users at `@op(rust='...')`). Never reaches Rust. **No Rust impl needed.**
- `call` — same problem: `ref(args)` requires a callable Value. JSON has no callable values. **No Rust impl needed.**
- `matmul`/`rmatmul` — `@` operator. Requires ndarray-shaped values. Not used by callbot. Defer to a hypothetical ML-graph Phase.

Real gap: `getattr` is aliased to `getitem` at [task_scheduler.rs:1430](../../../Operon/rust/operonx/src/core/ops/graph/task_scheduler.rs#L1430), which silently returns Null for non-Object values. Python's `getattr(value, name)` raises AttributeError for missing attrs / non-attr-able shapes. Architecture-parity requires the distinction.

Additionally, when an `apply`/`call`/`matmul`/`rmatmul` transform **does** somehow appear in a graph.json (e.g. via Rust-native graph construction), the current `Unknown(name)` catch-all produces a generic error. A named variant per transform makes the failure mode explicit and the error actionable.

**Tasks:**
- Split `TransformKind::GetItem` and `TransformKind::GetAttr` in `eval_op`. Route `"getitem"` → `GetItem`, `"getattr"` → `GetAttr` (currently both → GetItem at line 1430).
- `GetAttr` impl: on `Value::Object`, return `value[name]`; on any other shape, error with `"AttributeError: '<type>' object has no attribute '<name>'"` matching Python's AttributeError text.
- Add `TransformKind::{Apply, Call, MatMul, RMatMul}` variants that always error with a clear message naming the unsupported transform and pointing the user at `@op(rust='...')` (mirrors Python's serialization-time refusal at evaluation time).

**Tests:**
- `rust/operonx/tests/internal/core/test_refs.rs` — coverage:
  - `getitem` on Object/Array works (regression)
  - `getattr` on Object works
  - `getattr` on non-Object errors with AttributeError message
  - `apply` errors with named message
  - `call` errors with named message
  - `matmul`/`rmatmul` error with named message
- Shared fixtures `tests/spec/core/refs/{getattr_dict, getitem_array_index}` — both runtimes produce identical output for the supported cases

### Note on stage ordering — Stage 4+6 deferred after 5/7/8/9

Stage 4 (tracing pipeline) and Stage 6 (Langfuse exporter) are coupled (the
exporter feeds the pipeline) and require deleting Rust's old `collector.rs`,
`flush_worker.rs`, `labels.rs`, `local.rs`, `models.rs`, `tracers/{operon_eyes,
otel}.rs`, and rewiring engine.rs + telemetry/tracers/langfuse.rs. ~2 weeks
of focused work.

Stages 5, 7, 8, 9 are independent and callbot-critical. Done first to unblock
Phase 2. Then come back to Stage 4+6 for full architecture parity.

### Stage 4 — Tracing pipeline overhaul (5 days) — DEFERRED until after 5/7/8/9

**Why:** Python 0.8.0 ([commit bcac42f](../../../Operon/)) deleted `tracing/{collector, flush_worker, labels, local, _base, operon_eyes, otel}.py` and added `tracing/{events, emitter, pipeline, processors/{drop, group, redact, sample, truncate}, legacy, exporters/local_file}.py` (~2500 LOC). Rust got 0 mirror files.

**Tasks (in order):**
- Read [docs/TRACING_REDESIGN_PLAN.md](../../../Operon/docs/TRACING_REDESIGN_PLAN.md) (915 lines) to understand the new shape
- Port `events.py` → `rust/operonx/src/core/tracing/events.rs` (~110 LOC equivalent)
- Port `emitter.py` → `rust/operonx/src/core/tracing/emitter.rs` (~326 LOC equiv) — `_current_op_var` + thread-local span context
- Port `pipeline.py` → `rust/operonx/src/core/tracing/pipeline.rs` — `TracePipeline` orchestrator
- Port `processors/*.py` → `rust/operonx/src/core/tracing/processors/{drop, group, redact, sample, truncate}.rs`
- Port `legacy.py` → `rust/operonx/src/core/tracing/legacy.rs` — back-compat shim
- Port `exporters/local_file.py` → `rust/operonx/src/core/tracing/exporters/local_file.rs`
- Delete Rust-side `tracing/{collector, flush_worker, labels, local}.rs` (their Python counterparts are gone)
- Update `tracing/mod.rs` exports
- Wire `engine.rs` to new emitter+pipeline

**Tests:**
- Internal tests: mirror [tests/internal/core/tracing/](../../../Operon/tests/internal/core/tracing/) (test_emitter, test_pipeline, test_processors, test_events, test_engine_wiring, test_legacy_adapter, test_local_file_exporter, test_cancellation) — 8 files
- Shared fixtures `tests/spec/telemetry/tracing/{basic, with_processors, with_interrupt}` — both runtimes emit identical event sequences when fed identical graphs

### Stage 5 — Frame/Interrupt public API + sequential-edge cancel parity (2 days)

**Why:** Frame + cancel parity has four sub-gaps. The 0.8.0 commit added SCRATCH + Interrupt scheduler mirror in Rust but skipped the user-facing API: typed `Interrupt`, `handle.scratch`, `handle.interrupts`. The 0.8.1 fix never landed on Rust at all.

**Sub-gaps:**

| # | Python ([engine.py:191](../../../Operon/operonx/core/engine.py#L191), [ops/_events.py](../../../Operon/operonx/core/ops/_events.py)) | Rust today | Fix |
|---|---|---|---|
| 5a | `from operonx.core import Interrupt; return Interrupt(ctx_to_cancel=..., reason=...)` | users hand-build `{"__interrupt__": {...}}` JSON | add `pub struct Interrupt` in `rust/operonx/src/core/ops/_events.rs` (new) w/ `Into<Value>` + lib.rs re-export |
| 5b | `handle.scratch` returns per-call scratch dict ([engine.py:180](../../../Operon/operonx/core/engine.py#L180)) | no accessor — scratch is scheduler-internal `RuntimeState.scratch` | refactor scratch into shared `Arc<Mutex<HashMap<String, Value>>>` owned by `HandleInner`; scheduler holds a clone; expose `ExecutionHandle::scratch()` |
| 5c | `handle.interrupts` returns `list[Interrupt]` ([engine.py:191](../../../Operon/operonx/core/engine.py#L191)) | missing | add `ExecutionHandle::interrupts() -> Vec<Interrupt>` — walks `inner.buffered`, filters `op == "__interrupt__"`, deserializes payload |
| 5d | [0.8.1 fix](../../../Operon/): `_sweep_ctx` advances `seq_queues` on cancel | same architecture, same bug pattern likely | mirror Python regression tests; port fix if any fail |

**Tasks:**
- 5a — Create `rust/operonx/src/core/ops/_events.rs` mirroring [Python `_events.py`](../../../Operon/operonx/core/ops/_events.py). `pub struct Interrupt { op, ctx, ctx_to_cancel, reason }` + `From<Interrupt> for Value` producing canonical JSON. Update `parse_interrupt` in scheduler to use shared shape constants from this module.
- 5b — Refactor `RuntimeState.scratch: HashMap<String, Value>` to `Arc<Mutex<HashMap<String, Value>>>` shared with `HandleInner`. Initial seed via `engine.start(scratch=...)` writes through the shared Arc. Add `ExecutionHandle::scratch(&self) -> std::sync::Arc<parking_lot::Mutex<std::collections::HashMap<String, serde_json::Value>>>` accessor.
- 5c — Add `ExecutionHandle::interrupts() -> Vec<Interrupt>` — implementation: walk `inner.buffered.lock()`, for each `FrameEvent` with `frame.op == "__interrupt__"`, deserialize `frame.data["__interrupt__"]` into `Interrupt`. Skip malformed entries silently (mirrors Python's `isinstance` filter).
- 5d — Mirror Python `TestB1bSequentialAdvanceAfterCancel.{test_siblings_dispatch_after_active_item_cancel, test_descendant_queued_items_dropped_on_subtree_sweep}` from [tests/internal/core/ops/graph/test_interrupt.py](../../../Operon/tests/internal/core/ops/graph/test_interrupt.py). Run. If any fail: port fix from [commit 6c7f58b](../../../Operon/) — in `sweep_ctx`'s seq_origins cleanup loop, treat cancelled `(op, ctx)` as synthetic EOF (advance `seq_queues` + reset `seq_active` per Python's lines ~363-373).

**Tests:**
- Internal: `rust/operonx/tests/internal/core/test_interrupt_api.rs` — covers 5a-5c (typed `Interrupt::default()` builder, `handle.scratch()` round-trip, `handle.interrupts()` list)
- Internal: `rust/operonx/tests/internal/core/test_interrupt_seq_cancel.rs` — covers 5d (regression tests)
- Shared fixtures `tests/spec/core/interrupt/{return_typed_interrupt, mid_stream_cancel, nested_ctx_sweep, sequential_edge_advance}` — both runtimes produce identical frame sequences (synthetic `__interrupt__` frame at the same arrival index)

### Stage 6 — Langfuse exporter port (5 days)

**Why:** Python 0.8.0 added [operonx/telemetry/exporters/langfuse.py](../../../Operon/operonx/telemetry/exporters/langfuse.py) (805 LOC). The callbot uses `LangfuseGroupedTimelineExporter`. Rust has only a basic `client.rs` POST shim.

**Tasks:**
- Read Python source — understand grouping algorithm + timeline batching + flush behavior + Media handling
- Port to `rust/operonx/src/telemetry/exporters/langfuse.rs`
- Match Python's emitted JSON span shape byte-for-byte
- Bounded mpsc + drop-oldest-on-full
- Async backpressure parity

**Tests:**
- Internal: mirror `tests/internal/telemetry/test_langfuse_grouped_timeline_exporter.py` + `test_langfuse_tree_exporter.py`
- Shared fixtures `tests/spec/telemetry/langfuse_{emit_basic, grouped_timeline, with_media}` — both runtimes POST identical JSON to a `wiremock` Langfuse stub

### Stage 7 — ParserOp port (3 days)

**Why:** [parser_op.rs:57](../../../Operon/rust/operonx/src/core/ops/transform/parser_op.rs#L57) is a `not yet implemented` stub. Scheduler dispatch ([task_scheduler.rs:1945](../../../Operon/rust/operonx/src/core/ops/graph/task_scheduler.rs#L1945)) doesn't route `OpType::Parser` anywhere — callbot needs this for LLM-output XML/JSON parsing.

**Tasks:**
- Add `quick-xml = "0.36"` + `serde_yaml = "0.9"` to `rust/operonx/Cargo.toml`
- Implement `ParserOp::exec_core`:
  - Strip code fences (` ```xml ... ``` `, ` ```json ... ``` `)
  - Parse per `parse_as` enum (Json / Xml / Yaml)
  - Apply `extract_fields` (dot-path with array indexing — port Python algorithm exactly)
  - Type coercion per type_hint
- Add `OpType::Parser` to scheduler dispatch — likely best path: extend `is_provider_kind` to include `Parser` and add `OpType::Parser => parser::execute(...)` arm in [providers/ops/factory.rs](../../../Operon/rust/operonx/src/providers/ops/factory.rs), OR route via `BaseOp::exec_core` if cleaner. Choose path that matches scheduler's existing pattern.

**Tests:**
- Internal: `rust/operonx/tests/internal/core/test_parser_op.rs` — one test per parser format + per extract pattern + malformed-input error parity
- Shared fixtures `tests/spec/core/ops/{parser_xml_extract, parser_json_nested, parser_yaml_basic, parser_codefence_strip}`

### Stage 8 — TritonOp gRPC client (5 days)

**Why:** [providers/ops/triton.rs:23](../../../Operon/rust/operonx/src/providers/ops/triton.rs#L23) is a stub. Callbot STT goes through this.

**Tasks:**
- Add `tonic = "0.12"` + `prost = "0.13"` + `prost-build` to `rust/operonx/Cargo.toml`
- Vendor or generate Triton protos (`grpc_service.proto`)
- Implement `TritonOp::execute`:
  - Connect via `tonic::transport::Channel`, shared/pooled per `TRITON_URL`
  - Build `ModelInferRequest`, await response, unpack output tensors
  - Handle batch inputs (callbot may batch chunks)
- Optional warmup (`warmup()` hook)

**Tests:**
- Internal: `rust/operonx/tests/internal/providers/test_triton_op.rs` — use a wiremock-style gRPC stub or a real local Triton container in CI
- Shared fixtures `tests/spec/providers/triton_stt` — both runtimes hit a mock Triton, get identical transcript

### Stage 9 — OpenAI streaming + ExecutionHandle wiring (4 days)

**Why:** [openai.rs:82](../../../Operon/rust/operonx/src/providers/llms/openai.rs#L82) stub. [llm.rs:32](../../../Operon/rust/operonx/src/providers/ops/llm.rs#L32) — LLMOp streaming not wired through to ExecutionHandle. Callbot's `ask()` shorthand uses streaming for TTFM.

**Tasks:**
- Implement `OpenAILlm::stream` — `reqwest::Response::bytes_stream` → SSE parser → emit `LLMChunk`s. Reuse the existing partial SSE parser in [llms/response.rs](../../../Operon/rust/operonx/src/providers/llms/response.rs)
- Wire `LLMOp` streaming through `FrameSender::send` — each chunk arrives at the handle as a separate frame
- Match Python's chunk schema exactly (`{"content": "...", "is_final": false, ...}`)

**Tests:**
- Internal: `rust/operonx/tests/internal/providers/test_openai_stream.rs` — mock OpenAI SSE response
- Shared fixtures `tests/spec/providers/{openai_chat_nonstream, openai_chat_stream, ask_shorthand}` — both runtimes emit identical chunk sequence (timing stripped)

### Stage 10 — Test backfill (1 week)

**Why:** even after Stages 1-9, several Python features the callbot uses have no parity fixture: media handling, branch chains, soft edges, streaming policies. Plug holes.

**Fixtures to add** (numbers approximate after de-dup with prior stages):
- `tests/spec/core/streaming/{generator_fan_out, parallel_policy, collect_policy, eof_propagation}` (4)
- `tests/spec/core/iteration/{loop_with_collect, while_until_var}` (2)
- `tests/spec/core/ops/{branch_else_chain, soft_edge_fanin}` (2)
- `tests/spec/core/state/{scratch_mid_run_write, scratch_in_loop, state_observer}` (3)
- `tests/spec/core/interrupt/{mid_stream_cancel, nested_ctx_sweep}` (2) — Stage 5 also adds these
- `tests/spec/core/media/audio_wav_passthrough` (1)

**Internal tests to add/mirror:**
- `test_streaming.rs`, `test_streaming_collect.rs`, `test_streaming_flatmap.rs`, `test_streaming_ntom.rs`, `test_streaming_regression.rs`, `test_engine_stream.rs`, `test_concurrent.rs`, `test_middleware.rs`, `test_cache.rs`, `test_serialize.rs`

### Stage 11 — Rust crate version bump (½ day)

- Bump [rust/Cargo.toml:8](../../../Operon/rust/Cargo.toml#L8) → `version = "0.8.2"` to match Python
- Update [rust/operonx/Cargo.toml](../../../Operon/rust/operonx/Cargo.toml) if needed (uses workspace version)
- Tag the merged sync commit as `rs-sync-0.8.2`
- Update [examples/rust/](../../../Operon/examples/rust/) Cargo.toml deps to point to local `[patch.crates-io]` (already configured per CHANGELOG)
- Regression-test all `examples/rust/exNN_*` projects compile + run

### Stage 12 (optional, parallel with Phase 2) — Non-callbot stubs (2 weeks)

- Anthropic, Azure OpenAI, Gemini LLM backends
- OpenAI Batch API coordinator
- vLLM/TEI/ONNX embeddings + rerankers
- Pinecone reranker
- Shared ONNX backend (`ort` crate)
- Keycloak token refresh
- Langfuse prompt manager

Each gets a parity fixture under `tests/spec/providers/<name>/` and a micro-benchmark.

---

## 4. Estimate

| Stage | Days |
|---|---|
| 1 — Identity + baseline doc | 0.5 |
| 2 — Exception hierarchy | 1 |
| 3 — Ref evaluator small gaps | 1 |
| 4 — Tracing pipeline overhaul | 5 |
| 5 — Sequential-edge cancel parity | 1 |
| 6 — Langfuse exporter port | 5 |
| 7 — ParserOp | 3 |
| 8 — TritonOp gRPC | 5 |
| 9 — OpenAI streaming + ExecutionHandle | 4 |
| 10 — Test backfill | 5 |
| 11 — Version bump | 0.5 |
| **Sub-total — callbot-critical** | **~31 days = 5 weeks** |
| 12 — Non-callbot stubs (parallel w/ Phase 2) | 10 |

---

## 5. Rules (apply to every stage)

1. **Don't modify Python.** Python is canonical. If a fixture exposes a Python bug, file it; don't fix in Rust.
2. **Evidence, not assumption.** Every claim of "this works" → passing fixture. Every claim of "faster" → committed benchmark output.
3. **No bypass.** No `#[ignore]` to make CI green. If a parity test fails, root-cause-fix it.
4. **One stage per PR-equivalent commit batch.** Don't mix stages; each merges independently with its tests green.
5. **Commit author:** Bruce Win <batman1m2001@gmail.com> on operonx repo per [/home/thanglq/Operon/CLAUDE.md](../../../Operon/CLAUDE.md).
6. **No co-author trailer.** User memory says omit `Co-Authored-By: Claude`.
7. **Update this plan as walls appear.** Architectural decisions that turn out wrong → revise the doc + commit the revision in the same batch as the code that revealed them.
