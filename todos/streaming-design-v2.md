# Streaming Architecture v2

## Background: Current Hush Architecture

Hush is a DAG-based workflow engine. The execution model:

1. **Ops** are nodes in a graph. Each op has `run()`: `get_inputs()` -> `core(**inputs)` -> `store_result()`
2. **GraphOp** is a container that holds child ops and a scheduler. It manages edges and execution order.
3. **Scheduler** (inside `GraphOp.run()` at `graph_op.py:761-834`):
   - Uses `ready_count` per op: number of predecessors that must complete before this op fires
   - Starts entry ops (ready_count == 0)
   - When an op completes: decrement successors' ready_count -> fire any that hit 0
   - Supports async concurrency: async ops run as `asyncio.Task`, sync ops inline
   - Terminates when `active_tasks` is empty
4. **State** (`MemoryState`): flat key-value store accessed as `state[op_name, var_name, context_id]`
   - Cell = single value with overwrite semantics
   - context_id = isolation for iteration ops (ForOp/WhileOp) — each iteration gets its own context
   - Push refs: when writing to a cell, can auto-propagate to downstream cells
5. **Edges**: `a >> b` = hard edge (counts toward ready_count), `a >>~ b` = soft edge (doesn't count)

## Problem Statement

We want to extend Hush from batch DAG execution to support **streaming**: ops that produce multiple outputs over time. Example use cases:
- LLM token streaming
- Chunked document processing
- Real-time event processing
- Pipeline where each stage processes items as they arrive

The key requirement: **per-yield forwarding**. When a source yields an item, it should immediately flow to downstream ops — not wait until the source finishes.

## Previous Attempt (v1) — Failed

Built bottom-up: StreamCell (queue), Stream() marker, schema wiring, `_run_stream()` method on BaseOp. All reverted because:

- `_run_stream()` ran the entire generator to completion, THEN the scheduler activated successors — this is batch with a queue, not streaming
- True streaming needs the scheduler to activate successors **per-yield**, not after generator exhausts
- Spread complexity across BaseOp, StateSchema, MemoryState, configs — too many touch points

## v2 Design: Streaming Lives in the Scheduler

### Core Principle

**The op doesn't know it's streaming.** `BaseOp.run()` stays unchanged. Streaming behavior lives entirely in the scheduler (`GraphOp.run()`).

### How an Op Sees the World

An op defines a function: `get_inputs()` -> `core(**inputs)` -> `store_result()`. It receives values and returns values. Whether the input came from a generator or a static value is invisible to the op.

```python
@op
def process(item: str, config: dict):
    return {"result": item.upper()}
```

This op works identically whether:
- `item` is a static string from `PARENT["item"]`
- `item` comes from a streaming source that yields multiple strings

### How the Scheduler Drives Streaming

The scheduler detects generator ops and handles them differently:

**Regular op (return {}):** same as today
```
op.run() completes -> store_result() -> activate_successors(ctx) -> done
```

**Generator op (yield {}):** the scheduler iterates per-yield
```
generator = op.core(**inputs)
for each yield from generator:
    store_result(yield_value, context_id=new_ctx)
    activate_successors(new_ctx)  # downstream fires immediately
generator exhausts -> done
```

Each yield triggers the same `activate_successors()` that a normal op completion does. The only difference: generators activate successors N times (once per yield) in N different contexts. Regular ops activate once.

### Edges Are Unchanged

`a >> b` still means "after a produces, b can fire." The mechanism is identical.

## Context Model

### The Problem: Concurrent Data Flows Need Isolation

When a source yields 3 items and downstream ops run concurrently, they'd overwrite each other's state without isolation:

```
source yields "hello" -> process.input = "hello"
source yields "world" -> process.input = "world"  // overwrites before process reads "hello"!
```

### Solution: Tuple-Based Context IDs with Stream Depth

Context IDs are **tuples** that encode the execution hierarchy:

```python
("main",)                         # top level (depth 0)
("main", "s0")                   # first yield of a generator (depth 1)
("main", "s0", "s0")             # nested generator yield (depth 2)
("main", "[0]")                  # ForOp iteration 0
("main", "s0", "[0]")            # ForOp inside a streaming context
```

**Single source chain:**
```
source >> process >> format >> END

source yield 0 -> ctx=("main","s0") -> process[s0] -> format[s0]
source yield 1 -> ctx=("main","s1") -> process[s1] -> format[s1]
source yield 2 -> ctx=("main","s2") -> process[s2] -> format[s2]
```

Each context has its own state slots. No interference.

**Fork (one source, two branches, then join):**
```
source >> transform_a >> merge
source >> transform_b >> merge

source yield 0 -> ctx=("main","s0")
  -> transform_a[s0] completes -> merge ready_count in ("main","s0"): 2→1
  -> transform_b[s0] completes -> merge ready_count in ("main","s0"): 1→0 → fires!
```

Context propagates through the fork. The join sees matching contexts.

### Stream Depth: O(1) Cross-Context Reads

**Problem:** When `config` (no generator upstream) completes in `("main",)` and `process` runs in `("main", "s0")`, process can't read config's output — different contexts.

**Solution:** At build time, compute each op's **stream depth** — how many generators are upstream:

```python
# Graph: config >> process, outer_gen >> inner_gen >> process >> END
stream_depth = {
    "config": 0,       # no generators upstream
    "outer_gen": 0,    # is a generator at depth 0
    "inner_gen": 1,    # downstream of 1 generator
    "process": 2,      # downstream of 2 generators
}
```

At read time, the correct context for any source op is a **tuple slice**:

```python
# process runs in ("main", "s0", "s0") at depth 2
# Reading from config (depth 0):     ctx[:1] = ("main",)         — O(1)
# Reading from outer_gen (depth 1):  ctx[:2] = ("main", "s0")    — O(1)
# Reading from inner_gen (depth 2):  ctx[:3] = ("main", "s0", "s0") — O(1)
```

**No copying, no fallback chain, no runtime overhead.** Precomputed at build time.

## Ready Count: Per-Context

```python
ready_counts: Dict[tuple, Dict[str, int]]
# ("main",)        → {"process": 2, "format": 1}    # top-level context
# ("main", "s0")   → {"process": 1, "format": 1}    # streaming context (pre-decremented)
# ("main", "s1")   → {"process": 1, "format": 1}
```

Each streaming context starts with a fresh copy of `initial_ready_count`, **pre-decremented** by predecessors that already completed in ancestor contexts:

```python
def _create_stream_context(stream_ctx, source_op, parent_ctx):
    rc = initial_ready_count.copy()
    for op_name in rc:
        for pred in self.prevs[op_name]:
            pred_depth = stream_depths[pred]
            pred_ctx = stream_ctx[:pred_depth + 1]
            if pred in completed_in_ctx.get(pred_ctx, set()):
                rc[op_name] -= 1  # already done, pre-decrement
    ready_counts[stream_ctx] = rc
```

## Join Rules

### Two streaming sources — zip by index

```
source_a (yields 3) >> merge
source_b (yields 2) >> merge
```

Both produce context_ids with the same depth. Matching by index happens naturally via per-context ready_count.

### Fan-in: streaming + non-streaming

```
source (yields 3) >> process
config (returns once) >> process
```

- `config` completes in `("main",)` at depth 0
- `source` yields create `("main", "s0")`, `("main", "s1")`, ...
- When creating each streaming context, config's edge is pre-decremented (already completed)
- `process` reads config via stream depth: `ctx[:0+1] = ("main",)` — O(1), correct

## Unified Event-Queue Scheduler

Replace `asyncio.wait(FIRST_COMPLETED)` with an event queue. All events are **edge-triggered** (no polling).

### Event types

```python
("done", op_name, context_id)      # any op completed in any context
("yield", gen_name, new_ctx)       # generator produced a new context
("exhausted", gen_name)            # generator finished yielding
```

No batch/stream distinction. A regular op completing in `("main",)` and a regular op completing in `("main", "s0")` are both `("done", ...)` events.

### Main loop

```python
# Start entries in top-level context
for entry in entries:
    _schedule_op(entry, context_id, parent_context)

# Unified event loop
while active_count > 0:
    event = await event_queue.get()  # edge-triggered, suspends until event

    if event[0] == "done":
        _, op_name, ctx = event
        completed_in_ctx[ctx].add(op_name)
        newly_ready = _activate_successors(op_name, ctx)
        for name in newly_ready:
            _schedule_op(name, ctx, parent_context)

    elif event[0] == "yield":
        _, gen_name, stream_ctx = event
        _create_stream_context(stream_ctx, gen_name)
        newly_ready = _activate_successors(gen_name, stream_ctx)
        for name in newly_ready:
            _schedule_op(name, stream_ctx, parent_context)  # with semaphore

    elif event[0] == "exhausted":
        active_count -= 1

# Result collection
if stream_contexts:
    _outputs = {var: [state[self.name, var, ctx] for ctx in stream_contexts] for var in self.outputs}
else:
    _outputs = self.get_outputs(state, context_id, parent_context)
```

### Backpressure

Semaphore cap (`max_concurrent`, default 64) limits concurrent downstream contexts:

```python
semaphore = asyncio.Semaphore(max_concurrent)

# When scheduling op in streaming context:
async with semaphore:
    await op.run(state, stream_ctx, parent_context)
```

## Resolved Design Questions

| Question | Decision | Rationale |
|----------|----------|-----------|
| END collection | Aggregate into list | Consistent with ForOp pattern |
| Error handling | Fail entire graph | Matches current behavior, simplest for v1 |
| Backpressure | Semaphore cap | Limits concurrent contexts |
| Broadcast | Stream depth + tuple slice | O(1), no copying |
| Multiple streaming inputs | Zip by index | Natural via per-context ready_count |
| Scheduler architecture | Unified event queue | Edge-triggered, extensible, replaces `asyncio.wait` |
| Context ID format | Tuples | Enables O(1) depth slicing, hierarchical |

## What Doesn't Change

- `BaseOp.run()` — completely unchanged
- `BaseOp.store_result()` — unchanged
- Edge operators (`>>`, `>>~`) — unchanged
- `StateSchema` — unchanged
- ForOp / WhileOp — unchanged in Phase 1 (refactored in Phase 2)
- All existing tests — must pass without modification

## What Changes

1. **`@op` decorator** (`func_op.py`):
   - Auto-detect generators via `inspect.isgeneratorfunction` / `inspect.isasyncgenfunction`
   - Set `_is_generator` flag
   - Handle `ast.Yield` in `extract_return_schema()` for output key detection

2. **`BaseOp`** (`base.py`):
   - Add `_is_generator = False` class attribute
   - `get_inputs()` uses `stream_depths` for tuple-sliced context reads

3. **`GraphOp.build()`** (`graph_op.py`):
   - Compute `_stream_depths` via topological traversal
   - Detect `_has_streaming_ops`

4. **`GraphOp.run()` scheduler** (`graph_op.py:761-834`):
   - Replace `asyncio.wait` with event queue
   - Per-context `ready_counts: Dict[tuple, Dict[str, int]]`
   - Generator driving: `_drive_generator()` emits yield/exhausted events
   - Stream context creation with pre-decremented ready_count
   - Backpressure via semaphore
   - Result collection from streaming contexts

5. **Context ID format** (`cell.py`, `state.py`, `iteration/base.py`):
   - `DEFAULT_CONTEXT = ("main",)` (was `"main"`)
   - `get_iter_context` returns tuple
   - Cell/MemoryState keyed by tuple (no logic changes)

## Implications: Iteration Ops Become Redundant

The streaming scheduler provides everything ForOp does:

| ForOp Feature | Streaming Equivalent |
|---------------|---------------------|
| `Each([1,2,3])` | Generator that yields each item |
| `Broadcast()` | Stream depth resolves to parent context |
| Parallel execution | Semaphore (default: concurrent) |
| Sequential execution | `max_concurrent=1` |
| Result collection (column-oriented) | Scheduler collects into lists |
| Context isolation per iteration | Context isolation per yield |
| Error handling (skip/fail) | Scheduler error policy |

ForOp (~200 lines + own scheduler) becomes a 3-line generator. See Phase 2 plan.

WhileOp is harder — it requires feedback (downstream output → next iteration input). Generators can't receive downstream results without `gen.send()`. See Phase 2 plan for options.
