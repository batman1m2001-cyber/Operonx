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
    def __init__(self, config=None, resource=None, tags=None):
        super().__init__(tags=tags)
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
        """Called by FlushWorker in a background thread."""
        client = self._get_client()
        structure_map = {s["op_name"]: s for s in trace_data.get("graph_structure", [])}
        for record in trace_data.get("records", []):
            # Create traces/spans using client
            pass
        client.flush()
```

### 3. Register Plugin + Export

## Flushing

Tracers are called by `FlushWorker` (from `hush.core.tracing`) in a background thread pool.
The main async thread is never blocked.

### trace_data Structure

```python
{
    "request_id": "uuid-...",
    "workflow_name": "my_workflow",
    "user_id": "...",
    "session_id": "...",
    "tags": ["prod", "cache-hit"],       # Merged: static + dynamic
    "graph_structure": [                  # Static metadata from compiled graph
        {"op_name": "root", "op_type": "graph", "parent_name": None, "contain_generation": False},
        {"op_name": "root.llm", "op_type": "llm", "parent_name": "root", "contain_generation": True},
    ],
    "records": [                          # Dynamic execution data from state
        {"op_name": "root", "context_id": None, "inputs": {}, "outputs": {},
         "start_time": "...", "end_time": "...", "duration_ms": 100.0,
         "model": None, "usage": None, "cost": None},
    ],
}
```

## Testing

Tests use `sample_trace_data` fixtures with `graph_structure` + `records` format.
Tracer tests mock the backend client:

```python
def test_tracer_flush(sample_trace_data):
    with patch("hush.telemetry.tracers.langfuse.LangfuseClient") as mock_cls:
        tracer = LangfuseTracer(config=LangfuseConfig(...))
        tracer.flush(sample_trace_data)
        mock_cls.return_value.trace.assert_called_once()
```

## Feature Flags

- `[langfuse]` - Langfuse SDK
- `[otel]` - OpenTelemetry SDK + exporters
- `[all]` - Everything
