# Rust-Python Split

## Builder-Executor Architecture

Hush uses a **builder-executor split**: Python builds graphs via DSL, serializes to JSON config, and Rust loads and executes.

```
Python (build time)              Rust (run time)
─────────────────                ──────────────
GraphOp DSL                      Hush(config_json)
  │                                │
  ▼                                ▼
graph.serialize() ──JSON──→   GraphConfig::from_json()
                                 │
                                 ▼
                             run_graph() → async event-queue scheduler
```

## Why This Split?

1. **Python for expressiveness** — the `>>` operator, `@op`/`@graph` decorators, `PARENT` refs, context managers
2. **Rust for performance** — DashMap concurrent state, tokio async scheduler, zero-copy JSON
3. **No FFI coupling** — communicate via JSON, not PyO3/pyo3-asyncio

## Serialization

`GraphOp.serialize()` produces a JSON config dict containing:

- Op names, types, and parameters
- Edge lists (hard edges, soft edges)
- Input/output ref chains (serialized as transform lists)
- Loop configs (until expressions, max iterations, loop vars)
- Nested graph configs (recursive)
- `rust_name` for built-in Rust ops

## Module Map

| Domain | Python | Rust |
|--------|--------|------|
| Engine | `python/hush-icore/hush/core/engine.py` | `rust/hush-icore/src/engine.rs` |
| Graph scheduler | `python/hush-icore/hush/core/ops/graph/scheduler.py` | `rust/hush-icore/src/ops/graph/graph_op.rs` |
| State | `python/hush-icore/hush/core/states/state.py` | `rust/hush-icore/src/states/state.rs` |
| Config | `python/hush-icore/hush/core/configs/` | `rust/hush-icore/src/config.rs` |
| Providers | `python/hush-providers/` | `rust/hush-providers/` |
| Tracing | `python/hush-icore/hush/core/tracing/` | `rust/hush-icore/src/tracing/` |
| HTTP serve | `python/hush-serve/` | `rust/hush-serve/` |

See `MODULE_MAP.md` in the repository root for the complete mapping.

## Key Differences

| Aspect | Python | Rust |
|--------|--------|------|
| State storage | `MemoryState` (Cell list, index-based) | `EngineState` (DashMap, string-interned keys) |
| Scheduler | asyncio event loop + Queue | tokio mpsc channel + spawn |
| Parallelism | asyncio tasks | tokio::spawn + rayon (CPU-bound) |
| Op dispatch | Dynamic (async/sync, executor) | Static match on `rust_name` or provider type |
| Context IDs | Tuple `("main", "[0]")` | Dot-separated `"main.[0]"` |

## cdylib Plugin System

Custom Rust ops are compiled as shared libraries (`.so`/`.dylib`/`.dll`) and loaded at runtime via `libloading`.

### Architecture

```
hush-plugin crate
├── OpRegistry trait       # Interface for plugin ops
├── hush_plugin! macro     # Auto-generate registry + C ABI exports
└── C ABI functions        # rush_create_registry(), rush_destroy_registry()

hush-serve
├── --plugin flag          # Load shared libraries at runtime
├── libloading             # Dynamic library loading
└── OpRegistry dispatch    # Route ops to plugin functions
```

### Flow

1. Plugin author writes `fn(&Value) -> Value` functions in a `cdylib` crate
2. `hush_plugin!` macro generates `OpRegistry` impl + C ABI export functions
3. `hush-serve --plugin ./target/release/libmy_ops.so` loads the library
4. Python's `_rust_bridge.py` auto-detects plugin crates and passes `--plugin` when spawning hush-serve
5. Ops referenced via `@op(rust="./my-crate::func")` dispatch to the plugin at runtime

### Op dispatch

All Rust ops are dispatched via the `OpRegistry` trait in `hush-icore/src/registry.rs`. There are no built-in ops — every custom op must be provided by a cdylib plugin crate. All ops use the same `fn(&Value) -> Value` signature.

## Usage

```python
# Python backend (default) — FastAPI + uvicorn
engine = Hush(graph)
engine.serve(port=8000)

# Rust backend — Axum + hush-serve
engine.serve(port=8000, backend="rust", rust_ops="rust_ops")

# Standalone Rust (no Python)
# hush-serve --config graph.json --plugin ./target/release/libmy_ops.so
```
