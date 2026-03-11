# Rust-Python Split

## Builder-Executor Architecture

Hush uses a **builder-executor split**: Python builds graphs via DSL, serializes to JSON config, and Rust loads and executes.

```
Python (build time)              Rust (run time)
─────────────────                ──────────────
GraphOp DSL                      Rush(config_json)
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
| Engine | `python/hush-core/hush/core/engine.py` | `rust/rush-core/src/engine.rs` |
| Graph scheduler | `python/hush-core/hush/core/ops/graph/scheduler.py` | `rust/rush-core/src/ops/graph/graph_op.rs` |
| State | `python/hush-core/hush/core/states/state.py` | `rust/rush-core/src/states/state.rs` |
| Config | `python/hush-core/hush/core/configs/` | `rust/rush-core/src/config.rs` |
| Providers | `python/hush-providers/` | `rust/rush-providers/` |
| Tracing | `python/hush-core/hush/core/tracing/` | `rust/rush-core/src/tracing/` |
| HTTP serve | `python/hush-serve/` | `rust/rush-serve/` |

See [MODULE_MAP.md](../../MODULE_MAP.md) for the complete mapping.

## Key Differences

| Aspect | Python | Rust |
|--------|--------|------|
| State storage | `MemoryState` (Cell list, index-based) | `EngineState` (DashMap, string-interned keys) |
| Scheduler | asyncio event loop + Queue | tokio mpsc channel + spawn |
| Parallelism | asyncio tasks | tokio::spawn + rayon (CPU-bound) |
| Op dispatch | Dynamic (async/sync, executor) | Static match on `rust_name` or provider type |
| Context IDs | Tuple `("main", "[0]")` | Dot-separated `"main.[0]"` |

## Usage

```python
# Python mode (default)
engine = Hush(graph)
result = await engine.run(inputs={"x": 5})

# Rust mode
result = await engine.run(inputs={"x": 5}, mode="rust")

# Standalone Rust (no Python)
# rush-serve --config graph.json
```
