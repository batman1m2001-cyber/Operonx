# Module Map — Python ↔ Rust

Rosetta Stone for navigating between Python and Rust implementations.

## Package Mapping

| Python | Rust | Purpose |
|--------|------|---------|
| `python/hush-icore/` | `rust/hush-icore/` | Core engine, ops, state, scheduling |
| `python/hush-providers/` | `rust/hush-providers/` | LLM, embedding, reranking providers |
| `python/hush-telemetry/` | `rust/hush-telemetry/` | Tracing backends (Langfuse, OTEL) |
| `python/hush-serve/` | `rust/hush-serve/` | HTTP server (REST, WebSocket, SSE) |
| — | `rust/ui-hush-eyes/` | Trace visualization (Rust-only) |

## Core Engine

| Concept | Python | Rust |
|---------|--------|------|
| Engine entry point | `hush/core/engine.py` | `hush-icore/src/engine.rs` |
| Graph config | `hush/core/ops/graph/config.py` | `hush-icore/src/config.rs` |
| Scheduler | `hush/core/ops/graph/scheduler.py` | `hush-icore/src/ops/graph/graph_op.rs` |
| Base op execution | `hush/core/ops/base.py` | `hush-icore/src/ops/base.rs` |
| State store | `hush/core/states/state.py` | `hush-icore/src/states/state.rs` |
| State schema | `hush/core/states/schema.py` | `hush-icore/src/config.rs` (OpConfig) |
| Ref resolution | `hush/core/refs/` | `hush-icore/src/refs/ref_transforms.rs` |
| Loop evaluation | `hush/core/ops/graph/scheduler.py` | `hush-icore/src/ops/graph/loop_eval.rs` |
| Plugin ops | `@op(rust="...")` decorated functions | cdylib crate via `hush-plugin` |
| Op dispatch | Dynamic (Python callables) | `hush-icore/src/registry.rs` (OpRegistry trait) |

## Providers

| Concept | Python | Rust |
|---------|--------|------|
| LLM base | `hush/providers/llm/base.py` | `hush-providers/src/llms/` |
| Embedding base | `hush/providers/embedding/base.py` | `hush-providers/src/embeddings/` |
| Reranker base | `hush/providers/reranker/base.py` | `hush-providers/src/rerankers/` |
| Provider factory | `hush/providers/*/factory.py` | `hush-providers/src/lib.rs` (dispatch) |

## Telemetry

| Concept | Python | Rust |
|---------|--------|------|
| Tracer interface | `hush/core/tracing/base.py` | `hush-icore/src/tracing/tracer.rs` |
| Trace collector | `hush/core/tracing/collector.py` | `hush-icore/src/tracing/collector.rs` |
| Flush worker | `hush/core/tracing/flush_worker.py` | `hush-icore/src/tracing/flush_worker.rs` |
| HushEyes tracer | `hush/telemetry/tracers/hush_eyes.py` | `hush-telemetry/src/hush_eyes.rs` |
| Langfuse tracer | `hush/telemetry/tracers/langfuse.py` | `hush-telemetry/src/langfuse/` |
| OTEL tracer | `hush/telemetry/tracers/otel.py` | `hush-telemetry/src/otel/` |

## Serve

| Concept | Python | Rust |
|---------|--------|------|
| App setup | `hush/serve/app.py` | `hush-serve/src/main.rs` |
| Config | `hush/serve/config.py` | `hush-serve/src/config.rs` |
| REST handler | `hush/serve/routes/sync_handler.py` | `hush-serve/src/routes/sync_handler.rs` |
| WebSocket handler | `hush/serve/routes/ws_handler.py` | `hush-serve/src/routes/ws_handler.rs` |
| SSE stream handler | `hush/serve/routes/stream_handler.py` | `hush-serve/src/routes/stream_handler.rs` |

## Key Differences

| Aspect | Python | Rust |
|--------|--------|------|
| Concurrency model | asyncio (single-thread event loop) | tokio (multi-thread async runtime) |
| State store | `MemoryState` (dict-based) | `DashMap` (lock-free concurrent) |
| Op dispatch | Dynamic callables | Static match on `rust_name` string |
| Parallelism | asyncio.gather | rayon / tokio::spawn |
| Streaming | async generators (yield) | mpsc::Sender channels |
| String interning | — | lasso::ThreadedRodeo |
