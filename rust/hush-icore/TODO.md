# Hush-icore Big Update — Streaming Architecture Alignment

**STATUS: ALL 10 PHASES COMPLETE + hush-providers alignment** ✓

Last updated: 2026-03-10
Branch: `feat/stream-architecture`

## Post-Phase: hush-providers alignment ✓

Aligned hush-providers Rust ops with current hush-providers Python ops:

| Change | File | Details |
|--------|------|---------|
| `model_used` from resource key | `ops/llm.rs` | Prefers resource key over API model name (matches Python) |
| `context_used` field | `ops/llm.rs` | Added `estimate_context()` (~4 chars/token) |
| Missing variable detection | `ops/prompt.rs` | Raises error for unreplaced `{var}` (matches Python PromptError) |
| `extract` parser support | `ops/chain.rs` | Added `maybe_parse()` for structured output (Prompt→LLM→Parser) |
| `regex` dependency | `Cargo.toml` | For prompt variable detection |

Tests: 122 hush-providers + all hush-icore pass.

## Context

Python hush-core has replaced iteration ops (ForOp/MapOp/WhileOp/AIterOp) with:
- **Generator ops** — yield items one-by-one, creating stream contexts
- **GraphOp.loop()** — graph runs repeatedly until condition met
- **Event-queue scheduler** — async closures, propagation, PENDING sentinel
- **Tuple contexts** with Cell hierarchy fallback

hush-icore must be updated to match. Generator ops are critical — not optional.

## Architecture Decision: Generators in Rust

Python uses `yield`. Rust uses **channels** for the same semantics:

```
Python:  yield item         → scheduler receives "yield" event → creates stream context
Rust:    tx.send(item).await → scheduler receives from channel  → creates stream context
```

### 3 generator types

| Type | Python | Rust |
|------|--------|------|
| Built-in generator | `yield {"chunk": text}` | Fn returns `Vec<Value>`, scheduler iterates |
| Provider streaming (LLM) | `async for chunk in stream:` | `hush-providers::execute_streaming()` already has `Sender<Value>` |
| Custom generator | User `yield` | Fn returns `Vec<Value>` or `impl Iterator` |

### Scheduler: sync batch-queue → async event-queue

Current hush-icore scheduler is sync + rayon. Must become async + tokio event-queue
(1:1 mapping with Python scheduler.py):

```rust
async fn run_scheduler(graph, state, context_id, parent_context, request_id) {
    let (event_tx, mut event_rx) = tokio::sync::mpsc::unbounded_channel();
    let mut active_count = 0;
    let mut ready_counts: HashMap<String, HashMap<String, i32>>;
    let semaphore = Arc::new(Semaphore::new(64));

    // Async tasks (spawned by dispatch_op)
    async fn task_execute(...)   // runs op → emits Done or DonePending
    async fn task_generator(...) // iterates items → emits Yield per item → Exhausted

    // Scheduling logic
    fn propagate(...)   // decrement successors' ready_counts, return newly ready
    fn dispatch_op(...) // generator → task_generator, sync → inline, async → task_execute
    fn drain_ready(...) // process queue until empty

    // Event loop
    drain_ready(entries);
    while active_count > 0 {
        match event_rx.recv().await {
            Done(name, ctx)          → propagate + drain_ready
            DonePending(name, ctx)   → active_count -= 1
            Yield(name, stream_ctx)  → create stream context + propagate + drain_ready
            Exhausted(name)          → active_count -= 1
        }
    }
    collect_outputs()
}
```

Rayon still used for CPU-bound built-in ops via `tokio::task::spawn_blocking`.

---

## Phases

### Phase 1: EngineState — Context Hierarchy Fallback

**File:** `src/states/state.rs`

Context stays as string, dot-joined: `"main"`, `"main.[0]"`, `"main.[0].[1]"`.
DashMap key type unchanged: `(Spur, Spur, Spur)`.

Update `get()` with fallback:
```rust
pub fn get(&self, full_name: &str, var: &str, context: &str) -> Option<Arc<Value>> {
    // 1. Exact match
    if let Some(val) = self.exact_get(full_name, var, context) {
        return Some(val);
    }
    // 2. Walk up: "main.[0].[1]" → "main.[0]" → "main"
    let mut ctx = context;
    while let Some(dot_pos) = ctx.rfind('.') {
        ctx = &ctx[..dot_pos];
        if let Some(val) = self.exact_get(full_name, var, ctx) {
            return Some(val);
        }
    }
    None
}
```

`set()` unchanged (writes exact context). Root context changes from `""` to `"main"`.

---

### Phase 2: Config Overhaul

**File:** `src/config.rs`

**Add:**
```rust
pub struct LoopConfig {
    pub until: Option<String>,
    pub max_iterations: usize,
    pub loop_vars: Vec<String>,
}
```

**Add to `GraphConfig`:**
```rust
pub stream_predecrements: AHashMap<String, AHashMap<String, i32>>,
pub loop_config: Option<LoopConfig>,
pub max_stream_concurrent: usize,  // default 64
```

**Add to `OpConfig`:**
```rust
pub is_generator: bool,
```

**Remove:**
- `IterationConfig` struct
- `IterParamConfig` struct
- `iteration_config` field from `OpConfig`

**Update parsing:** Remove iteration_config parsing, add is_generator/loop_config/stream_predecrements.

---

### Phase 3: Delete Iteration Module

**Before deleting:** Extract `evaluate_until()` from `while_op.rs` → create `src/ops/graph/loop_eval.rs`

**Delete entire `src/ops/iteration/`:**
- `for_op.rs`, `map_op.rs`, `while_op.rs`, `aiter_op.rs`, `helpers.rs`, `mod.rs`

**Update `src/ops/mod.rs`:** Remove `pub mod iteration;`

---

### Phase 4: Scheduler Rewrite → Async Event-Queue

**File:** `src/ops/graph/graph_op.rs` (biggest change)

Rewrite from sync batch-queue to async event-queue. Port Python `scheduler.py` logic:

```rust
#[derive(Debug)]
enum SchedulerEvent {
    Done(String, String),           // (op_name, context)
    DonePending(String, String),    // (op_name, context)
    Yield(String, String, Value),   // (gen_name, stream_ctx, result)
    Exhausted(String),              // (gen_name)
}

pub(crate) async fn run_scheduler(
    graph: &GraphConfig,
    state: &EngineState,
    context_id: &str,
    parent_context: Option<&str>,
    request_id: &str,
) -> Result<(Value, Vec<String>), RushError> {
    // Local state
    let (event_tx, mut event_rx) = mpsc::unbounded_channel();
    let mut active_count: usize = 0;
    let mut ready_counts: AHashMap<String, AHashMap<String, i32>> = ...;
    let mut soft_satisfied: AHashMap<String, AHashSet<String>> = ...;
    let mut stream_contexts: Vec<String> = vec![];
    let semaphore = Arc::new(Semaphore::new(graph.max_stream_concurrent));

    // ... task_execute, task_generator, propagate, dispatch_op, drain_ready ...
    // ... event loop ...
    // ... collect outputs ...
}
```

**Key functions (same as Python):**

1. `task_execute` — async task for non-inline ops
   - Acquires semaphore for stream contexts (backpressure)
   - Calls `run_op()`, emits `Done` or `DonePending`

2. `task_generator` — async task for generator ops
   - Built-in: call `builtin_ops::call_generator()` → get `Vec<Value>` → iterate
   - Provider streaming: call `execute_streaming()` with channel → receive chunks
   - Each item: store result, emit `Yield`
   - On completion: emit `Exhausted`

3. `propagate` — decrement ready_counts, handle branches + soft edges
4. `dispatch_op` — classify and route: generator/sync-inline/async-task
5. `drain_ready` — process queue until empty

**Rayon integration:** CPU-bound ops still use rayon via `spawn_blocking`:
```rust
let result = tokio::task::spawn_blocking(move || {
    builtin_ops::call(&rust_op, &inputs)
}).await??;
```

---

### Phase 5: Generator Support

**File:** `src/builtin_ops/mod.rs`

Add generator dispatch:
```rust
pub fn call_generator(name: &str, inputs: &Value) -> Option<Vec<Value>> {
    match name {
        "chunk_text" => Some(ops::chunk_text(inputs)),
        // ... more generator ops
        _ => None,
    }
}
```

**File:** `src/builtin_ops/ops.rs`

Add generator op implementations (return `Vec<Value>` instead of single `Value`).

**File:** `src/ops/base.rs`

Add generator classification:
```rust
pub(crate) enum GeneratorKind<'a> {
    Builtin(&'a str),                    // rust_op name → Vec<Value>
    ProviderStream(&'a ProviderConfig),  // LLM streaming → channel
}

pub(crate) fn classify_generator(op: &OpConfig) -> Option<GeneratorKind> {
    if !op.is_generator { return None; }
    if let Some(ref rust_op) = op.rust_op {
        return Some(GeneratorKind::Builtin(rust_op));
    }
    if let Some(ref provider) = op.provider_config {
        return Some(GeneratorKind::ProviderStream(provider));
    }
    None
}
```

---

### Phase 6: Loop Support (GraphOp.loop)

**Create:** `src/ops/graph/loop_eval.rs`
- Extract `evaluate_until()` from old `while_op.rs`
- Patterns: `var op literal`, `var op var`, `var` (truthiness), `not var`, `len(var) op literal`

**Update:** `src/ops/graph/graph_op.rs`

Add `run_loop()`:
```rust
async fn run_loop(
    config: &GraphConfig,
    loop_config: &LoopConfig,
    state: &EngineState,
    base_context: &str,
    parent_context: Option<&str>,
    request_id: &str,
) -> Result<Value, RushError> {
    let mut iteration = 0;
    loop {
        let outputs = get_outputs(config, state, current_ctx)?;
        if evaluate_until(&loop_config.until, &outputs) {
            return Ok(with_metrics(outputs, iteration, true));
        }
        iteration += 1;
        if iteration >= loop_config.max_iterations {
            return Ok(with_metrics(outputs, iteration, false));
        }
        // Feed outputs back → run next iteration
        let next_ctx = format!("{}.iter_{}", base_context, iteration);
        inject_loop_vars(state, config, &next_ctx, &outputs, &loop_config.loop_vars);
        run_scheduler(config, state, &next_ctx, parent_context, request_id).await?;
    }
}
```

---

### Phase 7: PENDING Sentinel

**File:** `src/ops/base.rs`

```rust
pub(crate) enum OpResult {
    Done,
    Pending,
}

fn is_pending(result: &Value) -> bool {
    result.get("__pending__").and_then(|v| v.as_bool()).unwrap_or(false)
}
```

Update `run()` to check PENDING before storing result.
Scheduler: `Done` → propagate, `Pending` → skip propagation.

---

### Phase 8: Engine Entry Point

**File:** `src/engine.rs`
- Root context: `""` → `"main"`
- `run_json()` becomes async (calls async scheduler)
- Wrap in `block_on` for the public sync API

**File:** `src/error.rs`
- Remove `IterationError`
- Add `LoopError(String)`

---

### Phase 9: Python serialize() Sync

**File:** `hush-core/hush/core/ops/graph/graph_op.py` — `serialize()`

Add:
```python
"stream_predecrements": self._stream_predecrements,
"loop_config": {
    "until": self._loop_config.until,
    "max_iterations": self._loop_config.max_iterations,
    "loop_vars": list(self._loop_config.initial_state.keys()),
} if self._loop_config else None,
"max_stream_concurrent": self._max_stream_concurrent,
```

**File:** `hush-core/hush/core/ops/base.py` — `serialize()`

Add:
```python
"is_generator": inspect.isgeneratorfunction(self.core) or inspect.isasyncgenfunction(self.core),
```

---

### Phase 10: Tests + Benchmarks

**Update `tests/engine.rs`:**
- Remove ForOp/MapOp iteration tests
- Convert WhileOp tests → loop_config format
- Context assertions: `""` → `"main"`

**New tests:**
- `tests/context_fallback.rs` — hierarchy fallback in state
- `tests/loop.rs` — GraphOp.loop with until, max_iterations, variable feedback
- `tests/pending.rs` — PENDING sentinel
- `tests/generator.rs` — built-in generator ops, provider streaming
- `tests/streaming.rs` — full pipeline: generator → downstream ops → collect leaf contexts

**Update `benches/bench_e2e.py`:**
- Remove ForOp/MapOp benchmarks
- Add generator + loop patterns
- Add streaming throughput benchmark

---

## Files Summary

| File | Action | Lines |
|------|--------|-------|
| `src/states/state.rs` | MODIFY | +30 |
| `src/config.rs` | MODIFY | +60/-80 |
| `src/error.rs` | MODIFY | +2/-2 |
| `src/engine.rs` | MODIFY | +20/-10 |
| `src/ops/mod.rs` | MODIFY | -1 |
| `src/ops/base.rs` | MODIFY | +40 |
| `src/ops/graph/graph_op.rs` | REWRITE | +400/-300 |
| `src/ops/graph/loop_eval.rs` | CREATE | ~100 |
| `src/ops/graph/mod.rs` | MODIFY | +1 |
| `src/builtin_ops/mod.rs` | MODIFY | +15 |
| `src/builtin_ops/ops.rs` | MODIFY | +30 |
| `src/ops/iteration/*` | DELETE | -780 |
| `tests/engine.rs` | MODIFY | ~200 update |
| `tests/loop.rs` | CREATE | ~150 |
| `tests/generator.rs` | CREATE | ~200 |
| `tests/streaming.rs` | CREATE | ~200 |
| Python serialize() | MODIFY | +15 |
| `benches/bench_e2e.py` | MODIFY | ~100 update |

**Net change: ~-100 lines** (delete iteration > add streaming)

## Implementation Order

```
1. EngineState context fallback        ← foundation
2. Config overhaul                     ← new format
3. Delete iteration + extract loop_eval ← clean slate
4. Scheduler rewrite (async event-queue) ← core change
5. Generator support                   ← streaming heart
6. Loop support                        ← replaces WhileOp
7. PENDING sentinel                    ← absorb-without-propagate
8. Engine entry point                  ← wire everything
9. Python serialize() sync             ← ensure format match
10. Tests + benchmarks                 ← verify
```

Each phase is independently testable. Phase 4 (scheduler) is the biggest risk
but maps 1:1 from Python scheduler.py → well-understood logic.

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Scheduler rewrite (biggest change) | 1:1 port from Python scheduler.py, same event types |
| Context format change | Support both `""` and `"main"` during transition |
| Generator ops perf | Built-in: Vec (zero-overhead), Provider: existing channel |
| Rayon → tokio migration | CPU-bound ops still via `spawn_blocking`, no regression |
| Async public API | Wrap async scheduler in `block_on` for sync Hush::run_json |
