# hush-telemetry

External tracing backends for Hush workflows — Langfuse, OpenTelemetry, and local visualization.

[![PyPI](https://img.shields.io/pypi/v/hush-telemetry)](https://pypi.org/project/hush-telemetry/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)

## Installation

```bash
pip install hush-telemetry                 # Langfuse + HushEyes (default)
pip install "hush-telemetry[otel]"         # + OpenTelemetry
pip install "hush-telemetry[all]"          # Everything
```

## Quick Start

```python
from hush.core import Hush, GraphOp, START, END
from hush.telemetry import LangfuseTracer

with GraphOp(name="demo") as graph:
    # ... define ops
    pass

tracer = LangfuseTracer(resource="langfuse:default", tags=["prod"])
engine = Hush(graph, tracer=tracer)
result = await engine.run(inputs={...})
# Traces automatically sent to Langfuse
```

## Supported Backends

| Backend | Tracer Class | Description |
|---------|-------------|-------------|
| **HushEyes** | `HushEyesTracer` | Local trace viewer (SQLite, http://localhost:8420) |
| **Langfuse** | `LangfuseTracer` | LLM observability platform (custom HTTP, no SDK) |
| **OpenTelemetry** | `OTELTracer` | Vendor-neutral (Jaeger, Zipkin, Tempo, etc.) |

## Usage Patterns

```python
from hush.telemetry import HushEyesTracer, LangfuseTracer, LangfuseConfig

# Local visualization
tracer = HushEyesTracer(tags=["dev"])

# Langfuse with ResourceHub
tracer = LangfuseTracer(resource="langfuse:default")

# Langfuse with direct config
tracer = LangfuseTracer(config=LangfuseConfig.from_env())

# Multiple tracers at once
engine = Hush(graph, tracer=[HushEyesTracer(), LangfuseTracer(resource="langfuse:default")])
```

## Configuration

```yaml
# resources.yaml
tracer:
  langfuse:
    public_key: ${LANGFUSE_PUBLIC_KEY}
    secret_key: ${LANGFUSE_SECRET_KEY}
    host: https://cloud.langfuse.com
```

## Features

- **Hierarchical tracing** — pre-computed TraceNode tree with parent-child relationships
- **Token/cost tracking** — automatic for LLM ops
- **Stream sampling** — cap trace items per generator (default: 100)
- **Background flushing** — non-blocking, ThreadPoolExecutor-based
- **No SDK dependency** — Langfuse uses custom HTTP client (pure REST API)

## Related Packages

| Package | Description |
|---------|-------------|
| [hush-icore](https://pypi.org/project/hush-icore/) | Core engine with built-in LocalTracer |
| [hush-eyes](https://crates.io/crates/hush-eyes) | Standalone trace visualization server |

## License

Apache 2.0
