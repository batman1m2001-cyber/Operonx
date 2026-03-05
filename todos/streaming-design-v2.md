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

### Current Scheduler Code (simplified)

```python
# GraphOp.run() — current batch scheduler
ready_count = self.initial_ready_count.copy()  # dict[op_name, int]
active_tasks = {}

# Start entry ops
await _schedule_ops(self.entries)

# Main loop
while active_tasks:
    done, _ = await asyncio.wait(active_tasks.values(), FIRST_COMPLETED)
    for task in done:
        op_name = task.get_name()
        active_tasks.pop(op_name)
        # Decrement successors, fire newly ready
        await _schedule_ops(_activate_successors(op_name))
```

Each op fires exactly once. `_activate_successors` decrements ready_count and returns ops that hit 0.

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

**Batch op (return {}):** same as today
```
op.run() completes -> store_result() -> activate_successors() -> done
```

**Streaming op (yield {}):** the scheduler iterates per-yield
```
generator = op.core(**inputs)
for each yield from generator:
    store_result(yield_value, context_id=new_ctx)
    activate_successors(context_id=new_ctx)  # downstream fires immediately
generator exhausts -> done
```

Each yield triggers the same `activate_successors()` that a normal op completion does. The source doesn't wait for consumers — it's an async task yielding concurrently while consumers run on their own.

### Edges Are Unchanged

`a >> b` still means "after a produces, b can fire." The only difference: for generators, successors get activated N times (once per yield), not just once.

## Context Model

### The Problem: Concurrent Data Flows Need Isolation

When a source yields 3 items and downstream ops run concurrently, they'd overwrite each other's state without isolation:

```
source yields "hello" -> process.input = "hello"
source yields "world" -> process.input = "world"  // overwrites before process reads "hello"!
```

### Solution: Each Yield Creates a Context

Each yield gets a monotonic context_id. The context propagates through the entire downstream chain.

```
context_id = f"{parent_context}.{yield_index}"
```

**Single source chain:**
```
source >> process >> format >> END

source yield 0 -> ctx="0" -> process[0] -> format[0]  // independent flow
source yield 1 -> ctx="1" -> process[1] -> format[1]  // independent flow
source yield 2 -> ctx="2" -> process[2] -> format[2]  // independent flow
```

Each context has its own state slots. `process[0]` reads/writes in context "0", `process[1]` in context "1". No interference.

**Fork (one source, two branches, then join):**
```
source >> transform_a >> merge
source >> transform_b >> merge

source yield 0 -> ctx="0"
  -> transform_a[0] completes -> merge gets ctx="0" from edge a->merge
  -> transform_b[0] completes -> merge gets ctx="0" from edge b->merge
  -> merge[0] fires when BOTH a[0] and b[0] complete (ready_count for ctx "0" hits 0)
```

Context propagates through the fork — both branches inherit the same context from the source yield. The join sees matching contexts and knows which items belong together.

**Nested contexts (streaming inside a ForOp iteration):**
```
ForOp iteration 2, inner streaming source yield 1:
  context_id = "iter_2.1"
```

## Ready Count: Per-Context

Today: `ready_count: dict[op_name, int]` — one count per op.

Streaming: `ready_count: dict[context_id, dict[op_name, int]]` — one count per op **per context**.

```
# Source yields with ctx="0"
ready_count["0"]["process"] -= 1   # decrement process in context 0
if ready_count["0"]["process"] == 0:
    schedule process with context_id="0"

# Source yields with ctx="1"  (independent of ctx="0")
ready_count["1"]["process"] -= 1
if ready_count["1"]["process"] == 0:
    schedule process with context_id="1"
```

Each context starts with a fresh copy of `initial_ready_count`. Contexts are fully independent.

## Join Rules

### Two streaming sources — zip by index

```
source_a (yields 3) >> merge
source_b (yields 2) >> merge
```

Both sources produce context_ids independently: 0, 1, 2...

- `source_a` yields ctx="0" -> `ready_count["0"]["merge"]` -= 1 (now 1, needs source_b too)
- `source_b` yields ctx="0" -> `ready_count["0"]["merge"]` -= 1 (now 0, merge[0] fires!)
- `source_a` yields ctx="1" -> `ready_count["1"]["merge"]` -= 1
- `source_b` yields ctx="1" -> `ready_count["1"]["merge"]` -= 1 (merge[1] fires!)
- `source_a` yields ctx="2" -> `ready_count["2"]["merge"]` -= 1 (source_b never yields ctx="2", merge[2] never fires)

This is natural zip semantics — no special handling needed, just per-context ready_count.

### Fan-in: streaming + batch

```
source (yields 3) >> process
config (returns once) >> process
```

- `config` completes once in the parent context (no streaming context)
- Its ready_count contribution is "permanently satisfied" for all future contexts
- When `source` yields ctx="0", `ready_count["0"]["process"]` starts at 1 (only source edge counts), because config's edge is already satisfied
- `process` fires 3 times, each time reading:
  - `source["item"]` from its streaming context (per-yield value)
  - `config["settings"]` from the parent context (broadcast value, always available)

**Implementation:** when creating `ready_count["0"]` for a new streaming context, pre-decrement edges from ops that have already completed (batch ops). Only streaming edges still need to fire.

## State Model

- **No new cell types.** Regular Cell with overwrite semantics, same as today.
- State already supports context_id: `state[op_name, var_name, context_id]`
- Batch ops write to parent context -> readable by all child contexts (Cell already falls back to parent context on read)
- Streaming ops write to their specific context_id
- `get_inputs()` reads from the op's context_id, falls back to parent context for broadcast values — **this already works** via Cell's existing context fallback

## What Doesn't Change

- `BaseOp.run()` — completely unchanged, no `_run_stream()`
- `BaseOp.get_inputs()` — unchanged (already reads by context_id)
- `BaseOp.store_result()` — unchanged (already writes by context_id)
- Edge operators (`>>`, `>>~`, `>`) — unchanged
- Op definition (`@op`, `FuncOp`, etc.) — unchanged (except auto-detect generators)
- `StateSchema` — unchanged
- `MemoryState.__getitem__` / `__setitem__` — unchanged (already supports context_id)
- All existing tests — must pass without modification

## What Changes

1. **GraphOp.run() scheduler** (`graph_op.py:761-834`):
   - Detect generator ops (`inspect.isgeneratorfunction(op.core)`)
   - For generators: iterate per-yield, each yield -> `store_result()` + `activate_successors()` with new context_id
   - `ready_count` becomes `dict[context_id, dict[op_name, int]]`
   - New context creation: copy `initial_ready_count`, pre-decrement already-completed batch edges
   - Termination: graph completes when all generators exhausted AND all active_tasks done

2. **@op decorator** (`func_op.py`):
   - Auto-detect generators via `inspect.isgeneratorfunction` / `inspect.isasyncgenfunction`
   - Set a flag so the scheduler knows to iterate per-yield
   - Also handle `ast.Yield` in `extract_return_schema()` for output key detection

3. **Cell context fallback** (if not already working):
   - When reading `state[op_name, var, ctx="0"]` and no value in ctx="0", fall back to parent context
   - This enables broadcast: batch op writes to parent context, streaming consumers read from child context but get the parent value

## Open Questions

- **END collection:** How does END collect results from multiple contexts? Options: aggregate into list, return last, user-specified reducer
- **Queue semantics:** Should there be a way to opt into queue/keep-all semantics? Or is "latest value" always enough?
- **Error handling:** If one context fails (e.g., process[1] throws), should other contexts (process[0], process[2]) continue?
- **Memory/GC:** When can we clean up a completed context's state? After all downstream ops in that context finish?
- **Backpressure:** If source yields faster than consumers can process, should we buffer unlimited or cap?
- **Multiple streaming inputs:** If an op reads from two different streaming sources with different rates, how do contexts align? (Current answer: zip by index)
