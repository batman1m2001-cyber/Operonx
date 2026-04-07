# Streaming Architecture

## Overview

Hush supports streaming via generator ops that yield items one at a time,
creating independent stream contexts for downstream processing. This enables
real-time token-by-token LLM output and incremental data processing.

## Stream Model

```
Generator op (yields N items)
    │
    ├── yield item 0 → create context "[0]" → run downstream ops in "[0]"
    ├── yield item 1 → create context "[1]" → run downstream ops in "[1]"
    └── yield item N → create context "[N]" → run downstream ops in "[N]"
```

### Generator Ops

Any op that uses `yield` produces stream items:

```python
@op
async def chunk_text(text: str):
    for chunk in text.split("\n"):
        yield {"chunk": chunk}
```

### Stream Contexts

Each yielded item creates a new execution context. Downstream ops run
independently in each context:

```
main context:  [input] → [chunker] ─┬─ [0] → [process] → [output]
                                     ├─ [1] → [process] → [output]
                                     └─ [2] → [process] → [output]
```

---

## Scheduler Events: Frame and EOF

The scheduler uses two event types — both are internal. **User code never
constructs or yields them.**

| Event | Created by | Meaning |
|-------|-----------|---------|
| `Frame(op, ctx, result)` | `Scheduler._pump()` — one per `op.run()` yield | Op produced a result in this context |
| `EOF(op, ctx)` | `Scheduler._pump()` — after `op.run()` exhausts | Op is finished in this context |

### The three-layer model

```
Layer 1 — user @op function
    return {"k": v}        # normal op  → one Frame
    yield {"k": v}         # generator  → N Frames
    (never writes Frame or EOF)

Layer 2 — BaseOp.run()   (async generator)
    async for item_ctx, result in _exec_core(inputs):
        yield item_ctx, result          # uniform interface for scheduler

Layer 3 — Scheduler._pump()   (consumes the async generator)
    async for item_ctx, result in op.run(state, ctx):
        queue.put_nowait(Frame(op, item_ctx, result))
    queue.put_nowait(EOF(op, ctx))      # generator exhausted → EOF emitted
```

The key insight: **everything is a stream**. A normal op is just a generator
that yields once. The scheduler has a single code path — no `is_gen` check
anywhere.

### Scheduler loop

```python
while inflight > 0:
    event = await queue.get()
    inflight -= 1
    match event:
        case Frame(): _on_frame(event)   # seed item ctx, decrement ready, route downstream
        case EOF():   _on_eof(event)     # flush collect buffer, advance seq queue, check loop
```

---

## Stream Policies

Stream policies control how downstream ops consume generator output. They are
**var-level** attributes on `Ref`, not edge-level — one edge `gen → process`
can mix policies per variable:

```python
step = process(
    value=gen["value"].parallel(max=5),   # parallel: run downstream concurrently
    scores=gen["score"].collect(),         # collect: buffer all yields, dispatch once
)
```

| Policy | Behaviour |
|--------|-----------|
| *(default)* | Sequential — downstream dispatched once per yield |
| `.parallel(max=N)` | Downstream runs concurrently for each item (up to N at once) |
| `.collect()` | Buffer all yields; dispatch downstream once with the full list |

Stream policies are pre-computed by `StateSchema._build()` into
`_stream_policies: Dict[(dst_op, var_name), StreamPolicy]` for O(1) lookup at
runtime.

### `_stream_initial_ready`

When a generator emits `Frame[0]` in a new stream context, downstream ops need
accurate ready counts. These are pre-computed at build time as
`_stream_initial_ready[gen_name][op_name]` — the initial ready count for
`op_name` when `gen_name` starts streaming. Batch predecessors that are
guaranteed to have completed before the first frame are subtracted from the
count.

---

## Engine Streaming API

`engine.start()` returns an `ExecutionHandle` immediately. The graph runs in
the background; use the handle to stream frames, await specific outputs, or
collect the final result.

```python
engine = Hush(graph)

# Stream frames as they arrive (token-by-token for LLM ops)
handle = engine.start(inputs={"text": "..."})
async for op, ctx, data in handle:
    if op == "llm":
        print(data.get("content", ""), end="", flush=True)

# Await a specific output
answer = await handle["llm", "content"]

# Collect all outputs into a single dict (last-value-wins per key)
result = await handle.collect()
```

`engine.run()` is a thin wrapper over `start().collect()`:

```python
# Equivalent
result = await engine.run(inputs={"text": "..."})
result = await engine.start(inputs={"text": "..."}).collect()
```

---

## Python vs Rust

| Aspect | Python | Rust |
|--------|--------|------|
| Generator type | `async def` + `yield` | `Vec<Value>` iterated in `tokio::spawn` |
| Stream context | Tuple-based `("main", "[0]")` | Dot-separated `"main.[0]"` |
| Event queue | `asyncio.Queue` + Frame/EOF dataclasses | `tokio::mpsc` channel |
| Context fallback | Cell hierarchy walk (tuple prefix) | Walk up dot-separated string |

---

## PENDING Sentinel

Ops can return `{"__pending__": true}` to absorb input without triggering
downstream propagation. The scheduler suppresses Frame routing for that result.
Useful for accumulator patterns where an op collects stream items before
producing output.
