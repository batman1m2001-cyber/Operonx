# rush-telemetry

External tracing backend implementations for rush-core. Mirrors Python's `hush-telemetry` package. All tracers implement `rush_core::tracing::Tracer`.

## Module Structure

```
rush-telemetry/src/
├── lib.rs              # Crate root, re-exports
├── hush_eyes.rs        # HushEyesTracer — HTTP POST to ui-hush-eyes
└── langfuse/
    ├── mod.rs          # LangfuseTracer — batch REST API ingestion
    ├── config.rs       # LangfuseConfig (keys, host)
    └── client.rs       # LangfuseClient (reqwest, Basic auth)
└── otel/               # Feature-gated: "otel"
    ├── mod.rs          # OtelTracer — OpenTelemetry span export
    ├── config.rs       # OtelConfig (endpoint, protocol, headers)
    └── client.rs       # OtelClient (opentelemetry SDK wrapper)
```

## Tracers

### HushEyesTracer

Local trace visualization. Posts trace_data JSON to `http://127.0.0.1:8420/api/ingest`. Silent on connection failure (server may not be running).

```rust
use rush_telemetry::HushEyesTracer;

let tracer = HushEyesTracer::new(None, None, vec![]);
engine.add_tracer(tracer);
```

### LangfuseTracer

Sends traces to Langfuse via public REST API (`/api/public/ingestion`). No SDK dependency — pure HTTP with Basic auth. Converts TraceNodes to trace-create/span-create/generation-create events.

```rust
use rush_telemetry::langfuse::{LangfuseTracer, config::LangfuseConfig};

let config = LangfuseConfig::from_env().unwrap();
let tracer = LangfuseTracer::new(config, Some(100));
engine.add_tracer(tracer);
```

### OtelTracer (feature = "otel")

Vendor-neutral trace export to any OTLP-compatible backend (Jaeger, Zipkin, Tempo, etc.).

```rust
use rush_telemetry::otel::{OtelTracer, config::OtelConfig};

let config = OtelConfig::jaeger("localhost", 4317);
let tracer = OtelTracer::new(config, Some(100));
engine.add_tracer(tracer);
```

## Usage with rush-serve

Add a `tracers` section to the server config JSON:

```json
{
  "host": "0.0.0.0",
  "port": 8080,
  "tracers": {
    "hush_eyes": { "host": "127.0.0.1", "port": 8420 },
    "langfuse": { "host": "https://cloud.langfuse.com" }
  },
  "endpoints": [...]
}
```

Langfuse keys can be in config or env vars (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`).

## Dependencies

| Crate | Purpose |
|-------|---------|
| `rush-core` | Tracer trait |
| `reqwest 0.12` | HTTP client (blocking mode) |
| `serde / serde_json` | JSON serialization |
| `base64 0.22` | Langfuse Basic auth encoding |
| `uuid 1` | Event ID generation |
| `chrono 0.4` | Timestamp handling |
| `opentelemetry 0.28` | OTEL SDK (optional, feature = "otel") |
| `opentelemetry_sdk 0.28` | OTEL tracer provider (optional) |
| `opentelemetry-otlp 0.28` | OTLP exporter (optional) |

## Feature Flags

- `default` — HushEyes + Langfuse (no OTEL)
- `otel` — Adds OpenTelemetry tracer

## Build & Test

```bash
# Default (HushEyes + Langfuse)
cargo check -p rush-telemetry

# With OTEL
cargo check -p rush-telemetry --features otel

# Full workspace
cargo test --workspace
```
