# Hush Observability

> Observability framework cho Hush workflows - hỗ trợ nhiều backend providers.

## Cài đặt

```bash
# Qua meta-package (khuyến nghị)
uv pip install "hush-ai[langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# Hoặc với tất cả backends
uv pip install "hush-ai[all] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# Hoặc editable (cho development)
git clone https://github.com/batman1m2001-cyber/Hush-ai.git && cd hush
uv pip install -e hush-core -e "hush-observability[all]"
```

Xem chi tiết tại [Cài đặt và Thiết lập](../hush-tutorial/docs/01-cai-dat-va-thiet-lap.md).

## Quick Start

```python
from hush.core import RESOURCE_HUB
from hush.observability import LangfuseConfig

# Đăng ký tracer config
config = LangfuseConfig(
    public_key="pk-...",
    secret_key="sk-...",
    host="https://cloud.langfuse.com"
)
RESOURCE_HUB.register(config, registry_key="tracer:langfuse")

# Sử dụng với workflow
from hush.core import Hush, GraphNode, START, END

with GraphNode(name="demo", tracer_key="langfuse") as graph:
    # ... định nghĩa nodes
    pass

engine = Hush(graph)
await engine.run()  # Traces tự động gửi đến Langfuse
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

- [User Docs](../hush-tutorial/docs/) - Tutorials và guides
- [Architecture](../architecture/tracing/) - Internal documentation
  - [Tracer Interface](../architecture/tracing/tracer-interface.md)
  - [Local Tracer](../architecture/tracing/local-tracer.md)
  - [Data Model](../architecture/tracing/trace-data-model.md)
  - [Async Buffer](../architecture/tracing/async-buffer.md)

## License

MIT
