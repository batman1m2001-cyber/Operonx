# External Tracing Backends

## Overview

hush-telemetry cung cấp 2 external tracing backends: **Langfuse** và **OpenTelemetry (OTEL)**. Cả hai kế thừa `BaseTracer` từ hush-core và sử dụng subprocess-based flushing để gửi traces mà không ảnh hưởng performance.

Location: `hush-telemetry/hush/telemetry/`

## Kiến trúc

```
┌─────────────────────────────────────────────────────────┐
│                     hush-core                            │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ BaseTracer│  │ @register_   │  │ BackgroundProcess │  │
│  │ interface │  │ tracer       │  │ subprocess flush  │  │
│  └──────────┘  └──────────────┘  └───────────────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │ kế thừa
┌──────────────────────┼──────────────────────────────────┐
│                hush-telemetry                            │
│                      │                                   │
│     ┌────────────────┴────────────────┐                  │
│     │                                 │                  │
│  ┌──▼─────────────┐  ┌───────────────▼──┐               │
│  │ LangfuseTracer │  │   OTELTracer     │               │
│  │                │  │                  │               │
│  │ flush() ──────┐│  │ flush() ────────┐│               │
│  │               ▼│  │                ▼│               │
│  │ LangfuseClient│  │  OTELClient     │               │
│  └────────────────┘  └────────────────┘               │
└─────────────────────────────────────────────────────────┘
```

### Backend Pattern

Mỗi backend có 2 lớp:

| Lớp | File | Mục đích |
|-----|------|---------|
| Config | `backends/{name}/config.py` | Pydantic YamlModel cho ResourceHub |
| Client | `backends/{name}/client.py` | Lazy-init wrapper quanh vendor SDK |

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

## flush_data Structure

Dict được truyền cho `flush()` static method trong subprocess:

```python
flush_data = {
    "tracer_type": "LangfuseTracer",      # Class name
    "tracer_config": {                     # Từ _get_tracer_config()
        "config": {...}                    # Hoặc "resource_key": "..."
    },
    "workflow_name": "my_workflow",
    "request_id": "uuid-...",
    "user_id": "user123",                  # Optional
    "session_id": "session456",            # Optional
    "tags": ["production", "v2"],
    "execution_order": [                   # Topo-sorted (cha trước con)
        {
            "op": "workflow.prompt",
            "parent": "workflow",          # None cho root
            "context_id": None,            # "[0]" cho loop iterations
            "contain_generation": False,
        },
        {
            "op": "workflow.llm",
            "parent": "workflow",
            "context_id": None,
            "contain_generation": True,
        },
    ],
    "ops_trace_data": {                    # Key: "op_name" hoặc "op_name:context_id"
        "workflow.prompt": {
            "name": "prompt",
            "start_time": "2024-01-01T10:00:00",
            "end_time": "2024-01-01T10:00:01",
            "input": {...},
            "output": {...},
            "model": None,
            "usage": None,
            "cost": None,
            "metadata": {...},
        },
        "workflow.llm": {
            "name": "llm",
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "cost": {"input": 0.001, "output": 0.002, "total": 0.003},
            ...
        },
    },
}
```

## Subprocess Isolation

`flush()` là **static method** chạy trong subprocess riêng biệt:
- Re-import tất cả dependencies (không chia sẻ state với main process)
- Tạo client từ config hoặc ResourceHub
- Gửi traces đến external service
- Errors không ảnh hưởng main workflow

```python
@staticmethod
def flush(flush_data: dict) -> None:
    # Re-import trong subprocess
    from hush.core.registry import get_hub
    # ... resolve client, send traces
```

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

1. Resolve client từ config hoặc ResourceHub
2. Duyệt `execution_order` (topo-sorted)
3. Tạo root trace cho nodes không có parent
4. Tạo spans/generations cho child nodes
5. Phân biệt **span** (không có cost/usage) vs **generation** (có cost/usage)
6. Transform token usage: `prompt_tokens` → `input`, `completion_tokens` → `output`
7. Xử lý media attachments (base64/file → LangfuseMedia objects)
8. Gọi `client.flush()` cuối cùng
9. Log trace URL

### Context-Aware Parent Lookup

Cho nested loops (context_id = `[0].[1]`):

```python
# Execution order có thể có:
# op="loop.step", parent="loop", context_id="[0].[1]"
# Parent lookup: "loop:[0]" (strip last segment)
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
OTELConfig.from_env()                     # Từ OTEL_EXPORTER_OTLP_* env vars
OTELConfig.jaeger(host="localhost", port=4317)  # Local Jaeger
OTELConfig.tempo(endpoint="...", api_key="...")  # Grafana Tempo
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

1. Resolve client từ config hoặc ResourceHub
2. Lấy tracer từ `client.tracer`
3. Duyệt `execution_order`:
   - Root spans: `otel_tracer.start_span()`
   - Child spans: tạo trong context của parent span
4. Set standard attributes:
   - `workflow.name`, `workflow.request_id`
   - `op.name`, `op.type`
   - `user.id`, `session.id`
5. Cho generations (contain_generation=True):
   - `llm.request.type`, `llm.model`
   - `llm.usage.prompt_tokens`, `llm.usage.completion_tokens`
   - `llm.cost.total`
6. Langfuse compatibility attributes:
   - `langfuse.user.id`, `langfuse.session.id`, `langfuse.tags`
7. Serialize input/output thành JSON (giới hạn 10KB)
8. End spans theo **reverse order** (con trước cha)
9. `client.flush()`

### Datetime Conversion

OTEL sử dụng nanoseconds:

```python
@staticmethod
def _datetime_to_ns(dt) -> Optional[int]:
    # datetime → nanoseconds since epoch
    return int(dt.timestamp() * 1_000_000_000)
```

---

## Sử dụng

### Langfuse

```python
from hush.core import Hush, GraphOp
from hush.telemetry import LangfuseTracer

tracer = LangfuseTracer(resource_key="langfuse:default")
# hoặc: LangfuseTracer(config=LangfuseConfig.from_env())

engine = Hush(graph, tracers=[tracer])
await engine.run(inputs={...})
```

### OTEL

```python
from hush.telemetry import OTELTracer

tracer = OTELTracer(resource_key="otel:jaeger")
# hoặc: OTELTracer(config=OTELConfig.jaeger())

engine = Hush(graph, tracers=[tracer])
await engine.run(inputs={...})
```

---

## Thêm External Backend Mới

1. Tạo `backends/mybackend/config.py`:

```python
from hush.core.utils import YamlModel

class MyBackendConfig(YamlModel):
    _category: ClassVar[str] = "mybackend"
    api_key: str
    endpoint: str
    enabled: bool = True
```

2. Tạo `backends/mybackend/client.py`:

```python
class MyBackendClient:
    def __init__(self, config: MyBackendConfig):
        self._config = config
        self._client = None  # Lazy init

    def send_trace(self, name, start, end, metadata):
        # Gửi trace đến backend
        pass

    def flush(self):
        pass
```

3. Tạo `tracers/mybackend.py`:

```python
from hush.core.tracers import BaseTracer, register_tracer

@register_tracer
class MyBackendTracer(BaseTracer):
    def __init__(self, resource_key=None, config=None, tags=None):
        super().__init__(tags=tags)
        self._resource_key = resource_key
        self._config = config

    def _get_tracer_config(self) -> dict:
        if self._config:
            return {"config": self._config.model_dump()}
        return {"resource_key": self._resource_key}

    @staticmethod
    def flush(flush_data: dict) -> None:
        from hush.core.registry import get_hub
        # Re-import dependencies trong subprocess
        config = flush_data["tracer_config"]
        if "resource_key" in config:
            client = get_hub().mybackend(config["resource_key"])
        else:
            client = MyBackendClient(MyBackendConfig(**config["config"]))

        # Gửi traces
        for entry in flush_data["execution_order"]:
            op_key = entry["op"]
            data = flush_data["ops_trace_data"].get(op_key, {})
            client.send_trace(
                name=data.get("name", op_key),
                start=data.get("start_time"),
                end=data.get("end_time"),
                metadata=data,
            )
        client.flush()
```

4. Register plugin:

```python
# Trong plugin.py
REGISTRY.register(MyBackendConfig, lambda c: MyBackendClient(c))
```

## Xem thêm

- [Tracer Interface](tracer-interface.md) - BaseTracer abstract class
- [Local Tracer](local-tracer.md) - SQLite implementation
- [Trace Data Model](trace-data-model.md) - Database schema
- [Async Buffer](async-buffer.md) - Background process architecture
