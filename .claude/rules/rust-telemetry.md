---
paths: ["rust/hush-telemetry/**"]
---

# hush-telemetry (Rust)

Tracing backends for Rust hush-icore. Mirrors Python hush-telemetry.

## Module Structure

```
src/
├── lib.rs              # Re-exports
├── hush_eyes.rs        # HushEyesTracer — HTTP POST to localhost:8420
├── langfuse/
│   ├── mod.rs          # LangfuseTracer — batch REST API (Basic auth, no SDK)
│   ├── config.rs       # LangfuseConfig
│   └── client.rs       # LangfuseClient (reqwest)
└── otel/               # Feature-gated: "otel"
    ├── mod.rs          # OtelTracer — OTLP export
    ├── config.rs       # OtelConfig
    └── client.rs       # OtelClient (opentelemetry SDK)
```

## Tracers

- **HushEyesTracer**: Local, HTTP POST to `127.0.0.1:8420/api/ingest`. Silent on failure.
- **LangfuseTracer**: Cloud, REST API with Basic auth. Pure HTTP, no SDK.
- **OtelTracer**: Vendor-neutral OTLP export (Jaeger, Zipkin, Tempo).

## Config in hush-serve

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
