# hush-telemetry

External tracing backend integrations for Hush workflows. Supports Langfuse, OpenTelemetry, and more.

## Module Structure

```
hush/telemetry/
├── __init__.py         # Package exports, auto-registers plugin
├── plugin.py           # ObservabilityPlugin for ResourceHub
├── backends/           # Backend clients (config + client)
│   ├── langfuse/
│   │   ├── config.py   # LangfuseConfig
│   │   └── client.py   # LangfuseClient
│   └── otel/
│       ├── config.py   # OTELConfig
│       └── client.py   # OTELClient
└── tracers/            # Tracer implementations
    ├── hush_eyes.py    # HushEyesTracer — HTTP POST to ui-hush-eyes server
    ├── langfuse.py     # LangfuseTracer
    └── otel.py         # OTELTracer
```

## Key Concepts

### Backends vs Tracers

- **Backend**: Low-level client for interacting with external service (LangfuseClient, OTELClient)
- **Tracer**: High-level integration with Hush engine — inherits from `Tracer` (`hush.core.tracing`) and implements `flush(trace_data)`

## Usage

### With ResourceHub (Production)
```python
from hush.telemetry import LangfuseTracer

tracer = LangfuseTracer(resource="langfuse:default", tags=["prod"])
result = await engine.run(inputs={...}, tracer=tracer)
```

### With Direct Config (Simple)
```python
from hush.telemetry import LangfuseTracer, LangfuseConfig

config = LangfuseConfig.from_env()
tracer = LangfuseTracer(config=config)
result = await engine.run(inputs={...}, tracer=tracer)
```

### HushEyesTracer (ui-hush-eyes)
```python
from hush.telemetry import HushEyesTracer

tracer = HushEyesTracer(tags=["dev"])
result = await engine.run(inputs={...}, tracer=tracer)
# Open http://localhost:8420 to view traces
```

### Multiple Tracers
```python
from hush.telemetry import HushEyesTracer, LangfuseTracer

result = await engine.run(
    inputs={...},
    tracer=[HushEyesTracer(), LangfuseTracer(resource="langfuse:default")],
)
```

## Adding a New Tracing Backend

### 1. Create Backend (config + client)

### 2. Create Tracer

```python
from hush.core.tracing import Tracer

class MyBackendTracer(Tracer):
    def __init__(self, config=None, resource=None, tags=None, stream_trace_limit=100):
        super().__init__(tags=tags, stream_trace_limit=stream_trace_limit)
        if config is None and resource is None:
            raise ValueError("Must provide either 'config' or 'resource'")
        self._config = config
        self._resource = resource

    def _get_client(self):
        if self._config is not None:
            return MyBackendClient(self._config)
        from hush.core.registry import get_hub
        return get_hub().mybackend(self._resource)

    def flush(self, trace_data: dict) -> None:
        """Called by FlushWorker in a background thread.

        trace_data contains a pre-computed 'nodes' list (TraceNode dicts).
        Each node has parent_trace_key for simple parent lookup — no heuristics needed.
        """
        client = self._get_client()
        objects = {}  # trace_key -> created object

        for node in trace_data["nodes"]:
            key = node["trace_key"]
            parent_key = node["parent_trace_key"]
            parent = objects.get(parent_key)

            if node["node_type"] == "trace":
                obj = client.create_trace(name=node["display_name"], ...)
            elif node["node_type"] == "generation":
                obj = parent.generation(name=node["display_name"], model=node.get("model"), ...)
            else:
                obj = parent.span(name=node["display_name"], ...)

            objects[key] = obj

        client.flush()
```

### 3. Register Plugin + Export

## Flushing

Tracers are called by `FlushWorker` (from `hush.core.tracing`) in a background thread pool.
The main async thread is never blocked.

### trace_data Structure (nodes format)

`TraceCollector.collect_tree()` produces a pre-computed tree of `TraceNode` dicts. Each node has a `parent_trace_key` that tracers use for simple parent lookup — no heuristics needed.

```python
{
    "request_id": "uuid-...",
    "workflow_name": "my_workflow",
    "user_id": "...",
    "session_id": "...",
    "tags": ["prod", "cache-hit"],       # Merged: static + dynamic
    "nodes": [                           # Pre-computed TraceNode tree (topological order)
        {
            "trace_key": "root",                # Unique key for this node
            "parent_trace_key": None,           # None = root trace
            "op_name": "root",
            "display_name": "my_workflow",       # Short name for UI
            "node_type": "trace",               # "trace" | "span" | "generation"
            "kind": "graph",                    # "batch"|"generator"|"stream_context"|"stream_item"|"loop_iter"|"graph"
            "inputs": {...},
            "outputs": {...},
            "start_time": "2024-01-15T10:00:00Z",
            "end_time": "2024-01-15T10:00:01Z",
            "duration_ms": 1000.0,
            "metadata": {},
        },
        {
            "trace_key": "root.llm-node",
            "parent_trace_key": "root",         # Parent = root trace
            "op_name": "root.llm-node",
            "display_name": "llm-node",
            "node_type": "generation",          # LLM ops → generation
            "kind": "batch",
            "inputs": {"prompt": "..."},
            "outputs": {"completion": "..."},
            "model": "gpt-4",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "cost": 0.0015,
        },
        # Streaming: synthetic context nodes group downstream ops per yield
        {
            "trace_key": "$ctx:root:main.s0",   # Synthetic grouping node
            "parent_trace_key": "root",
            "op_name": None,
            "display_name": "[0]",              # Stream context label
            "node_type": "span",
            "kind": "stream_context",
        },
        {
            "trace_key": "root.process:main.s0",
            "parent_trace_key": "$ctx:root:main.s0",  # Child of stream context
            "display_name": "process",
            "node_type": "span",
            "kind": "stream_item",
            "metadata": {"spawned_by": "root.generator"},
        },
    ],
}
```

### Node Types and Kinds

| node_type | When |
|-----------|------|
| `"trace"` | Root GraphOp (becomes Langfuse trace) |
| `"generation"` | LLM ops with `contain_generation=True` |
| `"span"` | Everything else |

| kind | When |
|------|------|
| `"batch"` | Normal op (runs once) |
| `"generator"` | Generator op summary (yield_count in metadata) |
| `"stream_context"` | Synthetic `[N]` grouping span for stream yields |
| `"stream_item"` | Downstream op triggered by a yield |
| `"loop_iter"` | Loop iteration grouping span |
| `"graph"` | Nested GraphOp |

### Stream Sampling

`FlushWorker` applies `stream_trace_limit` (default 100) per tracer before calling `flush()`. This caps the number of `stream_item` nodes per generator and removes orphaned `stream_context` nodes.

```python
tracer = LangfuseTracer(resource="langfuse:default", stream_trace_limit=50)
# Only first 50 stream items per generator will be traced
```

## Testing

Tests use `sample_trace_data` fixtures with `nodes` format.
Tracer tests mock the backend client:

```python
def test_tracer_flush(sample_trace_data):
    tracer = LangfuseTracer(config=LangfuseConfig(...))
    with patch(...):  # mock Langfuse client
        tracer.flush(sample_trace_data)
        # Verify: trace created for root, span for children, generation for LLM
```

## Feature Flags

- `[langfuse]` - Langfuse SDK
- `[otel]` - OpenTelemetry SDK + exporters
- `[all]` - Everything
