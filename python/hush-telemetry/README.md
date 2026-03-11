# Hush Observability

> Observability framework cho Hush workflows - hỗ trợ nhiều backend providers.

## Cài đặt

```bash
# Với pip
pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"
pip install "hush-telemetry[langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-telemetry"

# Với uv
uv pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"
uv pip install "hush-telemetry[langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-telemetry"

# Editable (cho development)
git clone https://github.com/batman1m2001-cyber/Hush-ai.git && cd Hush-ai
uv pip install -e hush-core -e "hush-telemetry[all]"
```

Xem chi tiết tại [Cài đặt và Thiết lập](../tutorial/docs/01-cai-dat-va-thiet-lap.md).

## Quick Start

```python
from hush.core import Hush, GraphOp, START, END
from hush.telemetry import LangfuseTracer

# Định nghĩa workflow
with GraphOp(name="demo") as graph:
    # ... định nghĩa nodes
    pass

# Tạo tracer
tracer = LangfuseTracer(
    resource="langfuse:default",
    tags=["production"]
)

# Chạy workflow với tracer
engine = Hush(graph)
result = await engine.run(inputs={...}, tracer=tracer)
# Traces tự động gửi đến Langfuse
```

## Supported Backends

| Backend | Config Class | Mô tả |
|---------|--------------|-------|
| Langfuse | `LangfuseConfig` | LLM observability platform |
| Phoenix | `PhoenixConfig` | Arize Phoenix (self-hosted) |
| Opik | `OpikConfig` | Comet Opik |
| LangSmith | `LangSmithConfig` | LangChain tracing |

## YAML Configuration

```yaml
# resources.yaml
tracer:langfuse:
  _class: LangfuseConfig
  public_key: ${LANGFUSE_PUBLIC_KEY}
  secret_key: ${LANGFUSE_SECRET_KEY}
  host: https://cloud.langfuse.com

tracer:phoenix:
  _class: PhoenixConfig
  endpoint: http://localhost:6006
```

## Features

- **Unified interface**: Một API cho tất cả backends
- **Hierarchical tracing**: Traces với parent-child relationships
- **Token tracking**: Tự động track token usage và cost
- **Async-first**: Buffering và batching cho performance

## Architecture

```
┌─────────────────────────────────────┐
│         Your Application            │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│      BaseTracer Interface           │
└──────────────┬──────────────────────┘
               │
       ┌───────┴────────┬─────────────┐
       │                │             │
┌──────▼──────┐  ┌──────▼──────┐  ┌──▼───┐
│  Langfuse   │  │   Phoenix   │  │ Opik │
└─────────────┘  └─────────────┘  └──────┘
```

## Documentation

- [User Docs](../tutorial/docs/) - Tutorials và guides
- [Architecture](../architecture/tracing/) - Internal documentation
  - [Tracer Interface](../architecture/tracing/tracer-interface.md)
  - [Local Tracer](../architecture/tracing/local-tracer.md)
  - [Data Model](../architecture/tracing/trace-data-model.md)
  - [Async Buffer](../architecture/tracing/async-buffer.md)

## License

MIT
