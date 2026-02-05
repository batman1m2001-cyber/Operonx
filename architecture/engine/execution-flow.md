# Workflow Execution Flow

## Overview

`Hush` là execution engine chính, điều phối việc thực thi workflows.

Location: `hush-core/hush/core/engine.py`

## Hush Class

```python
class Hush:
    __slots__ = ["graph", "name", "_schema"]

    def __init__(self, graph: GraphNode):
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
# Create fresh state cho mỗi run
state = self._schema.create_state(
    inputs=inputs,
    user_id=user_id,
    session_id=session_id,
    request_id=request_id,
    trace_store=trace_store,  # Optional SQLite store
)
```

### 4. Graph Execution

```python
result = await self.graph.run(state)
```

GraphNode.run() thực thi tất cả child nodes theo dependency order.

### 5. Cleanup

```python
# End streams
await STREAM_SERVICE.end_request(request_id, session_id)

# Flush traces (fire-and-forget, non-blocking)
if tracer:
    tracer.flush_in_background(self.name, state)

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
│                          ↓                                  │
│  2. Create TraceStore (if tracer provided)                  │
│                          ↓                                  │
│  3. Create MemoryState from schema                          │
│                          ↓                                  │
│  4. graph.run(state) ─────────────────────────┐             │
│                                               │             │
│     ┌─────────────────────────────────────────┴───────┐     │
│     │              GraphNode.run()                    │     │
│     ├─────────────────────────────────────────────────┤     │
│     │  • Start entry nodes                            │     │
│     │  • Wait for task completion                     │     │
│     │  • Schedule successor nodes                     │     │
│     │  • Repeat until all complete                    │     │
│     └─────────────────────────────────────────────────┘     │
│                          ↓                                  │
│  5. End streams                                             │
│                          ↓                                  │
│  6. Flush traces (background)                               │
│                          ↓                                  │
│  7. Return result + $state                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

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
# Nodes: ['a', 'b', 'c']
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
from hush.observability import LangfuseTracer

tracer = LangfuseTracer()
result = await engine.run(inputs, tracer=tracer)

# Traces được flush non-blocking sau khi run hoàn thành
```
