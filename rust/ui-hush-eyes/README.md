# hush-eyes

Standalone trace visualization server for Hush workflows. Axum + SQLite.

[![crates.io](https://img.shields.io/crates/v/hush-eyes)](https://crates.io/crates/hush-eyes)

## Installation

```bash
cargo install hush-eyes
```

## Usage

```bash
hush-eyes                                    # Default: localhost:8420
hush-eyes --host 0.0.0.0 --port 9000       # Custom bind
hush-eyes --db-path /tmp/traces.db          # Custom database
```

Open http://localhost:8420 to view traces.

## Connect from Python

```python
from hush.telemetry import HushEyesTracer

tracer = HushEyesTracer(host="127.0.0.1", port=8420, tags=["dev"])
engine = Hush(graph, tracer=tracer)
result = await engine.run(inputs={...})
```

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ingest` | Receive trace data |
| GET | `/api/traces` | List traces (paginated) |
| GET | `/api/traces/{id}` | Trace detail |
| DELETE | `/api/traces/{id}` | Delete trace |
| DELETE | `/api/traces` | Clear all |
| GET | `/api/db-info` | Database info |

## License

Apache 2.0
