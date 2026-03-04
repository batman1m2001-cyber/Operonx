# rush-core

High-performance Rust execution backend for Hush workflows. Pure `rlib` crate — standalone engine, no PyO3. Rust + Hush = Rush.

## Module Structure

```
rush-core/
├── src/
│   ├── lib.rs              # Crate root (module declarations)
│   ├── engine.rs           # Rush engine — new(json) + run_json(inputs) entry point
│   ├── config.rs           # Config deserialization (GraphConfig, OpConfig, etc.)
│   ├── builtin_ops.rs      # Built-in op dispatch (match on rust_name → direct function call)
│   ├── ops/
│   │   ├── mod.rs
│   │   ├── base.rs         # Leaf op execution, ref resolution
│   │   ├── graph/
│   │   │   └── graph_op.rs # Graph scheduling loop, batch parallel, nested graphs
│   │   ├── iteration/
│   │   │   ├── for_op.rs   # ForOp — iterate over lists
│   │   │   └── while_op.rs # WhileOp — loop until condition
│   │   └── transform/
│   │       └── func_op.rs  # FuncOp execution (builtin dispatch or Python callback)
│   ├── refs/
│   │   └── ref_transforms.rs # Ref transform chain evaluation (getitem, arithmetic, boolean, etc.)
│   └── states/
│       └── state.rs        # EngineState — concurrent DashMap state (pure serde_json::Value)
├── tests/                  # Rust integration tests (95+ tests)
├── benches/
│   ├── bench_runner.rs     # Standalone Rust benchmark binary (rush-bench)
│   └── bench_e2e.py        # Python↔Rust comparison via subprocess
├── Cargo.toml
└── pyproject.toml
```

## Key Files to Read First

1. `src/engine.rs` — Entry point: `Rush::new(json_str)` + `Rush::run_json(inputs, req_id, user_id, session_id)`
2. `src/ops/graph/graph_op.rs` — Scheduler: batch-aware parallel execution
3. `src/states/state.rs` — Concurrent state: DashMap + Mutex
4. `src/ops/base.rs` — Leaf op execution, ref resolution
5. `src/config.rs` — Config deserialization from Python dict

## Architecture

### Builder-Executor Split

Python builds graphs via DSL, serializes to config dict, Rust loads and executes:

```
Python (build time)             Rust (run time)
─────────────────               ──────────────
GraphOp DSL                     Rush(config)
  │                               │
  ▼                               ▼
graph.serialize() ──dict──→  GraphConfig::from_dict()
                               │
                               ▼
                           run_graph() scheduler
                               │
                               ▼
                           execute_leaf_op() / nested / for / while
```

### Concurrent State (DashMap)

`EngineState` uses `DashMap<(String, String, String), serde_json::Value>` for lock-free concurrent reads/writes.

```rust
// Thread-safe — no &mut needed
state.set(op_name, var_name, context, value);
let val = state.get(op_name, var_name, context);  // Returns cloned Value
```

Key API:
- `get(full_name, var, context) -> Option<Value>` — cloned value
- `set(full_name, var, context, value)` — takes `&self` (not `&mut self`)
- `add_tags(tags)` — internally locked
- `values_snapshot()` — collect all entries for export

### Batch-Aware Parallel Scheduler

The `run_graph()` scheduler drains all ready ops into a batch each iteration:

```
queue: [A, B, C]  →  drain batch
                      │
                      ├── batch.len() == 1  → execute directly
                      │
                      └── batch.len() > 1   → parallel via rayon
                                              batch.par_iter().for_each(|op| {
                                                execute_leaf_op(op, &state, &config)
                                              })
```

**Heuristic**: Parallel mode activates when `batch.len() > 1`. All ops execute via rayon without GIL constraints.

**Why this is safe**:
- Batch ops are independent (`ready_count == 0`) — no data dependencies
- `GraphConfig` is `&` (shared immutable)
- `EngineState` uses DashMap — concurrent access is lock-free for different keys
- Successor activation happens sequentially AFTER all parallel ops complete

### Dependencies

| Crate | Purpose |
|-------|---------|
| `ahash 0.8` | Fast HashMap for scheduling (ready_count, soft_satisfied) |
| `dashmap 6` | Concurrent HashMap for EngineState (thread-safe values store) |
| `rayon 1.10` | Work-stealing thread pool for batch parallel execution |
| `smallvec 1` | Stack-allocated small vectors |
| `rush-ops-builtin` | Built-in Rust op implementations (direct rlib dependency) |
| `serde / serde_json 1` | JSON serialization |
| `tokio 1` | Async runtime |
| `chrono 0.4` | Timestamp metadata |
| `rush-providers` | Native provider implementations |

## Build & Test

```bash
cd rush-core

# Run all tests (unit + integration)
cargo test

# Run rush-providers tests
cargo test -p rush-providers

# Release build (optimized, LTO)
cargo build --release

# Run benchmarks (release build recommended)
cargo build --release && uv run python benches/bench_e2e.py
```

## Usage from Python

```python
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def double(x: int):
    return {"result": x * 2}

with GraphOp(name="workflow") as graph:
    step = double(x=PARENT["input"])
    START >> step >> END

# Python mode (default)
engine = Hush(graph)
result = await engine.run(inputs={"input": 5})

# Rust mode — 2-6x faster
result = await engine.run(inputs={"input": 5}, mode="rust")
```

## Adding a New Built-in Rust Op

Built-in Rust ops are linked directly into rush-core as a standard rlib dependency -- no dynamic loading, no C ABI.

### 1. Add the op function to `examples/rush-ops-builtin/src/lib.rs`:

```rust
pub fn my_op(inputs: &serde_json::Value) -> serde_json::Value {
    let x = inputs["x"].as_i64().unwrap();
    serde_json::json!({"result": x * 2})
}
```

### 2. Add a dispatch arm in `rush-core/src/builtin_ops.rs`:

```rust
"my_op" => rush_ops_builtin::my_op(inputs),
```

### 3. Use in Python:

```python
@op(rust="my_op")
def my_op(x: int):
    return {"result": x * 2}  # Python fallback
```

### Dispatch Architecture

- **Op implementations** (`examples/rush-ops-builtin/`): Plain `pub fn(&Value) -> Value` functions in a standard rlib crate
- **Dispatch** (`src/builtin_ops.rs`): Match on `rust_name` string, call the corresponding function directly
- **No dynamic loading**: rush-ops-builtin is a Cargo dependency of rush-core, linked at compile time

## Performance (Benchmark Results)

Release build, Python 3.13, comparing `mode="python"` vs `mode="rust"`:

| Pattern | Speedup | Py mean | Rs mean |
|---------|---------|---------|---------|
| Linear chain (50-500 ops) | 1.9x – 2.4x | 0.31–3.02ms | 0.13–1.27ms |
| Nested @graph (2-20 stages) | 3.6x – 3.9x | 0.20–1.87ms | 0.06–0.48ms |
| Parallel fan-out (5-50 branches) | 2.8x – 3.0x | 0.17–1.39ms | 0.06–0.46ms |
| Branching (5-20 stages) | 2.1x – 2.4x | 0.16–0.65ms | 0.08–0.28ms |
| ForOp loop (10-100 items) | 3.2x | 0.19–1.61ms | 0.06–0.51ms |
| Production-like (3-10 cases) | 2.4x – 2.5x | 0.18–0.44ms | 0.08–0.18ms |
| CPU contention (hash chains) | 4.7x – 6.0x | 18–97ms | 3–17ms |
| Production + CPU | 4.9x – 6.2x | 19–75ms | 3–15ms |
| Pure CPU chain (sequential) | 1.1x – 2.7x | 20–75ms | 8–68ms |

## Gotchas

1. **MapOp not supported** — use ForOp in Rust mode (MapOp is asyncio-based)
2. **Timing metadata** — uses `$`-prefixed keys (`$start_time`, `$end_time`, `$duration_ms`)
3. **Branch output refs** — must target parent graph (`output_ref("g", key)` not `output_ref("g.a", key)`)
4. **Ref transforms** — Python handles operator overloading at build time, Rust evaluates the serialized transforms chain

## Deep Documentation Links

| Topic | File |
|-------|------|
| Python scheduling (asyncio) | [architecture/engine/scheduling.md](../architecture/engine/scheduling.md) |
| Python execution flow | [architecture/engine/execution-flow.md](../architecture/engine/execution-flow.md) |
| State system design | [architecture/state/overview.md](../architecture/state/overview.md) |
| Op internals | [architecture/ops/base-op.md](../architecture/ops/base-op.md) |
