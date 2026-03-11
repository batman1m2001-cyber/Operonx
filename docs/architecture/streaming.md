# Streaming Architecture

## Overview

Hush supports streaming via generator ops that yield items one at a time, creating stream contexts for downstream processing. This enables real-time token-by-token LLM output and incremental data processing.

## Stream Model

```
Generator op (yields N items)
    │
    ├── yield item 0 → create context "[0]" → run downstream ops in "[0]"
    ├── yield item 1 → create context "[1]" → run downstream ops in "[1]"
    └── yield item N → create context "[N]" → run downstream ops in "[N]"
```

### Generator Ops

Any op that returns an async generator (or is marked as a generator) produces stream items:

```python
@op
async def chunk_text(text: str):
    for chunk in text.split("\n"):
        yield {"chunk": chunk}
```

### Stream Contexts

Each yielded item creates a new execution context. Downstream ops run independently in each context:

```
main context:  [input] → [chunker] ─┬─ [0] → [process] → [output]
                                     ├─ [1] → [process] → [output]
                                     └─ [2] → [process] → [output]
```

### Scheduler Events

The async event-queue scheduler uses these events for streaming:

| Event | Meaning |
|-------|---------|
| `Done(op, ctx)` | Op completed in context — propagate to successors |
| `DonePending(op, ctx)` | Op returned PENDING — no propagation |
| `Yield(gen, stream_ctx, data)` | Generator yielded — create stream context, run downstream |
| `Exhausted(gen)` | Generator done — decrement active count |

### Stream Predecrements

When a generator yields into a new stream context, batch predecessors that are already done need their edges pre-subtracted from the fresh `ready_counts`. This ensures downstream ops become ready immediately after the generator→successor edge fires.

## Python vs Rust

| Aspect | Python | Rust |
|--------|--------|------|
| Generator type | `async def` + `yield` | `Vec<Value>` iterated in `tokio::spawn` |
| Stream context | Tuple-based `("main", "[0]")` | Dot-separated `"main.[0]"` |
| Event queue | `asyncio.Queue` | `tokio::mpsc` channel |
| Context fallback | Walk up tuple | Walk up dot-separated string |

## PENDING Sentinel

Ops can return `{"__pending__": true}` to absorb input without triggering downstream propagation. The scheduler emits `DonePending` instead of `Done`. This is useful for accumulator patterns where an op collects stream items before producing output.

## Engine Streaming API

```python
engine = Hush(graph)

# Stream mode — yields events as they arrive
async for event in engine.stream(inputs={"text": "..."}):
    if event["type"] == "token":
        print(event["data"], end="")
    elif event["type"] == "done":
        result = event["data"]
```

The `stream()` method uses an `asyncio.Queue` (set via context var `_output_queue`) to collect events from generator ops in real-time.
