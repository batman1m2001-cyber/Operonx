# hush-serve

Auto-generate HTTP API servers from Hush workflow graphs. Built on FastAPI with support for sync, SSE streaming, WebSocket, batch, and background job endpoints. Dual-backend: Python (FastAPI/uvicorn) or Rust (spawns rush-serve binary).

## Module Structure

```
hush-serve/hush/serve/
├── __init__.py          # Public API: HushApp
├── app.py               # HushApp — register endpoints, build FastAPI, serve()
├── config.py            # EndpointConfig, AppConfig (Pydantic)
├── endpoint.py          # Endpoint — binds GraphOp to route config + Hush engine
├── schema.py            # Auto-generate Pydantic request/response models from graph metadata
├── middleware.py         # RequestIDMiddleware, TimingMiddleware
├── jobs.py              # In-memory JobStore with TTL cleanup
├── errors.py            # Error handling
├── _rust_bridge.py      # backend="rust" — serialize config, spawn rush-serve binary
└── routes/
    ├── sync_handler.py    # POST /path → JSON result
    ├── stream_handler.py  # POST /path/stream → SSE text/event-stream
    ├── ws_handler.py      # WS /path/ws → bidirectional WebSocket
    ├── batch_handler.py   # POST /path/batch → concurrent batch execution
    └── job_handler.py     # POST /path/submit + GET /jobs/{job_id}
```

## Key Files to Read First

1. `app.py` — `HushApp` class: endpoint registration, FastAPI builder, `serve()` entry point
2. `endpoint.py` — `Endpoint`: binds a `GraphOp` to config, creates `Hush` engine
3. `schema.py` — Auto-generates Pydantic models from `graph.inputs`/`graph.outputs`
4. `_rust_bridge.py` — `backend="rust"` path: serialize + spawn rush-serve

## Architecture

### Dual Backend

```
HushApp.serve(backend="python")     HushApp.serve(backend="rust")
         │                                    │
         ▼                                    ▼
    FastAPI + uvicorn                  serialize_for_rust()
    (Python handlers)                         │
                                              ▼
                                       spawn rush-serve binary
                                       (Axum, pure Rust)
```

### Route Generation

Each `app.endpoint("/path", graph=g)` auto-generates:

| Route | Handler | Condition |
|-------|---------|-----------|
| `POST /path` | sync_handler | Always |
| `POST /path/stream` | stream_handler | `stream=True` or auto-detected |
| `WS /path/ws` | ws_handler | `websocket=True` |
| `POST /path/batch` | batch_handler | `batch=True` (default) |
| `POST /path/submit` | job_handler | `jobs=True` |
| `GET /jobs/{job_id}` | job_handler | Any endpoint has `jobs=True` |

Plus system routes: `GET /health`, `GET /endpoints`.

### Auto-Schema

`schema.py` reads `graph.inputs` / `graph.outputs` Param definitions and generates:
- **Request model**: Pydantic model with field types, defaults, descriptions
- **Response model**: Pydantic model for OpenAPI docs
- **Schema info**: JSON dict for `/endpoints` listing

## Usage

```python
from hush.core import GraphOp, op, START, END, PARENT
from hush.serve import HushApp

@op
def double(x: int):
    return {"result": x * 2}

with GraphOp(name="doubler") as graph:
    step = double(x=PARENT["x"])
    START >> step >> END

app = HushApp()
app.endpoint("/double", graph=graph)

# Python backend (default)
app.serve(port=8000)

# Rust backend (spawns rush-serve binary)
app.serve(port=8000, backend="rust")
```

### Decorator Style

```python
@app.endpoint("/chat")
@graph
def chatbot(query):
    llm = chain(resource="gpt-4o", template={"user": "{query}"}, query=query)
    START >> llm >> END
```

## Dependencies

- `hush-core` — workflow engine
- `fastapi` — HTTP framework
- `uvicorn` — ASGI server
- `websockets` — WebSocket support

Optional: `hush-providers` (LLM ops), `hush-telemetry` (tracing)

## Build & Test

```bash
cd hush-serve
uv sync --all-extras
uv run -m pytest
```

## Deep Documentation Links

| Topic | File |
|-------|------|
| Core engine | [hush-core/CLAUDE.md](../hush-core/CLAUDE.md) |
| Rust backend | [rush-serve/CLAUDE.md](../../rust/rush-serve/CLAUDE.md) |
| Provider ops | [hush-providers/CLAUDE.md](../hush-providers/CLAUDE.md) |
