---
paths: ["python/hush-telemetry/**"]
---

# hush-telemetry (Python)

External tracing backend integrations.

## Module Structure

```
hush/telemetry/
├── plugin.py           # ObservabilityPlugin for ResourceHub
├── backends/
│   ├── langfuse/       # LangfuseConfig + LangfuseClient (custom HTTP, no SDK)
│   └── otel/           # OTELConfig + OTELClient
└── tracers/
    ├── hush_eyes.py    # HushEyesTracer — HTTP POST to ui-hush-eyes
    ├── langfuse.py     # LangfuseTracer — REST API + Basic auth
    └── otel.py         # OTELTracer — OTLP export
```

## Key Concepts

- **Backend**: Low-level HTTP client (LangfuseClient, OTELClient)
- **Tracer**: High-level — inherits `Tracer` from hush-icore, implements `flush(trace_data)`

## Usage

```python
# With ResourceHub
tracer = LangfuseTracer(resource="langfuse:default", tags=["prod"])

# Direct config
tracer = LangfuseTracer(config=LangfuseConfig.from_env())

# Multiple tracers
engine = Hush(graph, tracer=[HushEyesTracer(), LangfuseTracer(resource="langfuse:default")])
```

## Adding a New Backend

1. Create `backends/mybackend/config.py` + `client.py`
2. Create `tracers/mybackend.py` — inherit `Tracer`, implement `flush(trace_data)`
3. Register plugin in `plugin.py`, export in `__init__.py`

## trace_data.nodes Format

Pre-computed `TraceNode` list with `parent_trace_key` for simple parent lookup:
- `trace_key`, `parent_trace_key`, `display_name`, `node_type` (trace/span/generation)
- `kind` (batch/generator/stream_context/stream_item/loop_iter/graph)
- Synthetic `$ctx:` nodes group stream yields or loop iterations

## Stream Sampling

`stream_trace_limit` (default 100) caps stream_item nodes per generator before flushing.

## Feature Flags

`[langfuse]`, `[otel]`, `[all]`
