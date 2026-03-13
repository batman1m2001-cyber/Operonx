# Tracing và Observability

Debug và monitor workflows với HushEyesTracer, Langfuse, và OpenTelemetry.

> **Ví dụ chạy được**: `examples/06_tracing/demo.py`

## Tại sao cần Tracing?

- **Debug**: Op nào gây lỗi? Input/output mỗi op là gì?
- **Monitor**: Workflow chạy bao lâu? Op nào là bottleneck?
- **Cost tracking**: Bao nhiêu tokens? Chi phí LLM calls?

## Kiến trúc

```
Op.run() → stores I/O, timing, cost to state (ops không biết về tracing)
engine.run() completes
  → FlushWorker.submit(tracers, graph, state)     ← returns immediately
    → ThreadPoolExecutor thread:
      → TraceCollector.collect(graph, state)        ← reads state directly
      → tracer.flush(trace_data)                    ← I/O-bound (HTTP, SDK calls)
```

Tracers nhận `trace_data` dict chứa:
- `graph_structure`: Static metadata từ compiled graph (op_name, op_type, parent)
- `records`: Dynamic execution data từ state (I/O, timing, model, usage, cost)

## HushEyesTracer (Built-in)

Gửi traces đến **ui-hush-eyes** server — standalone Rust server với web UI.

### Khởi chạy server

```bash
cd ui-hush-eyes && cargo run
# Mở http://localhost:8420 để xem traces
```

### Sử dụng

```python
from hush.core import Hush
from hush.telemetry import HushEyesTracer

tracer = HushEyesTracer(tags=["dev", "testing"])

engine = Hush(graph)
result = await engine.run(
    inputs={"query": "Hello"},
    tracer=tracer,
    user_id="user-123",
    session_id="session-456"
)
# Mở http://localhost:8420 để xem trace
```

### Cấu hình server

```bash
# Default: localhost:8420, DB tại ~/.hush/ui-hush-eyes.db
cargo run

# Custom host/port/db
cargo run -- --host 0.0.0.0 --port 9000 --db-path /tmp/traces.db
```

## LangfuseTracer (Cloud)

Gửi traces đến [Langfuse](https://langfuse.com) — cloud platform với dashboard, team collaboration, cost tracking.

### Cấu hình resources.yaml

```yaml
langfuse:hush:
  public_key: ${LANGFUSE_PUBLIC_KEY}
  secret_key: ${LANGFUSE_SECRET_KEY}
  host: ${LANGFUSE_HOST}
  enabled: true
```

### Sử dụng

```python
from hush.telemetry import LangfuseTracer

# Cách 1: Dùng ResourceHub
tracer = LangfuseTracer(
    resource="langfuse:hush",
    tags=["production", "v1.0"]
)

# Cách 2: Config trực tiếp
tracer = LangfuseTracer(
    public_key="pk-...",
    secret_key="sk-...",
    host="https://cloud.langfuse.com",
    tags=["production"]
)

engine = Hush(graph, tracer=tracer)
result = await engine.run(inputs={...})
# Trace URL được log tự động
```

Xem ví dụ đầy đủ tại `examples/06_tracing/demo.py` (Langfuse tracing is configured via env vars).

## OTelTracer (OpenTelemetry)

Export traces theo chuẩn OpenTelemetry — tích hợp với Jaeger, Grafana, Datadog, etc.

### Cấu hình resources.yaml

```yaml
otel:default:
  endpoint: ${LANGFUSE_HOST}/api/public/otel/v1/traces
  protocol: http
  service_name: hush-workflow
  auth_type: basic
  public_key: ${LANGFUSE_PUBLIC_KEY}
  secret_key: ${LANGFUSE_SECRET_KEY}
```

### Sử dụng

```python
from hush.telemetry import OTelTracer

tracer = OTelTracer(resource="otel:default")
engine = Hush(graph, tracer=tracer)
result = await engine.run(inputs={...})
```

Xem ví dụ đầy đủ tại `examples/06_tracing/demo.py` (OTEL tracing is configured via env vars).

## Multiple Tracers

Gửi traces đến nhiều backends cùng lúc:

```python
from hush.telemetry import HushEyesTracer
from hush.telemetry import LangfuseTracer

tracers = [
    HushEyesTracer(tags=["dev"]),
    LangfuseTracer(resource="langfuse:hush", tags=["prod"]),
]
engine = Hush(graph, tracer=tracers)
result = await engine.run(inputs={...})
# Mỗi tracer nhận cùng trace_data, flush trong thread riêng
```

## Trace Data

Mỗi op execution được ghi lại:

| Field | Mô tả |
|-------|-------|
| `op_name` | Tên đầy đủ của op |
| `context_id` | Context ID (cho iteration ops) |
| `start_time` | Thời gian bắt đầu |
| `end_time` | Thời gian kết thúc |
| `duration_ms` | Thời gian chạy (ms) |
| `inputs` | Input của op |
| `outputs` | Output của op |
| `model` | Tên model (cho LLM ops) |
| `usage` | Token usage |
| `cost` | Chi phí USD |

## Tags

### Static tags

Set khi tạo tracer:

```python
tracer = HushEyesTracer(tags=["production", "v2.0", "customer-a"])
```

### Dynamic tags

Thêm trong runtime từ FuncOp bằng key `$tags`:

```python
def process(data):
    result = process_data(data)
    if result.get("from_cache"):
        return {"result": result, "$tags": ["cache-hit"]}
    return {"result": result}
```

### Tag merging

FlushWorker merge tags cho mỗi tracer:
1. Static tags từ tracer (set khi tạo)
2. Dynamic tags từ state (thu thập qua `$tags` trong op outputs)
3. Deduplicated — không có trùng lặp

## Request Correlation

Truyền user_id và session_id để filter traces:

```python
engine = Hush(graph, tracer=tracer)
result = await engine.run(
    inputs={...},
    user_id=request.user.id,
    session_id=request.session.id,
)
```

## Conditional Tracing

### Environment-based

```python
import os

if os.getenv("ENABLE_TRACING") == "true":
    tracers = [LangfuseTracer(resource="langfuse:hush")]
else:
    tracers = []

engine = Hush(graph, tracer=tracers)
result = await engine.run(inputs={...})
```

### Sampling

```python
import random

# Trace 10% of requests
tracers = [LangfuseTracer(resource="langfuse:hush")] if random.random() < 0.1 else []
```

## Cost Tracking

### Cấu hình trong resources.yaml

```yaml
llm:gpt-4o:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  model: gpt-4o
  cost_per_input_token: 0.000005    # $5 per 1M input
  cost_per_output_token: 0.000015   # $15 per 1M output
```

Cost được track tự động trong traces. Với LangfuseTracer, cost hiển thị trên dashboard.

## Production Setup

| Aspect | Development | Production |
|--------|-------------|------------|
| Tracing | HushEyesTracer | LangfuseTracer / OTelTracer |
| Logging | DEBUG | INFO / WARNING |
| Error handling | Basic | Comprehensive + fallback |
| Cost tracking | Optional | Required |
| Sampling | 100% | 10-100% tùy traffic |

### Environment Variables

```bash
export OPENAI_API_KEY=sk-...
export HUSH_CONFIG=/path/to/resources.yaml
export LANGFUSE_SECRET_KEY=sk-...
export LANGFUSE_PUBLIC_KEY=pk-...
export LANGFUSE_HOST=https://cloud.langfuse.com
```

### Deployment Checklist

- Cấu hình `resources.yaml` với tất cả providers
- Set environment variables cho API keys
- Enable tracing (Langfuse hoặc OTEL)
- Cấu hình fallback cho LLM ops
- Implement error handling
- Set up cost tracking
- Configure logging level
- Test edge cases

## Tiếp theo

- [Agent Workflow](10-agent-workflow.md) — Tool-calling agent
- [Multi-model](11-multi-model.md) — Load balancing, ensemble
