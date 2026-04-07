# Workflow Execution Flow

## Overview

`Hush` là execution engine chính, điều phối việc thực thi workflows.

Location: `hush-icore/hush/core/engine.py`

## Hush Class

```python
class Hush:
    __slots__ = ["graph", "name", "_schema"]

    def __init__(self, graph: GraphOp):
        self.graph = graph
        self.name = graph.name

        # Build graph và tạo schema
        self.graph.build()
        self._schema = StateSchema(self.graph)
```

## Execution Phases

### 1. Initialization

```python
engine = Hush(graph)
```

- Build graph structure
- Create StateSchema từ graph
- Validate graph (entries, exits, edges)
- Background process is **not** started here (lazy — only spawned on first traced request)

### 2. Run Request

```python
result = await engine.run(
    inputs={"query": "hello"},
    user_id="user_123",
    session_id="session_456",
    request_id="req_789",
    tracer=my_tracer
)
```

### 3. State Creation

```python
# Tạo fresh state cho mỗi run
state = self._schema.create_state(
    inputs=inputs,
    user_id=user_id,
    session_id=session_id,
    request_id=request_id,
)
```

### 4. Graph Execution

```python
# GraphOp.run() is an async generator — consumed by engine.start()
# engine.run() is a thin wrapper: await engine.start(inputs).collect()
handle = engine.start(inputs=inputs, ...)
result = await handle.collect()
```

`GraphOp.run()` is an async generator that yields `(ctx, result)` per frame.
The scheduler drives it via Frame/EOF events on an asyncio queue.

### 5. Cleanup

```python
# Collect + flush traces in background thread (non-blocking)
if tracers:
    from hush.core.tracing import get_flush_worker
    get_flush_worker().submit(tracers, self.graph, state)

# Include state in result
result["$state"] = state
```

## Execution Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        Hush.run()                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Generate IDs (user_id, session_id, request_id)          │
│                          |                                  │
│  2. Create MemoryState from schema                          │
│                          |                                  │
│  3. engine.start() ───────────────────────────┐             │
│                                               │             │
│     ┌─────────────────────────────────────────┴───────┐     │
│     │         Scheduler (Frame/EOF queue)             │     │
│     ├─────────────────────────────────────────────────┤     │
│     │  * dispatch() entry ops → _pump() tasks         │     │
│     │  * Frame event → _on_frame() route downstream   │     │
│     │  * EOF event  → _on_eof() flush/loop/advance    │     │
│     │  * Loop until inflight == 0                     │     │
│     └─────────────────────────────────────────────────┘     │
│                          |                                  │
│  4. End streams                                             │
│                          |                                  │
│  5. Flush traces (background via FlushWorker)               │
│                          |                                  │
│  6. Return result + $state                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Rust Mode (hush-icore)

When `backend="rust"` is used via `engine.serve()`, execution bypasses the Python asyncio scheduler entirely and runs in a compiled Rust engine (hush-serve):

```python
engine = Hush(graph)
engine.serve(port=8000, backend="rust", rust_ops="rust_ops")
```

### Rust Execution Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     Hush.run() (Rust)                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. graph.serialize() → config dict (Python side)            │
│                          |                                   │
│  2. Hush(config) → GraphConfig::from_dict() (Rust side)      │
│                          |                                   │
│  3. Store inputs into EngineState (DashMap)                   │
│                          |                                   │
│  4. run_graph() ─────────────────────────────┐               │
│                                              │               │
│     ┌────────────────────────────────────────┴──────┐        │
│     │          Batch-Aware Scheduler                 │        │
│     ├────────────────────────────────────────────────┤        │
│     │  * Drain all ready ops into batch              │        │
│     │  * If batch has rust_op → parallel (rayon)     │        │
│     │  * Else → sequential (one at a time)           │        │
│     │  * Activate successors after batch completes   │        │
│     │  * Repeat until queue empty                    │        │
│     └────────────────────────────────────────────────┘        │
│                          |                                   │
│  5. get_outputs() → result dict                          │
│                          |                                   │
│  6. Attach $state metadata (tags, values)                    │
│                          |                                   │
│  7. Return result to Python                                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Key Differences from Python Mode

| Aspect | Python Mode | Rust Mode |
|--------|------------|-----------|
| Scheduler | asyncio event loop | Synchronous queue + rayon |
| Parallelism | asyncio.wait(FIRST_COMPLETED) | Batch parallel via rayon (when beneficial) |
| State | MemoryState (Python dict) | EngineState (DashMap, concurrent) |
| Op execution | Async dispatch | Direct function call |
| Performance | Baseline | 2-6x faster |

For detailed Rust architecture, see `hush-icore/CLAUDE.md`.

## Multiple Runs

Engine có thể run nhiều lần với fresh state:

```python
engine = Hush(graph)

# Each run creates new state
result1 = await engine.run({"query": "first"})
result2 = await engine.run({"query": "second"})
# state1 và state2 độc lập
```

## Callable Syntax

```python
# Equivalent ways to run
result = await engine.run({"query": "hello"})
result = await engine({"query": "hello"})
```

## Debug

```python
engine.show()

# Output:
# === Hush Engine: my_workflow ===
# Graph: my_workflow
# Ops: ['a', 'b', 'c']
# Edges:
#   a -> b: normal
#   b -> c: normal
# Ready count: {'a': 0, 'b': 1, 'c': 1}
#
# === StateSchema: my_workflow ===
# my_workflow.a.input [0] <- pull my_workflow.input[1]
# ...
```

## Result Format

```python
result = await engine.run(inputs)

# result contains:
{
    "output_var_1": ...,
    "output_var_2": ...,
    "$state": MemoryState  # For debugging/tracing
}
```

## Tracing Integration

```python
from hush.telemetry import LangfuseTracer

tracer = LangfuseTracer()
engine = Hush(graph, tracer=tracer)
result = await engine.run(inputs)

# Traces được flush non-blocking sau khi run hoàn thành
```
