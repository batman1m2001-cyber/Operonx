---
paths: ["rust/hush-serve/**"]
---

# hush-serve (Rust)

Standalone Axum HTTP server. No Python, no PyO3.

## Module Structure

```
src/
├── main.rs              # CLI entry (clap), config load, bind & serve
├── config.rs            # ServerConfig, EndpointDef (serde)
├── router.rs            # build_router() — Axum route generation
├── execute.rs           # run_workflow() — spawn_blocking + Hush::new/run_json
├── state.rs             # AppState (DashMap<path, EndpointState>)
├── plugin.rs            # Plugin loading (libloading)
├── error.rs
└── routes/
    ├── sync_handler.rs    # POST /path → JSON
    ├── stream_handler.rs  # POST /path/stream → SSE
    └── ws_handler.rs      # WS /path/ws
```

## Request Flow

```
Python bridge                    Rust hush-serve
├── serialize_for_rust() ──JSON──→ Parse config
├── spawn hush-serve binary       ├── Build Axum routes
└── Wait for process              ├── Per request:
                                  │   ├── spawn_blocking
                                  │   ├── Hush::new(graph_json)
                                  │   ├── Hush::run_json(inputs)
                                  │   └── Return JSON
                                  └── Serve HTTP + SSE + WS
```

Fresh `Hush` engine per request — no mutable state leaks.

## CLI

```bash
hush-serve --config config.json --host 0.0.0.0 --port 8080
hush-serve --config config.json --plugin /path/to/libexample_ops.so
```

Env vars: `HUSH_SERVE_CONFIG`, `HUSH_SERVE_HOST`, `HUSH_SERVE_PORT`
