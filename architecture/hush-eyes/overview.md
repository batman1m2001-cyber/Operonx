# Hush Eyes — Standalone Rust Server

## Overview

Hush Eyes is a standalone Rust HTTP server for trace visualization. It receives trace data from Hush workflows via HTTP POST, stores them in SQLite, and serves a web UI for browsing and inspecting traces.

Location: `hush-eyes/src/`

## Technology Stack

| Crate | Purpose |
|-------|---------|
| [Axum](https://github.com/tokio-rs/axum) 0.8 | HTTP server and routing |
| [Tokio](https://tokio.rs/) 1.x | Async runtime |
| [rusqlite](https://github.com/rusqlite/rusqlite) 0.33 | SQLite database (bundled) |
| [clap](https://github.com/clap-rs/clap) 4.x | CLI argument parsing |
| [serde](https://serde.rs/) / serde_json | JSON serialization/deserialization |
| [tower-http](https://github.com/tower-rs/tower-http) 0.6 | CORS middleware and static file serving |
| [dirs](https://github.com/dirs-dev/dirs-rs) 6.x | Platform-specific default paths |

## Architecture

```
Hush Workflow (Python)
    │ HushEyesTracer sends POST /api/ingest
    ▼
┌─────────────────────────────────────────┐
│         Hush Eyes (Rust Server)          │
│                                          │
│  ┌──────────────────────────────────┐    │
│  │   Axum Router                    │    │
│  │   ┌─────────────────────────┐    │    │
│  │   │  /api/ingest (POST)     │    │    │
│  │   │  /api/traces (GET)      │    │    │
│  │   │  /api/traces/{id} (GET) │    │    │
│  │   │  /api/traces/{id} (DEL) │    │    │
│  │   │  /api/traces (DELETE)   │    │    │
│  │   │  /api/db-info (GET)     │    │    │
│  │   └────────────┬────────────┘    │    │
│  └────────────────┼─────────────────┘    │
│                   │                      │
│  ┌────────────────▼─────────────────┐    │
│  │   SQLite (rusqlite + WAL)        │    │
│  │   ~/.hush/traces.db              │    │
│  └──────────────────────────────────┘    │
│                                          │
│  ┌──────────────────────────────────┐    │
│  │   Static Files (tower-http)      │    │
│  │   index.html, main.js, styles.css│    │
│  └──────────────────────────────────┘    │
│                                          │
│  http://localhost:8420                    │
└──────────────────────────────────────────┘
```

## CLI Usage

```bash
# Default: http://127.0.0.1:8420, DB at ~/.hush/traces.db
cargo run

# Custom host, port, and database path
cargo run -- --host 0.0.0.0 --port 9000 --db-path /tmp/traces.db

# Production build
cargo build --release
./target/release/hush-eyes --port 8420
```

### CLI Flags

| Flag | Short | Default | Env Var | Description |
|------|-------|---------|---------|-------------|
| `--host` | - | `127.0.0.1` | - | Host to bind to |
| `--port` | `-p` | `8420` | - | Port to listen on |
| `--db-path` | `-d` | `~/.hush/traces.db` | `HUSH_TRACES_DB` | Path to SQLite database |

If `--db-path` is not specified and `HUSH_TRACES_DB` is not set, the server defaults to `~/.hush/traces.db` and creates the directory if needed.

## Module Structure

```
hush-eyes/
├── Cargo.toml              # Dependencies and build config
├── src/
│   ├── main.rs             # Entry point: parse CLI args, init DB, start server
│   ├── config.rs           # Config struct (clap): host, port, db_path
│   ├── api/
│   │   ├── mod.rs          # Router setup, CORS layer, static file fallback
│   │   ├── ingest.rs       # POST /api/ingest handler
│   │   ├── query.rs        # GET/DELETE handlers for traces and db-info
│   │   └── models.rs       # Request/response types (IngestRequest, TraceNode, etc.)
│   └── db/
│       ├── mod.rs          # DbPool type (Arc<Mutex<Connection>>), init_db()
│       ├── schema.rs       # CREATE TABLE and CREATE INDEX statements
│       ├── write.rs        # ingest_traces() — batch insert within transaction
│       └── read.rs         # list_traces(), get_trace_detail(), tree building, delete
└── static/                 # Web UI served at root path
    ├── index.html          # Main HTML page
    ├── main.js             # Client-side JavaScript
    ├── styles.css           # Styles
    └── hush-icon-*.png     # Icons
```

## Entry Point (main.rs)

```rust
#[tokio::main]
async fn main() {
    let config = Config::parse();       // CLI args via clap
    let db_path = config.db_path();     // Resolve default path
    let pool = db::init_db(&db_path);   // Create tables, set WAL mode
    let app = api::router(pool, &db_path);  // Build Axum router
    // Bind and serve
    axum::serve(listener, app).await;
}
```

## Database Initialization

`db::init_db()` opens (or creates) the SQLite database with these pragmas:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
```

The `DbPool` type is `Arc<Mutex<Connection>>`, providing thread-safe access from Axum handlers.

## Connection with hush-core

`HushEyesTracer` (from `hush.core.tracing`) is the Python-side client that sends trace data to the server:

```python
from hush.core.tracing import HushEyesTracer

tracer = HushEyesTracer(host="127.0.0.1", port=8420, tags=["dev"])
result = await engine.run(inputs={...}, tracer=tracer)
# Open http://localhost:8420 to view traces
```

The data flow:
1. `engine.run()` completes execution
2. `FlushWorker` calls `TraceCollector.collect()` to gather trace data from state
3. `FlushWorker` calls `HushEyesTracer.flush(trace_data)` in a background thread
4. `HushEyesTracer` sends HTTP POST to `http://{host}:{port}/api/ingest`
5. Hush Eyes server stores the trace in SQLite
6. Web UI at `http://localhost:8420` displays traces

## See Also

- [API and Storage](api-and-storage.md) - REST endpoints, SQLite schema, request/response models
- [Tracing Overview](../tracing/overview.md) - TraceCollector, FlushWorker, Tracer base class
- [External Backends](../tracing/external-backends.md) - Langfuse and OTEL integration
