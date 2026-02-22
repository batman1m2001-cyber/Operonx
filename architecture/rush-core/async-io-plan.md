# Async I/O + Native HTTP for Rust Mode

> **Status: IN PROGRESS** — Phases 1-2 complete, Phases 3-5 pending.

## Problem

Rust mode is optimized only for CPU-bound workloads (rayon parallel). For I/O-bound workloads (LLM calls, embeddings, reranking) — Hush's primary use case — it's actually **worse** than Python mode:

- 3 parallel LLM calls in Python mode: ~3x speedup (asyncio concurrent)
- 3 parallel LLM calls in Rust mode: 1x (sequential, heuristic blocks parallelism)
- Async Python ops (`is_async: true`) return coroutines that are never awaited in Rust
- MapOp and AIterOp have no Rust implementation — fall through to wrong handler

**Goal:** Rust mode should be strictly superior for ALL workloads — async I/O concurrency + multi-core CPU parallelism + native HTTP bypassing Python entirely.

## Architecture Overview

```
rush-core/                     rush-providers/
├── engine.rs                  ├── http/
├── ops/                       │   ├── mod.rs        (shared reqwest Client)
│   ├── graph/graph_op.rs      │   ├── llm.rs        (chat/completions)
│   ├── iteration/             │   ├── embedding.rs   (embeddings)
│   │   ├── for_op.rs          │   └── reranker.rs    (reranking)
│   │   ├── while_op.rs        └── lib.rs
│   │   ├── map_op.rs  ← NEW
│   │   └── aiter_op.rs ← NEW
│   └── base.rs
├── runtime.rs ← NEW (tokio)
└── config.rs

Mirrors Python structure:
  hush-core     ←→  rush-core       (engine, scheduler, state, base ops)
  hush-providers ←→  rush-providers  (LLM, embedding, rerank HTTP clients)
```

## Phase Summary

```
Phase 1: Smart heuristic + async driving       ← DONE
    │
Phase 2: Tokio runtime + MapOp + AIterOp       ← DONE
    │    (tokio dep, rayon concurrent iteration)
    │
Phase 3: Resource config serialization          ← NEXT
    │    (bridge provider configs to Rust)
    │
Phase 4: rush-providers — native HTTP clients   ← requires Phase 3
    │    (separate crate, reqwest, bypass Python)
    │
Phase 5: Scheduler integration                  ← requires Phase 4
         (tokio::spawn for native HTTP ops)
```

## Expected Performance After All Phases

| Workload | Python Mode | Rust (before) | Rust (after) |
|----------|-------------|---------------|--------------|
| Sequential CPU ops | baseline | 2.3-2.7x | 2.3-2.7x |
| 3 parallel LLM calls | ~3x (asyncio) | 1x (sequential!) | ~3x (native HTTP, no GIL) |
| 10 parallel embeddings | ~10x (asyncio) | 1x (sequential!) | ~10x (native HTTP) |
| MapOp (10 items, I/O) | ~10x (asyncio.gather) | falls through! | ~10x (tokio spawn) |
| Mixed CPU + I/O | partial | CPU only | fully concurrent |

---

## Phase 1: Smart Parallel Heuristic + Async Coroutine Driving

> **Status: COMPLETED** — 131 tests pass (125 existing + 6 new async tests).

### Problem

1. Parallel heuristic (`graph_op.rs`) required `rust_op.is_some()` — blocked I/O-bound async Python ops from parallelizing
2. `call_python()` (`base.rs`) didn't await async coroutines — returned a useless coroutine object

### Changes

**`rush-core/src/ops/graph/graph_op.rs`** — expanded parallel heuristic:

```rust
// BEFORE: only Rust-native ops triggered parallelism
let use_parallel = batch.len() > 1 && batch.iter().any(|name| {
    config.ops.get(name).map_or(false, |op| op.rust_op.is_some())
});

// AFTER: async I/O ops also trigger parallelism (they release GIL during network waits)
let use_parallel = batch.len() > 1 && batch.iter().any(|name| {
    config.ops.get(name).map_or(false, |op| op.rust_op.is_some() || op.is_async)
});
```

**`rush-core/src/ops/base.rs`** — added `drive_coroutine()` helper:

```rust
pub(crate) fn call_python(py, op, inputs_dict) -> PyResult<Option<PyObject>> {
    match &op.python_callable {
        Some(callable) => {
            let result = callable.bind(py).call((), Some(inputs_dict))?;
            if op.is_async {
                let driven = drive_coroutine(py, &result)?;
                Ok(Some(driven.unbind()))
            } else {
                Ok(Some(result.unbind()))
            }
        }
        None => Err(...)
    }
}

/// Drive an async coroutine to completion.
/// - No running event loop → asyncio.run(coro) directly
/// - Inside running loop (e.g. Hush.run()) → offload to ThreadPoolExecutor worker
fn drive_coroutine(py, coro) -> PyResult<Bound<PyAny>> {
    let asyncio = py.import_bound("asyncio")?;
    let in_running_loop = asyncio.call_method0("get_running_loop").is_ok();

    if in_running_loop {
        let cf = py.import_bound("concurrent.futures")?;
        let executor = cf.getattr("ThreadPoolExecutor")?.call1((1i32,))?;
        let asyncio_run = asyncio.getattr("run")?;
        let future = executor.call_method1("submit", (&asyncio_run, coro))?;
        let output = future.call_method0("result")?;
        let _ = executor.call_method0("shutdown");
        Ok(output)
    } else {
        asyncio.call_method1("run", (coro,))
    }
}
```

### Tests Added

`rush-core/tests/test_engine.py` — `TestAsyncOps` class (6 tests):

| Test | What it verifies |
|------|-----------------|
| `test_single_async_op` | Single async op works in Rust mode via `Rush.run()` |
| `test_async_op_chain` | Chained async ops — each coroutine driven to completion |
| `test_parallel_async_ops` | Fork-join with async ops triggers parallel batch execution |
| `test_mixed_async_and_sync` | Async + sync ops together in one graph |
| `test_async_op_both_modes[python]` | Same result via `Hush(mode="python")` |
| `test_async_op_both_modes[rust]` | Same result via `Hush(mode="rust")` (tests running-loop fallback) |

### Limitations (addressed in later phases)

- Each parallel async op creates a new event loop via `asyncio.run()` — **loses connection pool reuse** (fixed in Phase 2 with tokio)
- Async Python ops still hold the GIL during non-I/O parts — **not zero-cost** (fixed in Phase 4 with native HTTP)

---

## Phase 2: Tokio Runtime + MapOp + AIterOp

> **Status: COMPLETED** — 142 tests pass (131 existing + 11 new MapOp tests).

### 2a: Tokio Runtime (Foundation)

Added tokio as a dependency and created a global runtime singleton for future async operations (Phases 4-5). The main scheduler still uses rayon for parallel batches (working, tested) — tokio will be used in Phase 5 for native HTTP ops.

**New dependency** (`rush-core/Cargo.toml`):
```toml
tokio = { version = "1", features = ["rt-multi-thread", "sync", "time"] }
```

**New file** (`rush-core/src/runtime.rs`):
```rust
static TOKIO_RT: OnceLock<Runtime> = OnceLock::new();

pub(crate) fn get_runtime() -> &'static Runtime {
    TOKIO_RT.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("Failed to create Tokio runtime")
    })
}
```

### 2b: MapOp — Concurrent Iteration with Rayon

**Problem:** `op_type == "map"` fell through to `base::execute_leaf_op` — wrong behavior.

**Implementation** (`rush-core/src/ops/iteration/map_op.rs`):
Uses rayon's `par_iter` for concurrent inner graph execution, with `max_concurrency` control via chunked processing:

```rust
pub(crate) fn execute_map_op(py, op, state, context) -> PyResult<()> {
    // 1. Resolve each/broadcast inputs
    // 2. Determine iteration count, validate equal lengths
    // 3. Pre-extract per-iteration items (need GIL for Python list indexing)
    // 4. Process in chunks of max_concurrency using rayon:
    //    - py.allow_threads → indices.par_iter → Python::with_gil
    //    - Each iteration: store inputs → run_graph → collect_outputs
    //    - AtomicBool for fail_fast early termination
    //    - Mutex<Option<Result>> per iteration for ordered result collection
    // 5. Transpose results: [{a:1,b:2}, {a:3,b:4}] → {a:[1,3], b:[2,4]}
    // 6. Store iteration_metrics
    // 7. Push output refs
}
```

Key design decisions:
- **Rayon over tokio**: Avoids Arc-wrapping GraphConfig/EngineState. Rayon's `par_iter` within `py.allow_threads` allows shared references since rayon guarantees completion before return.
- **Chunked processing**: Iterations batched in groups of `max_concurrency`, processed in parallel. Provides concurrency control without custom thread pool creation per MapOp.
- **fail_fast**: Uses `AtomicBool` — set on first error, checked before processing each iteration.

### 2c: AIterOp — Async Streaming Iteration

**Problem:** `op_type == "stream"` fell through to `base::execute_leaf_op` — wrong behavior.

**Implementation** (`rush-core/src/ops/iteration/aiter_op.rs`):
Collects all items from async iterable first (using `drive_coroutine`), then processes them concurrently with rayon:

```rust
pub(crate) fn execute_aiter_op(py, op, state, context) -> PyResult<()> {
    // 1. Resolve the async iterable source (exactly one each param)
    // 2. Collect all items from async iterable via drive_coroutine
    //    (defines async Python helper: [item async for item in source])
    // 3. Apply batching if batch_fn provided
    // 4. Process chunks concurrently with rayon (same pattern as MapOp)
    // 5. Call callback in order after processing (if provided)
    // 6. Transpose results, store iteration_metrics
    // 7. Push output refs
}
```

Note: This implementation loses true streaming (all items collected before processing) but is correct. The concurrent processing via rayon still provides speedup for CPU/I/O-bound inner graphs.

### Config & Dispatch Updates

**`config.rs`** — Extended `IterationConfig`:
```rust
pub struct IterationConfig {
    pub each: Vec<IterParamConfig>,
    pub broadcast: Vec<IterParamConfig>,
    pub fail_fast: bool,
    pub until: Option<String>,
    pub max_iterations: Option<usize>,
    pub max_concurrency: Option<usize>,  // MapOp, AIterOp
    pub callback: Option<PyObject>,      // AIterOp
    pub batch_fn: Option<PyObject>,      // AIterOp
}
```

Extended `OpConfig::from_dict` to parse `inner_graph` and `iteration_config` for `"map"` and `"stream"` types.

**`graph_op.rs`** — Updated dispatch (both sequential and parallel paths):
```rust
match op.op_type.as_str() {
    "graph"  => execute_nested_graph(py, op, state, context)?,
    "for"    => for_op::execute_for_op(py, op, state, context)?,
    "while"  => while_op::execute_while_op(py, op, state, context)?,
    "map"    => map_op::execute_map_op(py, op, state, context)?,
    "stream" => aiter_op::execute_aiter_op(py, op, state, context)?,
    _        => base::execute_leaf_op(py, op, state, context)?,
}
```

**`base.rs`** — Made `drive_coroutine` `pub(crate)` for AIterOp's use.

### Tests Added

`rush-core/tests/test_engine.py` — `TestMapOp` class (11 tests):

| Test | What it verifies |
|------|-----------------|
| `test_simple_map_literal_each` | Basic MapOp with literal Each values |
| `test_map_with_broadcast` | Each values + broadcast scalar |
| `test_map_multiple_each_zip` | Multiple Each values (zipped) |
| `test_map_empty_list` | Empty Each list → empty results |
| `test_map_with_upstream_ref` | Each from upstream op output |
| `test_map_with_max_concurrency` | max_concurrency parameter respected |
| `test_map_fail_fast_config_parsed` | fail_fast config parsed correctly |
| `test_map_no_fail_fast_continues` | Error handling continues on failure |
| `test_map_iteration_metrics` | Metrics stored in state |
| `test_map_both_modes[python]` | Same result via Hush(mode="python") |
| `test_map_both_modes[rust]` | Same result via Hush(mode="rust") |

### Phase 2 Files

| File | Change |
|------|--------|
| `rush-core/Cargo.toml` | Add tokio dependency |
| `rush-core/src/runtime.rs` | **NEW** — global tokio runtime singleton |
| `rush-core/src/lib.rs` | Add `mod runtime;` |
| `rush-core/src/config.rs` | Add `max_concurrency`, `callback`, `batch_fn` to `IterationConfig`; parse `"map"` / `"stream"` |
| `rush-core/src/ops/base.rs` | Make `drive_coroutine` `pub(crate)` |
| `rush-core/src/ops/graph/graph_op.rs` | Dispatch `"map"` / `"stream"` in both execution paths |
| `rush-core/src/ops/iteration/mod.rs` | Add `pub mod map_op; pub mod aiter_op;` |
| `rush-core/src/ops/iteration/map_op.rs` | **NEW** — concurrent iteration with rayon |
| `rush-core/src/ops/iteration/aiter_op.rs` | **NEW** — async streaming iteration |
| `rush-core/tests/test_engine.py` | Add 11 MapOp tests |

---

## Phase 3: Resource Config Serialization

> **Status: PENDING** — Can be done in parallel with Phase 2.

### Problem

Provider ops (LLM, embedding, rerank) currently only pass a Python callable to Rust. Rust has no access to `api_key`, `base_url`, `model` — it can't make HTTP calls natively.

### Python-Side Changes

**`hush-providers/hush/providers/ops/llm.py`** — override `serialize()`:
```python
def serialize(self):
    base = super().serialize()
    if self._llm and hasattr(self._llm, "config"):
        cfg = self._llm.config
        base["resource_config"] = {
            "provider_type": "llm",
            "api_type": cfg.api_type.value,
            "model": cfg.model,
            "api_key": cfg.api_key,
            "base_url": cfg.base_url,
            "api_version": getattr(cfg, "api_version", None),
            "azure_endpoint": getattr(cfg, "azure_endpoint", None),
        }
    return base
```

Same pattern for `EmbeddingOp` and `RerankOp`.

### Rust-Side Changes

**`rush-core/src/config.rs`** — new struct + `OpConfig` field:
```rust
pub struct ResourceConfig {
    pub provider_type: String,  // "llm" | "embedding" | "reranking"
    pub api_type: String,       // "openai" | "azure" | "vllm" | "tei" | "pinecone"
    pub model: Option<String>,
    pub api_key: Option<String>,
    pub base_url: Option<String>,
    pub api_version: Option<String>,
    pub azure_endpoint: Option<String>,
}
```

### Phase 3 Files

| File | Change |
|------|--------|
| `hush-providers/hush/providers/ops/llm.py` | Serialize resource config |
| `hush-providers/hush/providers/ops/embedding.py` | Serialize resource config |
| `hush-providers/hush/providers/ops/rerank.py` | Serialize resource config |
| `rush-core/src/config.rs` | Add `ResourceConfig` struct + parse |

---

## Phase 4: rush-providers — Rust-Native HTTP Clients

> **Status: PENDING** — Requires Phase 2 (tokio) + Phase 3 (resource config).

### Goal

Implement LLM/embedding/rerank calls natively in Rust — no Python, no GIL.
**Separate crate** (`rush-providers/`) mirroring `hush-providers/`.

### Why a Separate Crate

Mirrors the Python package structure:
- `rush-core` = engine, scheduler, state (like `hush-core`)
- `rush-providers` = HTTP clients for LLM/embedding/rerank (like `hush-providers`)

This keeps rush-core lean (no HTTP deps) and allows independent versioning.

### Crate Structure

```
rush-providers/
├── Cargo.toml
├── pyproject.toml              # maturin build config
├── python/
│   └── rush_providers/
│       └── __init__.py         # Python exports
├── src/
│   ├── lib.rs                  # PyO3 module
│   ├── http/
│   │   ├── mod.rs              # Shared reqwest Client singleton
│   │   ├── llm.rs              # OpenAI-compatible /chat/completions
│   │   ├── embedding.rs        # OpenAI + TEI embeddings
│   │   └── reranker.rs         # vLLM, TEI, Pinecone rerankers
│   └── registry.rs             # Register ops: rust_llm_openai, etc.
└── tests/
    ├── test_llm.py             # Unit tests (mock HTTP)
    ├── test_embedding.py
    └── test_reranker.py
```

### Dependencies

**`rush-providers/Cargo.toml`**:
```toml
[dependencies]
pyo3 = { version = "0.22", features = ["extension-module"] }
reqwest = { version = "0.12", default-features = false, features = ["json", "rustls-tls"] }
tokio = { version = "1", features = ["rt-multi-thread"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

Using `rustls-tls` (not OpenSSL) to avoid linker issues in maturin builds.

### Supported Endpoints

| Provider Type | API Type | Endpoint | Auth |
|--------------|----------|----------|------|
| LLM | openai/vllm | `POST {base_url}/chat/completions` | `Bearer {api_key}` |
| LLM | azure | `POST {azure_endpoint}/openai/deployments/{model}/chat/completions` | `api-key: {api_key}` |
| Embedding | openai | `POST {base_url}` with `{model, input}` | `Bearer {api_key}` |
| Embedding | tei | `POST {base_url}` with `{inputs}` | None |
| Reranker | vllm | `POST {base_url}` with `{model, query, documents}` | `Bearer {api_key}` |
| Reranker | tei | `POST {base_url}` with `{query, texts}` | None |
| Reranker | pinecone | `POST {base_url}` with `{model, query, documents}` | `Api-Key: {api_key}` |

### Registration

Ops registered as: `"rust_llm_openai"`, `"rust_embedding_openai"`, `"rust_embedding_tei"`, `"rust_rerank_vllm"`, etc.

Provider ops auto-set `rust_op` in `serialize()` (Phase 3) based on `api_type`:
```python
if cfg.api_type.value in ("openai", "vllm"):
    base["rust_op"] = "rust_llm_openai"
```

If rush-providers has the native client → uses reqwest (no Python). If not (e.g. Gemini) → falls back to Python callable.

---

## Phase 5: Scheduler Integration

> **Status: PENDING** — Requires Phase 2 (tokio) + Phase 4 (native HTTP).

### Goal

Connect rush-providers ops to the tokio scheduler so native HTTP ops run as true async tasks (no GIL, no `spawn_blocking`).

### Scheduler Detection

`graph_op.rs` detects native HTTP ops and routes them differently:

```rust
if op.rust_op.is_some() && op.resource_config.is_some() {
    // True async — reqwest HTTP call, no GIL needed
    tokio::spawn(async move {
        rush_providers::execute_http_op(op, state, context).await
    })
} else {
    // Python op or pure-Rust CPU op — needs GIL
    tokio::task::spawn_blocking(move || {
        Python::with_gil(|py| execute_leaf_op(py, op, state, context))
    })
}
```

### Phase 5 Files

| File | Change |
|------|--------|
| `rush-core/Cargo.toml` | Add `rush-providers` as optional dependency |
| `rush-core/src/ops/graph/graph_op.rs` | Route native HTTP ops to `tokio::spawn` |
| `rush-core/src/ops/base.rs` | Inject resource_config into native ops |

---

## Missing Ops Status

| Op Type | Python Class | Rust File | Status |
|---------|-------------|-----------|--------|
| `"func"` | FuncOp | `base.rs` (generic) | Done |
| `"graph"` | GraphOp | `graph/graph_op.rs` | Done |
| `"branch"` | BranchOp | `graph_op.rs` (activate_successors) | Done |
| `"for"` | ForOp | `iteration/for_op.rs` | Done |
| `"while"` | WhileOp | `iteration/while_op.rs` | Done |
| `"map"` | MapOp | `iteration/map_op.rs` | Done (Phase 2) |
| `"stream"` | AIterOp | `iteration/aiter_op.rs` | Done (Phase 2) |
| LLM/Embed/Rerank | Provider ops | `rush-providers/` | **Phase 4** |

---

## Files Summary (all phases)

| Phase | Status | Crate | Files Modified | Files Added |
|-------|--------|-------|---------------|-------------|
| 1 | DONE | rush-core | `graph_op.rs`, `base.rs`, `test_engine.py` | — |
| 2 | DONE | rush-core | `Cargo.toml`, `lib.rs`, `config.rs`, `base.rs`, `graph_op.rs`, `iteration/mod.rs`, `test_engine.py` | `runtime.rs`, `map_op.rs`, `aiter_op.rs` |
| 3 | PENDING | rush-core + hush-providers | `config.rs`, `llm.py`, `embedding.py`, `rerank.py` | — |
| 4 | PENDING | **rush-providers (NEW)** | — | entire crate (~8 files) |
| 5 | PENDING | rush-core | `Cargo.toml`, `graph_op.rs`, `base.rs` | — |
