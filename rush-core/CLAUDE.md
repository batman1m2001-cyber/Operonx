# rush-core

High-performance Rust execution backend for Hush workflows. Compiles to a Python extension module via PyO3. Rust + Hush = Rush.

## Module Structure

```
rush-core/
├── src/
│   ├── lib.rs              # PyO3 module entry point
│   ├── engine.rs           # Rush engine (#[pyclass]) — run() entry point
│   ├── config.rs           # Config deserialization (GraphConfig, OpConfig, etc.)
│   ├── ops/
│   │   ├── mod.rs
│   │   ├── base.rs         # Leaf op execution, ref resolution, result storage
│   │   ├── graph/
│   │   │   └── graph_op.rs # Graph scheduling loop, batch parallel, nested graphs
│   │   ├── iteration/
│   │   │   ├── for_op.rs   # ForOp — iterate over lists
│   │   │   └── while_op.rs # WhileOp — loop until condition
│   │   └── transform/
│   │       └── func_op.rs  # Rust-native op registry (string, json, math ops)
│   ├── refs/
│   │   └── interpreter.rs  # Ref op chain evaluation (getitem, arithmetic, boolean, etc.)
│   └── states/
│       └── state.rs        # EngineState — concurrent DashMap + Mutex state
├── tests/                  # Python tests via pytest
├── benches/
│   └── bench_e2e.py        # E2E benchmark: Python vs Rust mode
├── Cargo.toml
└── pyproject.toml
```

## Key Files to Read First

1. `src/engine.rs` — Entry point: `Rush::new(config)` + `Rush::run(inputs)`
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

`EngineState` uses `DashMap<(String, String, String), PyObject>` for lock-free concurrent reads/writes. Tags and execution order use `Mutex<Vec>`.

```rust
// Thread-safe — no &mut needed
state.set(op_name, var_name, context, value);
let val = state.get(py, op_name, var_name, context);  // Returns owned clone
```

Key API:
- `get(py, full_name, var, context) -> Option<PyObject>` — owned clone (needs GIL for clone_ref)
- `set(full_name, var, context, value)` — takes `&self` (not `&mut self`)
- `add_tags(tags)` / `record_execution(...)` — internally locked
- `values_snapshot(py)` — collect all entries for export

### Batch-Aware Parallel Scheduler

The `run_graph()` scheduler drains all ready ops into a batch each iteration:

```
queue: [A, B, C]  →  drain batch
                      │
                      ├── All pure Python? → sequential (one at a time)
                      │
                      └── Any rust_op?     → parallel via rayon
                                             py.allow_threads(|| {
                                               batch.par_iter().for_each(|op| {
                                                 Python::with_gil(|py| execute(py, op))
                                               })
                                             })
```

**Heuristic**: Parallel mode activates when `batch.len() > 1` AND at least one op has `rust_op` set. Pure Python batches run sequentially to avoid `allow_threads`/`with_gil` overhead (GIL prevents true parallelism for Python ops anyway).

**Why this is safe**:
- Batch ops are independent (`ready_count == 0`) — no data dependencies
- `GraphConfig` is `&` (shared immutable) — `PyObject` is `Send + Sync`
- `EngineState` uses DashMap — concurrent access is lock-free for different keys
- Successor activation happens sequentially AFTER all parallel ops complete

### Dependencies

| Crate | Purpose |
|-------|---------|
| `pyo3 0.22` | Python-Rust bridge, GIL management |
| `ahash 0.8` | Fast HashMap for scheduling (ready_count, soft_satisfied) |
| `dashmap 6` | Concurrent HashMap for EngineState (thread-safe values store) |
| `rayon 1.10` | Work-stealing thread pool for batch parallel execution |
| `smallvec 1` | Stack-allocated small vectors |

## Build & Test

```bash
cd rush-core

# Dev build (fast compile, debug)
uv run maturin develop

# Release build (optimized, LTO)
uv run maturin develop --release

# Run tests
uv run python -m pytest tests/ -v --tb=short

# Run benchmarks (release build recommended)
uv run maturin develop --release && uv run python benches/bench_e2e.py
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

## Adding a New Rust-Native Op

1. Add the function in `src/ops/transform/func_op.rs`:
```rust
fn my_op(py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<Option<PyObject>> {
    let x: i64 = inputs.get_item("x")?.unwrap().extract()?;
    let result = PyDict::new_bound(py);
    result.set_item("output", x * 2)?;
    Ok(Some(result.unbind()))
}
```

2. Register in `INTERNAL_OPS` HashMap and `has_internal()`/`execute_internal()`.

3. In Python, tag the op with `@rust_op("my_op")` decorator.

## Performance (Benchmark Results)

Release build, Python 3.13, comparing `mode="python"` vs `mode="rust"`:

| Pattern | Speedup |
|---------|---------|
| Linear chain (50-500 ops) | 2.3x – 2.7x |
| Nested @graph (2-20 stages) | 3.4x – 3.9x |
| Parallel fan-out (5-50 branches) | 2.9x – 3.2x |
| Branching (5-20 stages) | 2.0x – 2.4x |
| ForOp loop (10-100 items) | 3.0x – 3.3x |
| Production-like (3-10 cases) | 2.3x – 2.5x |
| CPU contention (hash chains) | 2.4x – 6.1x |
| Production + CPU | 3.4x – 5.1x |

## Gotchas

1. **`Py<T>` doesn't implement `Clone`** — use `clone_ref(py)` with a GIL token
2. **`state.get()` needs `py`** — DashMap returns owned clone via `clone_ref(py)`
3. **GIL limits Python op parallelism** — rayon only helps Rust-native ops or I/O ops that release GIL
4. **MapOp not supported** — use ForOp in Rust mode (MapOp is asyncio-based)
5. **Async ops** — Rust mode calls the sync version of all ops (no asyncio)

## Deep Documentation Links

| Topic | File |
|-------|------|
| Python scheduling (asyncio) | [architecture/engine/scheduling.md](../architecture/engine/scheduling.md) |
| Python execution flow | [architecture/engine/execution-flow.md](../architecture/engine/execution-flow.md) |
| State system design | [architecture/state/overview.md](../architecture/state/overview.md) |
| Op internals | [architecture/ops/base-op.md](../architecture/ops/base-op.md) |
