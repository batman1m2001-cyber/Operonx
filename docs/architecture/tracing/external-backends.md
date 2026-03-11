# External Tracing Backends

## Tổng quan

hush-telemetry cung cấp 2 external tracing backends: **Langfuse** và **OpenTelemetry (OTEL)**. Cả hai kế thừa `Tracer` từ `hush.core.tracing` và implement `flush(trace_data)` — instance method chạy trong FlushWorker thread pool.

Location: `hush-telemetry/hush/telemetry/`

## Kiến trúc

```
┌─────────────────────────────────────────────────────────────────┐
│                         hush-core                                │
│                                                                  │
│  ┌──────────┐  ┌──────────────────┐  ┌───────────────────────┐  │
│  │  Tracer   │  │  TraceCollector   │  │    FlushWorker        │  │
│  │  base.py  │  │  collector.py     │  │    flush_worker.py    │  │
│  │           │  │                   │  │    ThreadPoolExecutor │  │
│  └─────┬────┘  └──────────────────┘  └───────────────────────┘  │
│        │ kế thừa                                                 │
└────────┼────────────────────────────────────────────────────────┘
         │
┌────────┼────────────────────────────────────────────────────────┐
│        │              hush-telemetry                             │
│        │                                                         │
│   ┌────┴──────────────────────────────────────┐                  │
│   │                                            │                  │
│   ▼                                            ▼                  │
│ ┌──────────────────┐              ┌──────────────────┐           │
│ │  LangfuseTracer  │              │    OTELTracer     │           │
│ │                  │              │                   │           │
│ │  flush()         │              │  flush()          │           │
│ │  ├ _get_client() │              │  ├ _get_client()  │           │
│ │  ├ walk graph_   │              │  ├ walk graph_    │           │
│ │  │ structure     │              │  │ structure      │           │
│ │  ├ iterate       │              │  ├ iterate        │           │
│ │  │ records       │              │  │ records        │           │
│ │  └ client.flush()│              │  └ client.flush() │           │
│ │        │         │              │        │          │           │
│ │        ▼         │              │        ▼          │           │
│ │  LangfuseClient  │              │    OTELClient     │           │
│ └──────────────────┘              └──────────────────┘           │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Backend Pattern

Mỗi backend có 2 lớp:

| Lớp | File | Mục đích |
|-----|------|---------|
| Config | `backends/{name}/config.py` | Pydantic YamlModel cho ResourceHub |
| Client | `backends/{name}/client.py` | Lazy-init wrapper quanh vendor SDK |

---

## Plugin Registration

`ObservabilityPlugin` tự động register khi import hush-telemetry:

```python
# hush-telemetry/hush/telemetry/plugin.py
class ObservabilityPlugin:
    @classmethod
    def register(cls):
        from hush.core.registry import REGISTRY
        REGISTRY.register(LangfuseConfig, lambda c: LangfuseClient(c))
        REGISTRY.register(OTELConfig, lambda c: OTELClient(c))

ObservabilityPlugin.register()  # Tự động chạy khi import
```

Sau đó ResourceHub có thể resolve:

```python
hub.langfuse("default")  # → LangfuseClient
hub.otel("jaeger")       # → OTELClient
```

---

## Tracer Base Class (mới)

Tất cả external tracers kế thừa `Tracer` từ `hush.core.tracing` (thay vì `BaseTracer` cũ):

```python
from hush.core.tracing import Tracer

class MyTracer(Tracer):
    def __init__(self, config=None, resource=None, tags=None):
        super().__init__(tags=tags)
        if config is None and resource is None:
            raise ValueError("Must provide either 'config' or 'resource'")
        if config is not None and resource is not None:
            raise ValueError("Cannot provide both 'config' and 'resource'")
        self._config = config
        self._resource = resource

    def _get_client(self):
        """Get backend client from config or ResourceHub."""
        if self._config is not None:
            return MyBackendClient(self._config)
        from hush.core.registry import get_hub
        return get_hub().mybackend(self._resource)

    def flush(self, trace_data: dict) -> None:
        """Called by FlushWorker in a background thread."""
        client = self._get_client()
        # ... use graph_structure + records to create traces ...
        client.flush()
```

### So sánh với BaseTracer cũ

| Thuộc tính | BaseTracer (cũ) | Tracer (mới) |
|-----------|----------------|-------------|
| `flush()` | `@staticmethod` — chạy trong subprocess, cần re-import | Instance method — chạy trong thread pool, truy cập `self` |
| `_get_tracer_config()` | Required — serialize config cho subprocess | Không cần — `_get_client()` dùng `self._config` trực tiếp |
| Client creation | Re-create trong mỗi flush (subprocess isolate) | `_get_client()` helper trên instance |
| Data format | `execution_order` + `ops_trace_data` | `graph_structure` + `records` |
| Background mechanism | Subprocess (multiprocessing/Popen) | ThreadPoolExecutor(4) |

### `_get_client()` helper

Pattern chung cho cả LangfuseTracer và OTELTracer — resolve client từ config trực tiếp hoặc qua ResourceHub:

```python
def _get_client(self):
    if self._config is not None:
        # Direct: tạo client từ config object
        return MyBackendClient(self._config)

    # ResourceHub: lookup bằng resource key
    from hush.core.registry import get_hub
    return get_hub().mybackend(self._resource)
```

---

## trace_data Format (mới)

Dict mà `flush()` nhận từ FlushWorker. **Khác hoàn toàn** format cũ (`execution_order` + `ops_trace_data`).

```python
trace_data = {
    "request_id": "uuid-...",
    "workflow_name": "my_workflow",
    "user_id": "user123",              # Optional
    "session_id": "session456",        # Optional
    "tags": ["prod", "v2"],            # Merged: static (tracer) + dynamic (state)

    # Static — graph structure (one entry per op)
    "graph_structure": [
        {"op_name": "workflow", "op_type": "graph",
         "parent_name": None, "contain_generation": False},
        {"op_name": "workflow.prompt", "op_type": "func",
         "parent_name": "workflow", "contain_generation": False},
        {"op_name": "workflow.llm", "op_type": "llm",
         "parent_name": "workflow", "contain_generation": True},
    ],

    # Dynamic — execution records (one per op execution, loops → multiple)
    "records": [
        {
            "op_name": "workflow",
            "context_id": None,
            "inputs": {"query": "What is Hush?"},
            "outputs": {"answer": "Hush is a workflow engine."},
            "start_time": "2025-01-15T10:30:00.000000",
            "end_time": "2025-01-15T10:30:02.500000",
            "duration_ms": 2500.0,
            "model": None, "usage": None, "cost": None, "metadata": None,
        },
        {
            "op_name": "workflow.llm",
            "context_id": None,
            "inputs": {"messages": [...]},
            "outputs": {"content": "...", "model_used": "gpt-4o",
                        "tokens_used": {"prompt_tokens": 100, "completion_tokens": 50}},
            "start_time": "2025-01-15T10:30:00.300000",
            "end_time": "2025-01-15T10:30:02.400000",
            "duration_ms": 2100.0,
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "cost": 0.003,
            "metadata": None,
        },
    ],
}
```

### So sánh format cũ vs mới

| Cũ | Mới | Thay đổi |
|----|-----|---------|
| `execution_order` (list of dicts) | `graph_structure` + `records` | Static/dynamic tách biệt |
| `ops_trace_data` (dict by op key) | `records` (list in execution order) | Flat list thay vì nested dict |
| `ops_trace_data[key]["input"]` | `records[i]["inputs"]` | Key đổi (singular → plural) |
| `ops_trace_data[key]["output"]` | `records[i]["outputs"]` | Key đổi (singular → plural) |
| `tracer_type` + `tracer_config` | Không có | FlushWorker gọi `flush()` trực tiếp |
| Separate `contain_generation` per entry | `graph_structure` chứa `contain_generation` | Lookup qua `structure_map` |

---

## LangfuseTracer

### LangfuseConfig

```python
class LangfuseConfig(YamlModel):
    _category = "langfuse"
    public_key: str
    secret_key: str
    host: str = "https://cloud.langfuse.com"
    no_proxy: Optional[str] = None
    enabled: bool = True
    sample_rate: float = 1.0
```

Factory: `LangfuseConfig.from_env()` đọc từ `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`.

YAML config:

```yaml
langfuse:default:
  public_key: ${LANGFUSE_PUBLIC_KEY}
  secret_key: ${LANGFUSE_SECRET_KEY}
  host: https://cloud.langfuse.com
```

### LangfuseClient

Lazy-init wrapper quanh Langfuse SDK:

```python
class LangfuseClient:
    def trace(**kwargs)       # Tạo root trace
    def span(**kwargs)        # Tạo child span
    def generation(**kwargs)  # Tạo LLM generation
    def score(**kwargs)       # Ghi điểm
    def flush()               # Gửi tất cả pending events
    def get_prompt(name)      # Lấy prompt từ Langfuse
```

### flush() Implementation

1. Resolve client từ `self._config` hoặc ResourceHub via `_get_client()`
2. Build `structure_map`: `{op_name: structure_dict}` từ `graph_structure`
3. Iterate `records` theo execution order:
   - **Root** (parent_name is None): tạo `client.trace()` với `id=request_id`, `name=workflow_name`, `user_id`, `session_id`, `tags`
   - **Child non-generation** (contain_generation is False): tạo `parent.span()` với short name, timing, I/O
   - **Child generation** (contain_generation is True): tạo `parent.generation()` với model, usage (prompt_tokens → input, completion_tokens → output), cost (trong metadata)
4. Context-aware parent lookup cho loop iterations (context_id)
5. `client.flush()` cuối cùng
6. Log trace URL

```python
def flush(self, trace_data):
    client = self._get_client()

    structure_map = {s["op_name"]: s for s in trace_data.get("graph_structure", [])}
    langfuse_objects = {}

    for record in trace_data.get("records", []):
        op_name = record["op_name"]
        context_id = record.get("context_id")
        structure = structure_map.get(op_name, {})
        parent_name = structure.get("parent_name")
        contain_generation = structure.get("contain_generation", False)

        trace_key = f"{op_name}:{context_id}" if context_id else op_name

        if parent_name is None:
            # Root → client.trace()
            root_trace = client.trace(id=req_id, name=workflow_name, ...)
            langfuse_objects[trace_key] = root_trace
        elif contain_generation:
            # LLM child → parent.generation()
            parent = langfuse_objects[parent_key]
            langfuse_objects[trace_key] = parent.generation(
                name=short_name,
                model=record.get("model"),
                usage={"input": usage["prompt_tokens"], "output": usage["completion_tokens"]},
                ...
            )
        else:
            # Non-LLM child → parent.span()
            parent = langfuse_objects[parent_key]
            langfuse_objects[trace_key] = parent.span(name=short_name, ...)

    client.flush()
```

### Token Usage Transform

Langfuse dùng `input`/`output` thay vì `prompt_tokens`/`completion_tokens`:

```python
usage = record.get("usage")
if usage:
    langfuse_usage = {}
    if "prompt_tokens" in usage:
        langfuse_usage["input"] = usage["prompt_tokens"]
    if "completion_tokens" in usage:
        langfuse_usage["output"] = usage["completion_tokens"]
    if "total_tokens" in usage:
        langfuse_usage["total"] = usage["total_tokens"]
```

### Media Attachments

`LangfuseTracer._resolve_media()` static method hỗ trợ attach media (base64/file path) vào trace data:

```python
# Nếu op output chứa media_attachments:
media_attachments = {
    "image": {"content_type": "image/png", "base64": "...", "attach_to": "output"},
}
# → Convert thành LangfuseMedia objects và merge vào input/output/metadata
```

---

## OTELTracer

### OTELConfig

```python
class OTELConfig(YamlModel):
    _category = "otel"
    endpoint: str
    protocol: Literal["grpc", "http"] = "grpc"
    headers: Optional[Dict[str, str]] = None
    service_name: str = "hush-workflow"
    service_version: Optional[str] = None
    insecure: bool = False
    timeout: int = 30
    enabled: bool = True
    sample_rate: float = 1.0
```

Factory methods:

```python
OTELConfig.from_env()                               # Từ OTEL_EXPORTER_OTLP_* env vars
OTELConfig.jaeger(host="localhost", port=4317)       # Local Jaeger
OTELConfig.tempo(endpoint="...", api_key="...")       # Grafana Tempo
```

YAML config:

```yaml
otel:jaeger:
  endpoint: http://localhost:4317
  protocol: grpc
  service_name: my-workflow
```

### OTELClient

Lazy-init wrapper quanh OpenTelemetry SDK:

- Tạo `TracerProvider` với `BatchSpanProcessor`
- Hỗ trợ gRPC và HTTP exporters
- Reuse global provider nếu đã set
- Methods: `start_span()`, `get_current_span()`, `flush()`, `shutdown()`

### flush() Implementation

1. Resolve client từ `self._config` hoặc ResourceHub via `_get_client()`
2. Build `structure_map` từ `graph_structure`
3. Get `otel_tracer` từ `client.tracer`
4. Iterate `records`:
   - **Root** (parent_name is None): `otel_tracer.start_span(name=workflow_name, attributes=..., start_time=ns)`
   - **Child**: `otel_tracer.start_span(name=short_name, context=parent_ctx, attributes=..., start_time=ns)`
5. Set standard attributes per span:
   - `workflow.name`, `workflow.request_id`
   - `op.name`
   - `user.id`, `session.id`
   - `langfuse.user.id`, `langfuse.session.id`, `langfuse.tags` (compatibility)
6. Cho generations (`contain_generation == True`):
   - `llm.request.type` = `"generation"`
   - `llm.model` = model name
   - `llm.usage.prompt_tokens`, `llm.usage.completion_tokens`, `llm.usage.total_tokens`
7. Serialize input/output thành JSON attributes (giới hạn 10KB)
8. End spans theo **reverse order** (children first, then parents)
9. `client.flush()`

```python
def flush(self, trace_data):
    client = self._get_client()
    otel_tracer = client.tracer

    structure_map = {s["op_name"]: s for s in trace_data.get("graph_structure", [])}
    spans = {}
    span_end_times = {}

    for record in trace_data.get("records", []):
        # Build attributes
        attributes = {"workflow.name": workflow_name, "op.name": op_name, ...}

        if contain_generation:
            attributes["llm.request.type"] = "generation"
            attributes["llm.model"] = record["model"]
            # ... usage attributes

        if parent_name is None:
            span = otel_tracer.start_span(name=workflow_name, attributes=attributes,
                                           start_time=start_time_ns)
        else:
            ctx = trace.set_span_in_context(parent_span)
            span = otel_tracer.start_span(name=short_name, context=ctx,
                                           attributes=attributes, start_time=start_time_ns)

        spans[trace_key] = span
        span_end_times[trace_key] = end_time_ns

    # End spans in reverse (children first)
    for key in reversed(list(spans.keys())):
        spans[key].set_status(Status(StatusCode.OK))
        spans[key].end(end_time=span_end_times[key])

    client.flush()
```

### Datetime Conversion

OTEL sử dụng nanoseconds since epoch:

```python
@staticmethod
def _datetime_to_ns(dt) -> Optional[int]:
    """Convert datetime or ISO string to nanoseconds."""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if isinstance(dt, datetime):
        return int(dt.timestamp() * 1_000_000_000)
    return None
```

---

## Context-Aware Parent Lookup

Cả LangfuseTracer và OTELTracer dùng cùng logic để tìm parent span khi record có `context_id` (loop iterations):

```python
# Record: op_name="loop.step", parent_name="loop", context_id="[0].[1]"

# Step 1: Build unique key cho record
trace_key = f"{op_name}:{context_id}" if context_id else op_name
# → "loop.step:[0].[1]"

# Step 2: Tìm parent key
parent_key = parent_name  # "loop"

if parent_name and context_id:
    # Try 1: parent cùng full context
    # "loop:[0].[1]"
    context_parent_key = f"{parent_name}:{context_id}"
    if context_parent_key in known_spans:
        parent_key = context_parent_key

    else:
        # Try 2: parent ở outer context (strip last segment)
        # "[0].[1]" → rfind(".") → "[0]"
        # "loop:[0]"
        last_dot = context_id.rfind(".")
        if last_dot > 0:
            parent_context = context_id[:last_dot]
            parent_with_parent_ctx = f"{parent_name}:{parent_context}"
            if parent_with_parent_ctx in known_spans:
                parent_key = parent_with_parent_ctx
```

---

## Sử dụng

### Langfuse

```python
from hush.core import Hush, GraphOp
from hush.telemetry import LangfuseTracer

# Production: Use ResourceHub
tracer = LangfuseTracer(resource="langfuse:default", tags=["prod"])

# Simple: Direct config
from hush.telemetry import LangfuseConfig
tracer = LangfuseTracer(config=LangfuseConfig.from_env())

result = await engine.run(inputs={...}, tracer=tracer)
```

### OTEL

```python
from hush.telemetry import OTELTracer, OTELConfig

# Production: Use ResourceHub
tracer = OTELTracer(resource="otel:jaeger", tags=["prod"])

# Simple: Direct config
tracer = OTELTracer(config=OTELConfig.jaeger())

result = await engine.run(inputs={...}, tracer=tracer)
```

### Multiple Tracers

```python
from hush.telemetry import HushEyesTracer
from hush.telemetry import LangfuseTracer

result = await engine.run(
    inputs={...},
    tracer=[
        HushEyesTracer(tags=["dev"]),
        LangfuseTracer(resource="langfuse:default", tags=["prod"]),
    ],
)
# Cả hai nhận cùng trace_data, nhưng tags merged per-tracer
```

---

## Thêm External Backend Mới

### 1. Tạo backend (config + client)

```python
# backends/mybackend/config.py
from hush.core.utils import YamlModel

class MyBackendConfig(YamlModel):
    _category: ClassVar[str] = "mybackend"
    api_key: str
    endpoint: str
    enabled: bool = True
```

```python
# backends/mybackend/client.py
class MyBackendClient:
    def __init__(self, config: MyBackendConfig):
        self._config = config
        self._client = None  # Lazy init

    def send_trace(self, name, start, end, metadata):
        pass

    def flush(self):
        pass
```

### 2. Tạo tracer

```python
# tracers/mybackend.py
from hush.core.tracing import Tracer

class MyBackendTracer(Tracer):
    def __init__(self, config=None, resource=None, tags=None):
        super().__init__(tags=tags)
        if config is None and resource is None:
            raise ValueError("Must provide either 'config' or 'resource'")
        if config is not None and resource is not None:
            raise ValueError("Cannot provide both 'config' and 'resource'")
        self._config = config
        self._resource = resource

    def _get_client(self):
        """Get client from config or ResourceHub."""
        if self._config is not None:
            return MyBackendClient(self._config)
        from hush.core.registry import get_hub
        return get_hub().mybackend(self._resource)

    def flush(self, trace_data: dict) -> None:
        """Called by FlushWorker in a background thread.

        trace_data contains: graph_structure, records, tags,
        request_id, workflow_name, user_id, session_id
        """
        client = self._get_client()

        # Build structure lookup for parent/generation info
        structure_map = {
            s["op_name"]: s
            for s in trace_data.get("graph_structure", [])
        }

        # Iterate records in execution order
        for record in trace_data.get("records", []):
            op_name = record["op_name"]
            structure = structure_map.get(op_name, {})
            parent_name = structure.get("parent_name")
            contain_generation = structure.get("contain_generation", False)

            client.send_trace(
                name=op_name,
                start=record.get("start_time"),
                end=record.get("end_time"),
                metadata={
                    "inputs": record.get("inputs"),
                    "outputs": record.get("outputs"),
                    "model": record.get("model") if contain_generation else None,
                    "usage": record.get("usage") if contain_generation else None,
                    "cost": record.get("cost"),
                },
            )

        client.flush()
```

### 3. Register plugin + export

```python
# plugin.py
from hush.core.registry import REGISTRY
REGISTRY.register(MyBackendConfig, lambda c: MyBackendClient(c))
```

```python
# __init__.py
from hush.telemetry.tracers.mybackend import MyBackendTracer
```

---

## Xem thêm

- [Overview](overview.md) — Kiến trúc tổng thể tracing system
- [Data Model](data-model.md) — Chi tiết format trace_data, dataclasses, context IDs
