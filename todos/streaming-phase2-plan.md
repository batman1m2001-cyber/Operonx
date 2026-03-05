# Streaming Phase 2 — Iteration Op Refactor

## Context

Phase 1 introduces a streaming scheduler that handles per-yield forwarding, per-context ready_count, broadcast via stream depth, and backpressure. Phase 1 also migrated context IDs from strings to tuples — ForOp/MapOp/AIterOp/WhileOp already use tuple contexts. This is **the same machinery ForOp/MapOp/AIterOp implement internally** (~200 lines each + own mini-schedulers). Phase 2 removes the duplication.

## What Gets Removed

### ForOp → Generator Op

```python
# BEFORE: ForOp (~200 lines, custom _execute, manual context management)
with ForOp(name="loop", inputs={"value": Each([1,2,3]), "config": Broadcast(cfg)}) as loop:
    node = double(value=PARENT["value"], config=PARENT["config"])
    START >> node >> END

# AFTER: generator op (3 lines) + streaming scheduler
@op
def for_each(items: list):
    for item in items:
        yield {"value": item}

with GraphOp(name="workflow") as g:
    source = for_each(items=PARENT["items"])
    cfg = get_config()
    node = double(value=source["value"], config=cfg["config"])
    START >> source >> node >> END
    START >> cfg >> node  # cfg is depth 0, read via stream depth — no Broadcast needed
```

### MapOp → Generator Op (same as ForOp)

MapOp is ForOp but concurrent. The streaming scheduler is concurrent by default (semaphore > 1), so MapOp and ForOp become the same thing:

```python
# BEFORE: MapOp (~150 lines, asyncio.gather, semaphore)
with MapOp.of(url=Each(urls), max_concurrency=10) as loop:
    node = fetch(url=PARENT["url"])
    START >> node >> END

# AFTER: same generator, scheduler handles concurrency
@op
def for_each(items: list):
    for item in items:
        yield {"url": item}

# max_concurrent=10 on GraphOp controls concurrency
# max_concurrent=1 for sequential (old ForOp behavior)
```

### AIterOp → Async Generator + Composition

AIterOp's "special features" are just composable ops:

```python
# BEFORE: AIterOp (~200 lines, batching, callbacks, ordered output)
with AIterOp.of(item=Each(events()), callback=handle, batch_fn=batch_by_size(10)) as stream:
    step = process(item=PARENT["item"])
    START >> step >> END

# AFTER: compose separate concerns as ops

# Batching = a buffering generator
@op
async def batched(source, size: int = 10):
    buffer = []
    async for item in source:
        buffer.append(item)
        if len(buffer) >= size:
            yield {"batch": buffer.copy()}
            buffer.clear()
    if buffer:
        yield {"batch": buffer}

# Callback = a regular op at the end
@op
async def notify(result):
    await handle(result)
    return {"result": result}

# Compose in graph
source >> process >> notify >> END
```

### Feature Mapping

| Iteration Op Feature | Streaming Equivalent |
|---------------------|---------------------|
| `Each(items)` | `yield {"key": item}` in generator |
| `Broadcast(value)` | Regular op upstream (stream depth resolves) |
| `parallel=True` / `max_concurrency=N` | `max_concurrent=N` on GraphOp (default: concurrent) |
| `parallel=False` | `max_concurrent=1` |
| `fail_fast=True` | Scheduler `error_handling="fail"` (default) |
| `fail_fast=False` / skip errors | Scheduler `error_handling="skip"` |
| Column-oriented result | Scheduler result collection |
| Ordered output (AIterOp) | Streaming contexts are ordered by yield index |
| Batching (AIterOp) | Buffering generator op |
| Callbacks (AIterOp) | Regular op at end of chain |
| Context isolation | Per-yield context (tuple-based, already migrated in Phase 1) |

## What Stays

### WhileOp — Keep As-Is

WhileOp is fundamentally different from streaming. It's a **feedback loop**: downstream output becomes the next iteration's input. This is a cycle, not a stream.

```python
# Agent loop: messages → LLM → parse → execute → update → messages (repeat)
with WhileOp(name="agent", until="done == True") as loop:
    llm = LLMOp.of(resource="gpt-4o", messages=PARENT["messages"])
    parse = extract_action(response=llm["content"])
    execute = run_tool(action=parse["action"])
    update = append_messages(messages=PARENT["messages"], tool_result=execute["result"])

    update["new_messages"] >> PARENT["messages"]  # feedback edge
    parse["done"] >> PARENT["done"]
    START >> llm >> parse >> execute >> update >> END
```

A generator can't replace this because it has no way to receive downstream results back. The generator yields OUT but can't read what downstream ops produced (results are in state, generator is a plain Python function).

WhileOp stays in `hush-core/hush/core/ops/iteration/while_op.py` unchanged.

## What to Build

### 1. Scheduler error_handling parameter

Add `error_handling` to GraphOp (default `"fail"`):
- `"fail"`: any streaming context error fails the entire graph (current behavior)
- `"skip"`: failed contexts are excluded from result collection, graph continues

### 2. Deprecation warnings

Add `DeprecationWarning` to ForOp, MapOp, AIterOp constructors pointing to generator equivalents.

### 3. Update BaseIterationOp

After removing ForOp/MapOp/AIterOp, BaseIterationOp only serves WhileOp. Simplify or inline its code into WhileOp.

### 4. Update documentation & examples

Tutorial docs and examples that use ForOp/MapOp/AIterOp → generator patterns.

## Migration Path

```
Phase 1: Ship streaming scheduler
    ↓
Phase 2a: Deprecate ForOp, MapOp, AIterOp
    - Add deprecation warnings
    - Add error_handling="skip" to scheduler
    - Update tutorial/examples to use generators
    ↓
Phase 2b: Remove ForOp, MapOp, AIterOp
    - Remove from iteration/ module
    ↓
Phase 3: Remove WhileOp → GraphOp.loop()
    - Add loop() classmethod to GraphOp
    - Add @graph.loop() decorator
    - Deprecate then remove WhileOp
    - Delete entire iteration/ module
    ↓
Final state:
    iteration/ deleted entirely
    GraphOp handles everything:
      - Regular graph     → GraphOp(name="workflow")
      - Streaming/fan-out → generator ops inside GraphOp
      - Feedback loops    → GraphOp.loop(until="...")
```

## Files to Modify

### Phase 2a (deprecation)

| File | Change |
|------|--------|
| `hush-core/hush/core/ops/iteration/for_op.py` | Add deprecation warning |
| `hush-core/hush/core/ops/iteration/map_op.py` | Add deprecation warning |
| `hush-core/hush/core/ops/iteration/aiter_op.py` | Add deprecation warning |
| `hush-core/hush/core/ops/graph/graph_op.py` | Add `error_handling` parameter |
| `tutorial/docs/05-loops-branches.md` | Update with generator patterns |
| `tutorial/examples/05-*` | Update examples |

### Phase 2b (removal)

| File | Change |
|------|--------|
| `hush-core/hush/core/ops/iteration/for_op.py` | Delete |
| `hush-core/hush/core/ops/iteration/map_op.py` | Delete |
| `hush-core/hush/core/ops/iteration/aiter_op.py` | Delete |
| `hush-core/hush/core/ops/iteration/base.py` | Keep (WhileOp still uses it) |
| `hush-core/hush/core/ops/iteration/__init__.py` | Remove ForOp, MapOp, AIterOp exports |
| `hush-core/tests/ops/iteration/` | Remove ForOp/MapOp/AIterOp tests, add generator equivalents |
