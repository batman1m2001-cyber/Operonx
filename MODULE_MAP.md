# Module Map — Python ↔ Rust

Rosetta Stone for navigating between Python and Rust implementations.

## Package Mapping

| Python | Rust | Purpose |
|--------|------|---------|
| `python/hush-core/` | `rust/rush-core/` | Core engine, ops, state, scheduling |
| `python/hush-providers/` | `rust/rush-providers/` | LLM, embedding, reranking providers |
| `python/hush-telemetry/` | `rust/rush-telemetry/` | Tracing backends (Langfuse, OTEL) |
| `python/hush-serve/` | `rust/rush-serve/` | HTTP server (REST, WebSocket, SSE) |
| — | `rust/ui-hush-eyes/` | Trace visualization (Rust-only) |

## Core Engine

| Concept | Python | Rust |
|---------|--------|------|
| Engine entry point | `hush/core/engine.py` | `rush-core/src/engine.rs` |
| Graph config | `hush/core/ops/graph/config.py` | `rush-core/src/config.rs` |
| Scheduler | `hush/core/ops/graph/scheduler.py` | `rush-core/src/ops/graph/graph_op.rs` |
| Base op execution | `hush/core/ops/base.py` | `rush-core/src/ops/base.rs` |
| State store | `hush/core/states/state.py` | `rush-core/src/states/state.rs` |
| State schema | `hush/core/states/schema.py` | `rush-core/src/config.rs` (OpConfig) |
| Ref resolution | `hush/core/refs/` | `rush-core/src/refs/ref_transforms.rs` |
| Loop evaluation | `hush/core/ops/graph/scheduler.py` | `rush-core/src/ops/graph/loop_eval.rs` |
| Built-in ops | `@op` decorated functions | `rush-core/src/builtin_ops/ops.rs` |
| Op dispatch | Dynamic (Python callables) | `rush-core/src/builtin_ops/mod.rs` (match) |

## Providers

| Concept | Python | Rust |
|---------|--------|------|
| LLM base | `hush/providers/llm/base.py` | `rush-providers/src/llms/` |
| Embedding base | `hush/providers/embedding/base.py` | `rush-providers/src/embeddings/` |
| Reranker base | `hush/providers/reranker/base.py` | `rush-providers/src/rerankers/` |
| Provider factory | `hush/providers/*/factory.py` | `rush-providers/src/lib.rs` (dispatch) |

## Telemetry

| Concept | Python | Rust |
|---------|--------|------|
| Tracer interface | `hush/core/tracing/base.py` | `rush-core/src/tracing/tracer.rs` |
| Trace collector | `hush/core/tracing/collector.py` | `rush-core/src/tracing/collector.rs` |
| Flush worker | `hush/core/tracing/flush_worker.py` | `rush-core/src/tracing/flush_worker.rs` |
| HushEyes tracer | `hush/telemetry/tracers/hush_eyes.py` | `rush-telemetry/src/hush_eyes.rs` |
| Langfuse tracer | `hush/telemetry/tracers/langfuse.py` | `rush-telemetry/src/langfuse/` |
| OTEL tracer | `hush/telemetry/tracers/otel.py` | `rush-telemetry/src/otel/` |

## Serve

| Concept | Python | Rust |
|---------|--------|------|
| App setup | `hush/serve/app.py` | `rush-serve/src/main.rs` |
| Config | `hush/serve/config.py` | `rush-serve/src/config.rs` |
| REST handler | `hush/serve/routes/sync_handler.py` | `rush-serve/src/routes/sync_handler.rs` |
| WebSocket handler | `hush/serve/routes/ws_handler.py` | `rush-serve/src/routes/ws_handler.rs` |
| SSE stream handler | `hush/serve/routes/stream_handler.py` | `rush-serve/src/routes/stream_handler.rs` |

## Key Differences

| Aspect | Python | Rust |
|--------|--------|------|
| Concurrency model | asyncio (single-thread event loop) | tokio (multi-thread async runtime) |
| State store | `MemoryState` (dict-based) | `DashMap` (lock-free concurrent) |
| Op dispatch | Dynamic callables | Static match on `rust_name` string |
| Parallelism | asyncio.gather | rayon / tokio::spawn |
| Streaming | async generators (yield) | mpsc::Sender channels |
| String interning | — | lasso::ThreadedRodeo |
