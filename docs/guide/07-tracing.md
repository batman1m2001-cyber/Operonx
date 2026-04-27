# Tracing

Operonx ships three tracing backends: a local file-based viewer, Langfuse,
and OpenTelemetry. All three implement the same `Tracer` interface — you
can register multiple at once.

## Local file tracer

No setup needed. Traces write to `~/.operonx/traces/{request_id}.json`,
configurable via `OPERON_TRACES_DIR`.

```python
from operonx.core import Operon
from operonx.telemetry.tracers import LocalTracer

engine = Operon(graph, tracer=LocalTracer())
```

## Langfuse

```bash
pip install "operonx[langfuse]"
```

Configure in `resources.yaml`:

```yaml
tracers:
  langfuse:
    backend: langfuse
    public_key: ${LANGFUSE_PUBLIC_KEY}
    secret_key: ${LANGFUSE_SECRET_KEY}
    host: ${LANGFUSE_HOST}
```

Use:

```python
from operonx.telemetry.tracers import LangfuseTracer

operonx.bootstrap()
engine = Operon(graph, tracer=LangfuseTracer(resource="langfuse"))
```

Every op start/end becomes a span. LLM ops automatically populate
input/output, model name, and token counts.

## OpenTelemetry

```bash
pip install "operonx[otel]"
```

```yaml
tracers:
  otel:
    backend: otel
    endpoint: ${OTEL_EXPORTER_OTLP_ENDPOINT}
    headers: ${OTEL_EXPORTER_OTLP_HEADERS}
```

```python
from operonx.telemetry.tracers import OTelTracer

operonx.bootstrap()
engine = Operon(graph, tracer=OTelTracer(resource="otel"))
```

Spans go to your OTLP collector. Compatible with any backend that
accepts OTLP gRPC or HTTP.

## Multiple tracers

Pass a list:

```python
engine = Operon(
    graph,
    tracer=[LocalTracer(), LangfuseTracer(resource="langfuse")],
)
```

Both fire for every span. Handy when you want a local viewer in dev plus
Langfuse in staging.

## Custom tracer

Implement the `Tracer` protocol — `on_start`, `on_end`, `on_error` —
and pass it to the engine. See `operonx.core.tracing` for the contract.

## Where to go next

- Stream traces alongside frames: [Streaming](06-streaming.md).
- Inspect resource configs: [Resource hub](../architecture/resource-hub.md).
