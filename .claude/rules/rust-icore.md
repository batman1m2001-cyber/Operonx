---
paths: ["rust/hush-icore/**"]
---

# hush-icore (Rust)

Pure Rust execution backend. No PyO3 — standalone rlib.

## Module Structure

```
src/
├── engine.rs           # Hush::new(json) + run_json(inputs) entry point
├── config.rs           # GraphConfig, OpConfig, LoopConfig deserialization
├── runtime.rs          # Tokio runtime helpers (block_on_async)
├── registry.rs         # OpRegistry trait — plugin dispatch via cdylib
├── logging.rs          # Shared log_templates.json format
├── ops/
│   ├── base.rs         # Leaf op execution, ref resolution, PENDING sentinel
│   └── graph/
│       ├── graph_op.rs # Async event-queue scheduler, generators, loops
│       └── loop_eval.rs # Loop condition evaluation (until expressions)
├── refs/
│   └── ref_transforms.rs # Transform chain: getitem, arithmetic, boolean, etc.
└── states/
    └── state.rs        # EngineState — DashMap + context hierarchy fallback
```

## Architecture

### Builder-Executor Split

Python builds graphs → `graph.serialize()` → JSON → Rust `Hush::new(json)` + `run_json(inputs)`

### Async Event-Queue Scheduler

```rust
enum SchedulerEvent {
    Done(op_name, context),       // Propagate to successors
    DonePending(op_name, context), // PENDING — no propagation
    Yield(gen_name, ctx, data),    // Generator yield → stream context
    Exhausted(gen_name),           // Generator done
}
```

### Dispatch
- CPU-bound plugin ops → `spawn_blocking`
- IO-bound / provider ops → `spawn_blocking`
- Nested graphs → `tokio::spawn` (async, avoids deadlock)
- Generator ops → `tokio::spawn`
- Branch ops → inline

### Context Hierarchy Fallback

`EngineState.get()` walks: `"main.[0].[1]"` → `"main.[0]"` → `"main"`

### Concurrent State

`DashMap<(Spur, Spur, Spur), Arc<Value>>` with string interning (lasso).

### Generator Types

| Type | Implementation |
|------|---------------|
| Plugin generator | `OpRegistry::call_generator()` → `Vec<Value>` → iterate |
| Provider streaming | `execute_streaming()` via `std::sync::mpsc::Sender` |

### Loop Support

`LoopConfig { until, max_iterations, loop_vars }`. Contexts: `"main"`, `"main.loop_1"`, etc.

## Adding a Rust Plugin Op

1. Create cdylib crate, write `fn my_op(inputs: &Value) -> Value`
2. Export: `hush_plugin!(my_op);`
3. Python: `@op(rust="./crate::module::func")`

## Gotchas

1. **Nested graph deadlock** — MUST use `run_scheduler()` directly, NOT `run_graph()` → `block_on_async()`
2. **Stream predecrements** — only for OTHER batch predecessors, not generator's own edge
3. **Timing metadata** — `$start_time`, `$end_time`, `$duration_ms` ($ prefix)
4. **Branch output refs** — must target parent graph, not nested op
5. **Provider streaming** — `std::sync::mpsc::Sender` (NOT tokio channel)
6. **Loop outputs** — copied back to base_context for parent graph
