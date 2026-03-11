# ui-hush-eyes

Standalone Rust HTTP server for trace visualization. Stores traces in SQLite and serves a web UI.

## Module Structure

```
ui-hush-eyes/
├── Cargo.toml          # Dependencies: axum, tokio, rusqlite, serde, clap
├── src/
│   ├── main.rs         # CLI entry point (clap), server startup
│   ├── config.rs       # Config struct (host, port, db_path)
│   ├── api/
│   │   ├── mod.rs      # Router setup, CORS, static file serving
│   │   ├── ingest.rs   # POST /api/ingest — receive traces
│   │   ├── query.rs    # GET /api/traces, GET /api/traces/{id}, DELETE
│   │   └── models.rs   # IngestRequest, TraceRow, QueryParams
│   └── db/
│       ├── mod.rs      # DbPool (r2d2 + rusqlite), init_db()
│       ├── schema.rs   # CREATE TABLE statements
│       ├── write.rs    # Insert trace records
│       └── read.rs     # Query traces (list, detail, stats)
├── static/             # Web UI (HTML + JS + CSS)
│   ├── index.html
│   ├── main.js
│   ├── styles.css
│   └── hush-icon-*.png
└── .gitignore          # /target
```

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ingest` | Receive trace data (from HushEyesTracer) |
| GET | `/api/traces` | List all traces (paginated) |
| GET | `/api/traces/{request_id}` | Get trace detail with all records |
| DELETE | `/api/traces/{request_id}` | Delete a specific trace |
| DELETE | `/api/traces` | Clear all traces |
| GET | `/api/db-info` | Database file path and size |

## Build & Run

```bash
# Development
cargo run

# Production
cargo build --release
./target/release/ui-hush-eyes

# Custom config
cargo run -- --host 0.0.0.0 --port 9000 --db-path /tmp/traces.db
```

Default: `http://127.0.0.1:8420`, DB at `~/.hush/ui-hush-eyes.db`

## Connection from Python

```python
from hush.telemetry import HushEyesTracer

tracer = HushEyesTracer(host="127.0.0.1", port=8420, tags=["dev"])
result = await engine.run(inputs={...}, tracer=tracer)
# Open http://localhost:8420 to view traces
```
