# hush-serve

Standalone Rust HTTP server for Hush workflows. Pure Rust — Axum + hush-icore.

[![crates.io](https://img.shields.io/crates/v/hush-serve)](https://crates.io/crates/hush-serve)

## Installation

```bash
cargo install hush-serve
```

## Usage

### Spawned by Python (typical)

```python
from hush.serve import HushApp

app = HushApp()
app.endpoint("/double", graph=graph)
app.serve(backend="rust")  # Auto-spawns hush-serve
```

### Standalone CLI

```bash
hush-serve --config config.json
hush-serve --config config.json --host 0.0.0.0 --port 8080
hush-serve --config config.json --plugin /path/to/libexample_ops.so
```

## Routes

Each endpoint generates:

| Route | Description |
|-------|-------------|
| `POST /path` | JSON request/response |
| `POST /path/stream` | SSE streaming |
| `WS /path/ws` | WebSocket |
| `GET /health` | Health check |
| `GET /endpoints` | Endpoint listing |

## Architecture

- Fresh `Hush` engine per request (no mutable state leaks)
- `tokio::spawn_blocking` for workflow execution
- Plugin ops loaded via `libloading` at startup

## License

Apache 2.0
