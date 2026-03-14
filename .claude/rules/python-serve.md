---
paths: ["python/hush-serve/**"]
---

# hush-serve (Python)

HTTP API server — auto-generates endpoints from Hush workflow graphs.

## Module Structure

```
hush/serve/
├── app.py              # HushApp — endpoint registration, FastAPI builder, serve()
├── endpoint.py         # Endpoint — binds GraphOp to config + Hush engine
├── schema.py           # Auto-generate Pydantic models from graph.inputs/outputs
├── config.py           # EndpointConfig, AppConfig
├── middleware.py        # RequestID, Timing middleware
├── errors.py
├── _rust_bridge.py     # backend="rust" — serialize config, find/spawn hush-serve binary
└── routes/
    ├── sync_handler.py    # POST /path → JSON
    ├── stream_handler.py  # POST /path/stream → SSE
    └── ws_handler.py      # WS /path/ws
```

## Dual Backend

```
app.serve(backend="python")  → FastAPI + uvicorn (Python handlers)
app.serve(backend="rust")    → serialize_for_rust() → spawn hush-serve binary (Axum)
```

## Route Generation

Each `app.endpoint("/path", graph=g)` creates:
- `POST /path` (always)
- `POST /path/stream` (if stream=True or auto-detected)
- `WS /path/ws` (if websocket=True)
- Plus: `GET /health`, `GET /endpoints`

## Usage

```python
app = HushApp()
app.endpoint("/double", graph=graph)
app.serve(port=8000)                    # Python
app.serve(port=8000, backend="rust")    # Rust

# Decorator style
@app.endpoint("/chat")
@graph
def chatbot(query):
    llm = chain(resource="gpt-4o", template={"user": "{query}"}, query=query)
    START >> llm >> END
```

## _rust_bridge.py

`find_hush_serve_binary()` looks for hush-serve in:
1. Monorepo `rust/target/release/hush-serve`
2. `shutil.which("hush-serve")` on PATH (for `cargo install hush-serve`)
