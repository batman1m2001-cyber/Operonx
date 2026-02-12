# hush-ops

External tracing backend integrations for Hush workflows. Supports Langfuse, OpenTelemetry, and more.

## Module Structure

```
hush/ops/
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
    ├── langfuse.py     # LangfuseTracer
    └── otel.py         # OTELTracer
```

## Key Concepts

### Backends vs Tracers

- **Backend**: Low-level client for interacting with external service (LangfuseClient, OTELClient)
- **Tracer**: High-level integration with Hush engine (LangfuseTracer, OTELTracer)

Tracers use backends internally but provide the `BaseTracer` interface expected by the Hush engine.

## Usage

### With ResourceHub (Production)
```python
from hush.ops import LangfuseTracer

# Config in resources.yaml:
# observability:
#   langfuse:
#     default:
#       public_key: ${LANGFUSE_PUBLIC_KEY}
#       secret_key: ${LANGFUSE_SECRET_KEY}
#       host: https://cloud.langfuse.com

tracer = LangfuseTracer(resource_key="langfuse:default", tags=["prod"])
result = await engine.run(inputs={...}, tracer=tracer)
```

### With Direct Config (Simple)
```python
from hush.ops import LangfuseTracer, LangfuseConfig

config = LangfuseConfig.from_env()  # Reads LANGFUSE_* env vars
tracer = LangfuseTracer(config=config)
```

## Adding a New Tracing Backend

### 1. Create Backend (config + client)

```
backends/mybackend/
├── __init__.py
├── config.py
└── client.py
```

**config.py:**
```python
from pydantic import BaseModel

class MyBackendConfig(BaseModel):
    api_key: str
    endpoint: str
    # other settings
```

**client.py:**
```python
from hush.ops.backends.mybackend.config import MyBackendConfig

class MyBackendClient:
    def __init__(self, config: MyBackendConfig):
        self.config = config
        # Initialize SDK/connection

    def trace(self, **kwargs):
        # Create trace
        pass

    def span(self, **kwargs):
        # Create span
        pass

    def flush(self):
        # Ensure all data is sent
        pass
```

### 2. Create Tracer

**tracers/mybackend.py:**
```python
from typing import Any, Dict, Optional
from hush.core.tracers import BaseTracer, register_tracer

@register_tracer
class MyBackendTracer(BaseTracer):
    def __init__(
        self,
        config: Optional["MyBackendConfig"] = None,
        resource_key: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ):
        super().__init__(tags=tags)
        if config is None and resource_key is None:
            raise ValueError("Must provide either 'config' or 'resource_key'")
        self._config = config
        self._resource_key = resource_key

    def _get_tracer_config(self) -> Dict[str, Any]:
        """Return config for subprocess."""
        if self._config is not None:
            return {"config": self._config.model_dump()}
        return {"resource_key": self._resource_key}

    @staticmethod
    def flush(flush_data: Dict[str, Any]) -> None:
        """Execute in subprocess - create traces from flush_data."""
        # Re-import dependencies (runs in subprocess)
        # Get client from config or ResourceHub
        ops_trace_data = flush_data["ops_trace_data"]
        for execution in flush_data["execution_order"]:
            op_id = execution["op"]
            parent_id = execution["parent"]
            context_id = execution.get("context_id")
            trace_key = f"{op_id}:{context_id}" if context_id else op_id
            trace_data = ops_trace_data.get(trace_key)
            # Create traces/spans with parent-child relationships
        # Call client.flush()
        pass
```

### 3. Register Plugin

**plugin.py:**
```python
from hush.core.registry import REGISTRY

@REGISTRY.register("mybackend")
def mybackend_plugin(config: dict):
    from hush.ops.backends.mybackend import MyBackendConfig, MyBackendClient
    return MyBackendClient(MyBackendConfig(**config))
```

### 4. Export in `__init__.py`

## Background Flushing

Tracers use a long-running background process to avoid blocking the main workflow:

1. During workflow execution, trace data is written incrementally to SQLite via the background process
2. After workflow completes, `tracer.flush_in_background()` marks traces as ready for flushing
3. The background worker calls `Tracer.flush(flush_data)` with the collected trace data
4. Main process continues without waiting

**Important**: The `flush()` method runs in the background worker process, so it must:
- Re-import all dependencies
- Use only data from `flush_data` dict
- Not access any shared state

### flush_data Structure

The `flush_data` dict passed to `Tracer.flush()` has this shape:

```python
{
    "tracer_type": "LangfuseTracer",       # Registered tracer class name
    "tracer_config": {...},                 # From _get_tracer_config()
    "workflow_name": "my_workflow",
    "request_id": "uuid-...",
    "user_id": "...",                       # Optional
    "session_id": "...",                    # Optional
    "tags": ["prod"],
    "execution_order": [                    # Topologically sorted (parents first)
        {
            "op": "my_workflow",            # Op name (was "node" before rename)
            "parent": None,                 # Parent op name (None for root)
            "context_id": None,             # Loop iteration context
            "contain_generation": False,    # Whether op has LLM generation data
        },
        ...
    ],
    "ops_trace_data": {                     # Keyed by "op_name" or "op_name:context_id"
        "my_workflow": {
            "name": "my_workflow",
            "start_time": "...",
            "end_time": "...",
            "input": {...},
            "output": {...},
            "model": None,                  # Set for LLM ops
            "usage": None,                  # Token counts for LLM ops
            "cost": None,                   # Cost in USD for LLM ops
            "metadata": {...},
        },
        ...
    },
}
```

Key naming conventions:
- `execution["op"]` - the op's name (renamed from `execution["node"]`)
- `ops_trace_data` - trace data keyed by op name (renamed from `nodes_trace_data`)
- OTEL span attribute: `"op.name"` (renamed from `"node.name"`)

## Testing

Tests use `MockOp` and `MockIndexer` (see `tests/conftest.py`):

```python
class MockOp:
    """Mock op for testing."""
    def __init__(self, op_id: str, contain_generation: bool = False):
        self.op_id = op_id
        self.contain_generation = contain_generation
        self._trace_data = {"name": op_id, ...}

class MockIndexer:
    def __init__(self):
        self._ops = {}

    def add_op(self, op: MockOp):
        self._ops[op.op_id] = op
```

Tracer tests mock the backend client:

```python
import pytest
from unittest.mock import patch, MagicMock

@pytest.mark.asyncio
async def test_langfuse_tracer():
    with patch("hush.ops.tracers.langfuse.LangfuseClient") as mock_client:
        mock_client.return_value.trace.return_value = MagicMock()
        # Test tracer initialization and flush
```

## Feature Flags

- `[langfuse]` - Langfuse SDK
- `[otel]` - OpenTelemetry SDK + exporters
- `[all]` - Everything
