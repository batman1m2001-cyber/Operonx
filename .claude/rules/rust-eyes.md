---
paths: ["rust/ui-hush-eyes/**"]
---

# ui-hush-eyes

Standalone trace visualization server. Axum + SQLite.

## Module Structure

```
src/
├── main.rs         # CLI (clap), server startup
├── config.rs       # host, port, db_path
├── api/
│   ├── ingest.rs   # POST /api/ingest — receive traces
│   ├── query.rs    # GET /api/traces, GET /api/traces/{id}, DELETE
│   └── models.rs   # IngestRequest, TraceRow, QueryParams
└── db/
    ├── schema.rs   # CREATE TABLE
    ├── write.rs    # Insert trace records
    └── read.rs     # Query traces
```

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ingest` | Receive trace data |
| GET | `/api/traces` | List all traces (paginated) |
| GET | `/api/traces/{id}` | Trace detail |
| DELETE | `/api/traces/{id}` | Delete trace |
| DELETE | `/api/traces` | Clear all |
| GET | `/api/db-info` | DB path and size |

Default: `http://127.0.0.1:8420`, DB at `~/.hush/ui-hush-eyes.db`
