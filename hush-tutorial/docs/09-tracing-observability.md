# Tracing và Observability

Debug và monitor workflows với LocalTracer, Langfuse, và OpenTelemetry.

> **Ví dụ chạy được**: `examples/06_tracing.py`, `examples/08_langfuse_tracing.py`, `examples/09_otel_tracing.py`

## Tại sao cần Tracing?

- **Debug**: Node nào gây lỗi? Input/output mỗi node là gì?
- **Monitor**: Workflow chạy bao lâu? Node nào là bottleneck?
- **Cost tracking**: Bao nhiêu tokens? Chi phí LLM calls?

## Kiến trúc

```
┌─────────────────────────────────────────────┐
│                 Hush Engine                   │
│  ┌─────────────────────────────────────┐     │
│  │              Tracer                  │     │
│  │  • LocalTracer  (SQLite, built-in)   │     │
│  │  • LangfuseTracer (cloud)            │     │
│  │  • OTelTracer (OpenTelemetry)        │     │
│  └─────────────────────────────────────┘     │
└─────────────────────────────────────────────┘
```

## LocalTracer (Built-in)

Lưu traces vào SQLite — zero dependencies, chạy offline.

```python
from hush.core import Hush
from hush.core.tracers import LocalTracer

tracer = LocalTracer(
    name="my-app",
    tags=["dev", "testing"]
)

engine = Hush(graph)
result = await engine.run(
    inputs={"query": "Hello"},
    tracer=tracer,
    user_id="user-123",
    session_id="session-456"
)
# Traces lưu tại ~/.hush/traces.db
```

## LangfuseTracer (Cloud)

Gửi traces đến [Langfuse](https://langfuse.com) — cloud platform với dashboard, team collaboration, cost tracking.

### Cấu hình resources.yaml

```yaml
langfuse:default:
  public_key: ${LANGFUSE_PUBLIC_KEY}
  secret_key: ${LANGFUSE_SECRET_KEY}
  host: ${LANGFUSE_HOST}
  enabled: true
```

### Sử dụng

```python
from hush.observability import LangfuseTracer

# Cách 1: Dùng ResourceHub
tracer = LangfuseTracer(
    resource_key="langfuse:default",
    tags=["production", "v1.0"]
)

# Cách 2: Config trực tiếp
tracer = LangfuseTracer(
    public_key="pk-...",
    secret_key="sk-...",
    host="https://cloud.langfuse.com",
    tags=["production"]
)

result = await engine.run(inputs={...}, tracer=tracer)
# Trace URL được log tự động
```

Xem ví dụ đầy đủ tại `examples/08_langfuse_tracing.py`.

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
from hush.observability import OTelTracer

tracer = OTelTracer(resource_key="otel:default")
result = await engine.run(inputs={...}, tracer=tracer)
```

Xem ví dụ đầy đủ tại `examples/09_otel_tracing.py`.

## Trace Data

Mỗi node execution được ghi lại:

| Field | Mô tả |
|-------|-------|
| `node_name` | Tên đầy đủ của node |
| `start_time` | Thời gian bắt đầu |
| `end_time` | Thời gian kết thúc |
| `duration_ms` | Thời gian chạy (ms) |
| `input_data` | Input của node |
| `output_data` | Output của node |
| `model` | Tên model (cho LLM nodes) |
| `usage` | Token usage |
| `cost` | Chi phí USD |
| `error` | Lỗi nếu có |

## Tags

### Static tags

Set khi tạo tracer:

```python
tracer = LocalTracer(tags=["production", "v2.0", "customer-a"])
```

### Dynamic tags

Thêm trong runtime từ CodeNode bằng key `$tags`:

```python
def process(data):
    result = process_data(data)
    if result.get("from_cache"):
        return {"result": result, "$tags": ["cache-hit"]}
    return {"result": result}
```

## Request Correlation

Truyền user_id và session_id để filter traces:

```python
result = await engine.run(
    inputs={...},
    tracer=tracer,
    user_id=request.user.id,
    session_id=request.session.id
)
```

## Conditional Tracing

### Environment-based

```python
import os

if os.getenv("ENABLE_TRACING") == "true":
    tracer = LangfuseTracer(resource_key="langfuse:default")
else:
    tracer = None

result = await engine.run(inputs={...}, tracer=tracer)
```

### Sampling

```python
import random

# Trace 10% of requests
tracer = LangfuseTracer(resource_key="langfuse:default") if random.random() < 0.1 else None
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
| Tracing | LocalTracer | LangfuseTracer / OTelTracer |
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
- Cấu hình fallback cho LLM nodes
- Implement error handling
- Set up cost tracking
- Configure logging level
- Test edge cases

## Tiếp theo

- [Agent Workflow](10-agent-workflow.md) — Tool-calling agent
- [Multi-model](11-multi-model.md) — Load balancing, ensemble
