# Rust ↔ Python Baseline Status — Pre Phase-1 Sync

Captured 2026-05-31 before the Rust↔Python sync work begins. This file is the
ground truth the rest of the sync plan ([docs/RUST_PARITY_SYNC_PLAN.md](RUST_PARITY_SYNC_PLAN.md))
builds on.

## Versions

| Side | Version | Source |
|------|---------|--------|
| Python | 0.8.2 | [pyproject.toml:7](../pyproject.toml#L7) |
| Rust crate | 0.7.1 | [rust/Cargo.toml:8](../rust/Cargo.toml#L8) |

`git log --oneline 23667fe..HEAD -- rust/` returns 0 commits. The Rust crate
has not received any change since the 0.8.0 commit (`bcac42f`) — which itself
landed only the SCRATCH primitive + Interrupt event mirrors. Tracing pipeline
rewrite, sequential-edge cancel fix, and 0.8.2 logging perf are Python-only.

### Per-commit audit since Rust 0.7.1 (`c5a839d..HEAD`)

Excluding style/cargo-fmt/docs commits:

| Commit | What | Rust got | Rust gap |
|---|---|---|---|
| `bcac42f` (0.8.0) | tracing pipeline + SCRATCH + Interrupt | SCRATCH (`scratch_ref.rs`, scheduler integration), Interrupt scheduler+test | Tracing pipeline (events/emitter/pipeline/processors/legacy/exporters/local_file) + Langfuse exporter 805 LOC + `current_op_var`/`current_emitter` + typed `Interrupt` export + `handle.scratch` + `handle.interrupts` + 0.8.0 BaseOp emitter integration |
| `6c7f58b` (0.8.1) | `_sweep_ctx` advances seq_queues on cancel | nothing | Same scheduler architecture in Rust → same bug likely |
| `7252ce3` (0.8.2) | loggings perf short-circuit ndarray/tensor | nothing | Per-language (Python ndarray formatter); not a parity issue |
| `23667fe` | drop `[otel]` extra after tracing redesign | nothing | Rust still has `telemetry/backends/otel/` (empty). Drop to match intent. |

## Build

```
cargo build --workspace
→ ok, 9 warnings, 0 errors
```

The 9 warnings are pre-existing (`unused_must_use` on `sender.send().await`
in scheduler error paths). None are new from this sync work.

## Tests

```
cargo test --workspace --no-fail-fast
→ 136 tests run, 136 pass, 0 fail, 3 ignored (doctests)
```

Breakdown by test binary:

| Binary | Tests | Status |
|--------|-------|--------|
| `operonx` lib unit | 96 | all pass |
| `internal_core` | 16 | all pass |
| `internal_providers` | 4 | all pass |
| `internal_telemetry` | 2 | all pass |
| `spec_core` (13 fixtures) | 14 | all pass |
| `spec_providers` | 2 | all pass |
| `spec_telemetry` | 2 | all pass |

The 13 shared spec fixtures under `tests/spec/core/{ops,scheduler,state,iteration}/`
pass on both runtimes. SCRATCH (3 fixtures) and Interrupt scenarios are
covered. Providers and telemetry shared spec dirs are empty — these grow in
later stages.

## Stub inventory (entered Phase 1 with these)

Grep `not yet implemented` across `rust/operonx/src/`:

- `providers/llms/anthropic.rs:38` — `generate` stub
- `providers/llms/anthropic.rs:49` — `stream` stub
- `providers/llms/azure.rs:37` — `generate` stub
- `providers/llms/azure.rs:48` — `stream` stub
- `providers/llms/gemini.rs:37` — `generate` stub
- `providers/llms/gemini.rs:48` — `stream` stub
- `providers/llms/openai.rs:82` — `stream` stub
- `providers/llms/batch_coordinator.rs:68` — Batch API stub
- `providers/ops/llm.rs:32` — streaming not wired into ExecutionHandle
- `providers/ops/llm.rs:37` — batch_mode not implemented
- `providers/ops/triton.rs:23` — Triton gRPC stub
- `providers/embeddings/{vllm,tei,onnx}.rs` — stubs
- `providers/rerankers/{vllm,tei,onnx,pinecone}.rs` — stubs
- `providers/onnx/backend.rs:46` — ONNX backend stub
- `providers/auth/keycloak.rs:134` — token refresh stub
- `core/ops/transform/parser_op.rs:57` — Parser exec stub (also missing scheduler dispatch arm)
- `core/registry/resource_hub.rs:491` — Keycloak refresh stub
- `telemetry/backends/langfuse/prompt_manager.rs:52` — prompt fetch stub
- `telemetry/backends/otel/mod.rs` — entire backend empty (deferred to v0.7 per upstream plan)
- `telemetry/exporters/` — directory does not exist (Python 0.8.0 added `exporters/langfuse.py` 805 LOC; Rust never followed)
- `core/tracing/` — old layout (`collector.rs`, `flush_worker.rs`, `labels.rs`, `local.rs`); Python deleted these in 0.8.0 and replaced with `events.py`, `emitter.py`, `pipeline.py`, `processors/*.py`, `legacy.py`, `exporters/local_file.py`

## What's intentionally not in the sync scope

Per the plan §0, these architectural differences are intentional Rust
optimizations — they deliver the 3.2× linear / 11–12× production-shape /
17–38× pure-compute wins documented in CHANGELOG 0.7.0. Do not "fix" them:

- **Scheduler-inlined op execution** — `task_scheduler.rs::execute_op` dispatches
  by `OpType::*` instead of vtable-calling `BaseOp::run(ctx)`. Intentional.
- **`RuntimeState` separate from `MemoryState`** — pre-sized hot-path layer
  per CHANGELOG 0.7.1 ("eliminating the resize cycle"). Intentional.
- **Compiled Ref pipeline at scheduler construction** — `CompiledRef` /
  `CompiledOp` / `TransformKind` enum-dispatch instead of per-frame string
  matching. Intentional.

## Identity

Operon repo commits go under `Bruce Win <batman1m2001@gmail.com>` per
[/home/thanglq/Operon/CLAUDE.md](../CLAUDE.md). Verified set on the working
tree at the start of this sync work.
