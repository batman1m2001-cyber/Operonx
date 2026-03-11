# rush-core

High-performance Rust execution backend for Hush workflows. Pure `rlib` crate — standalone engine, no PyO3. Rust + Hush = Rush.

## Module Structure

```
rush-core/
├── src/
│   ├── lib.rs              # Crate root (module declarations)
│   ├── engine.rs           # Rush engine — new(json) + run_json(inputs) entry point
│   ├── config.rs           # Config deserialization (GraphConfig, OpConfig, LoopConfig, etc.)
│   ├── runtime.rs          # Tokio runtime helpers (block_on_async)
│   ├── builtin_ops/
│   │   ├── mod.rs          # Dispatch: call() for regular ops, call_generator() for generators
│   │   └── ops.rs          # Op implementations (regular + generator ops)
│   ├── ops/
│   │   ├── mod.rs
│   │   ├── base.rs         # Leaf op execution, ref resolution, PENDING sentinel
│   │   └── graph/
│   │       ├── graph_op.rs # Async event-queue scheduler, generators, loops, nested graphs
│   │       ├── loop_eval.rs # Loop condition evaluation (until expressions)
│   │       └── mod.rs
│   ├── refs/
│   │   └── ref_transforms.rs # Ref transform chain evaluation (getitem, arithmetic, boolean, etc.)
│   └── states/
│       └── state.rs        # EngineState — concurrent DashMap with context hierarchy fallback
├── tests/                  # Rust integration tests (140 tests)
│   ├── common/mod.rs       # Test helpers (config builders)
│   ├── engine.rs           # Core engine tests (single ops, chains, branches, nested graphs)
│   ├── builtin_ops.rs      # Builtin op unit tests
│   ├── complex_graphs.rs   # Complex graph topologies
│   ├── concurrency.rs      # Concurrent execution tests
│   ├── provider_ops.rs     # Provider op tests
│   ├── context_fallback.rs # Context hierarchy fallback tests
│   ├── generator.rs        # Generator op tests (range_gen, chunk_text)
│   ├── streaming.rs        # Full streaming pipeline tests
│   ├── loop.rs             # Loop tests (counter, fibonacci, accumulator)
│   └── pending.rs          # PENDING sentinel tests
├── benches/
│   ├── bench_runner.rs     # Standalone Rust benchmark binary (rush-bench)
│   └── bench_e2e.py        # Python↔Rust comparison via subprocess
├── Cargo.toml
└── pyproject.toml
```

## Key Files to Read First

1. `src/engine.rs` — Entry point: `Rush::new(json_str)` + `Rush::run_json(inputs, req_id, user_id, session_id)`
2. `src/ops/graph/graph_op.rs` — Async event-queue scheduler (1:1 port of Python scheduler.py)
3. `src/states/state.rs` — Concurrent state with context hierarchy fallback
4. `src/ops/base.rs` — Leaf op execution, ref resolution, PENDING sentinel
5. `src/config.rs` — Config deserialization (GraphConfig, OpConfig, LoopConfig)

## Architecture

### Builder-Executor Split

Python builds graphs via DSL, serializes to config dict, Rust loads and executes:

```
Python (build time)             Rust (run time)
─────────────────               ──────────────
GraphOp DSL                     Rush(config)
  │                               │
  ▼                               ▼
graph.serialize() ──dict──→  GraphConfig::from_json()
                               │
                               ▼
                           run_graph() → run_scheduler() [async event-queue]
                               │
                               ├── CPU-bound ops → inline execution
                               ├── IO-bound ops → tokio::spawn_blocking
                               ├── Nested graphs → tokio::spawn (async)
                               ├── Generator ops → tokio::spawn (yield items)
                               └── Branch ops → inline condition evaluation
```

### Async Event-Queue Scheduler

The scheduler uses tokio channels as the event queue (1:1 mapping with Python's asyncio.Queue):

```rust
enum SchedulerEvent {
    Done(op_name, context),           // Op completed — propagate to successors
    DonePending(op_name, context),    // Op returned PENDING — no propagation
    Yield(gen_name, stream_ctx, data), // Generator yielded — create stream context
    Exhausted(gen_name),              // Generator done — decrement active_count
}
```

**Dispatch classification:**
- CPU-bound builtin ops → inline execution (no task spawn)
- IO-bound / provider ops → `tokio::task::spawn_blocking`
- Nested graph ops → `tokio::spawn` (calls `run_scheduler` directly — avoids deadlock)
- Generator ops → `tokio::spawn` (iterates items, emits Yield events)
- Branch ops → inline condition evaluation

### Context Hierarchy Fallback

`EngineState.get()` walks up the dot-separated context hierarchy:
```
"main.[0].[1]" → "main.[0]" → "main"
```

This lets stream context ops inherit values from parent (batch) contexts automatically.
Root context is `"main"` (matches Python's `DEFAULT_CONTEXT = ("main",)`).

### Generator Ops

Three types of generators, all using the same scheduler event flow:

| Type | Implementation | Scheduler Event |
|------|---------------|-----------------|
| Built-in generator | `call_generator()` → `Vec<Value>` → iterate | Yield per item, then Exhausted |
| Provider streaming (LLM) | `execute_streaming()` via `std::sync::mpsc::Sender` | Yield per chunk, then Exhausted |
| Custom generator | `Vec<Value>` return | Same as built-in |

**Stream predecrements:** When a generator yields, batch predecessors are already done. Their edges are pre-subtracted from fresh ready_counts so downstream ops become ready immediately after the gen→successor edge is decremented by propagate.

### Loop Support (GraphOp.loop)

Replaces WhileOp. A graph with `loop_config` runs repeatedly until `until` condition met or `max_iterations` reached:

```rust
struct LoopConfig {
    until: Option<String>,      // e.g., "new_counter >= 5"
    max_iterations: usize,
    loop_vars: Vec<String>,     // Variables fed back between iterations
}
```

Loop contexts: `"main"` (iteration 0), `"main.loop_1"`, `"main.loop_2"`, etc.
Final outputs are copied back to base context for the parent graph.

### PENDING Sentinel

Ops can return `{"__pending__": true}` to absorb input without triggering downstream propagation. The scheduler emits `DonePending` instead of `Done`.

### Concurrent State (DashMap)

`EngineState` uses `DashMap<(Spur, Spur, Spur), Arc<Value>>` for lock-free concurrent reads/writes with string interning.

Key API:
- `get(full_name, var, context) -> Option<Arc<Value>>` — with hierarchy fallback
- `set(full_name, var, context, value)` — takes `&self` (not `&mut self`)
- `add_tags(tags)` — internally locked
- `values_snapshot()` — collect all entries for export

### Dependencies

| Crate | Purpose |
|-------|---------|
| `ahash 0.8` | Fast HashMap for scheduling (ready_count, soft_satisfied) |
| `dashmap 6` | Concurrent HashMap for EngineState (thread-safe values store) |
| `lasso 0.7` | String interning for zero-alloc key lookups |
| `smallvec 1` | Stack-allocated small vectors |
| `serde / serde_json 1` | JSON serialization |
| `tokio 1` | Async runtime (scheduler event loop, task spawning) |
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

Built-in Rust ops are an internal module of rush-core -- no external crate, no dynamic loading, no C ABI.

### 1. Add the op function to `src/builtin_ops/ops.rs`:

```rust
// Regular op (returns single Value)
pub fn my_op(inputs: &serde_json::Value) -> serde_json::Value {
    let x = inputs["x"].as_i64().unwrap();
    serde_json::json!({"result": x * 2})
}

// Generator op (returns Vec<Value> — each item is one yield)
pub fn my_gen(inputs: &serde_json::Value) -> Vec<serde_json::Value> {
    let n = inputs["n"].as_i64().unwrap_or(3);
    (0..n).map(|i| serde_json::json!({"value": i})).collect()
}
```

### 2. Add dispatch arms in `src/builtin_ops/mod.rs`:

```rust
// In call():
"my_op" => ops::my_op(inputs),

// In call_generator():
"my_gen" => ops::my_gen(inputs),
```

### 3. Use in Python:

```python
@op(rust="my_op")
def my_op(x: int):
    return {"result": x * 2}  # Python fallback
```

### Dispatch Architecture

- **Op implementations** (`src/builtin_ops/ops.rs`): Plain `pub fn(&Value) -> Value` functions
- **Generator implementations** (`src/builtin_ops/ops.rs`): `pub fn(&Value) -> Vec<Value>` functions
- **Dispatch** (`src/builtin_ops/mod.rs`): Match on `rust_name` string, call the corresponding function directly
- **No dynamic loading**: builtin_ops is an internal module of rush-core

## Gotchas

1. **Nested graph deadlock** — nested graphs MUST call `run_scheduler()` directly from async context, NOT go through `run_graph()` → `block_on_async()` which deadlocks from inside `tokio::spawn`
2. **Stream predecrements** — only for OTHER batch predecessors already done, NOT for the generator's own edge (handled by propagate)
3. **Timing metadata** — uses `$`-prefixed keys (`$start_time`, `$end_time`, `$duration_ms`)
4. **Branch output refs** — must target parent graph (`output_ref("g", key)` not `output_ref("g.a", key)`)
5. **Ref transforms** — Python handles operator overloading at build time, Rust evaluates the serialized transforms chain
6. **Provider streaming** — uses `std::sync::mpsc::Sender` (NOT tokio channel) because `execute_streaming` expects it
7. **Loop output context** — loop final outputs are copied back to base_context so caller finds them

## Deep Documentation Links

| Topic | File |
|-------|------|
| Execution flow | [docs/architecture/execution-flow.md](../../docs/architecture/execution-flow.md) |
| State model | [docs/architecture/state-model.md](../../docs/architecture/state-model.md) |
| Streaming design | [docs/architecture/streaming.md](../../docs/architecture/streaming.md) |
| Rust-Python split | [docs/architecture/rust-python-split.md](../../docs/architecture/rust-python-split.md) |
