# hush-telemetry

Rust tracing backends for Hush workflows — HushEyes, Langfuse, and OpenTelemetry.

[![crates.io](https://img.shields.io/crates/v/hush-telemetry)](https://crates.io/crates/hush-telemetry)

## Tracers

| Tracer | Description |
|--------|-------------|
| **HushEyesTracer** | HTTP POST to local ui-hush-eyes server |
| **LangfuseTracer** | REST API with Basic auth (no SDK dependency) |
| **OtelTracer** | OTLP export to Jaeger, Zipkin, Tempo, etc. |

## Usage

Configured via hush-serve config JSON:

```json
{
  "tracers": {
    "hush_eyes": { "host": "127.0.0.1", "port": 8420 },
    "langfuse": { "host": "https://cloud.langfuse.com" }
  }
}
```

## Feature Flags

- `default` — HushEyes + Langfuse
- `otel` — Adds OpenTelemetry

## License

Apache 2.0
