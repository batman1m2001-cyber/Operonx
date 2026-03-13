# hush-serve (Rust)

Standalone Rust HTTP server for Hush workflows. Pure Rust — no Python, no PyO3. Receives a JSON config (produced by `hush-serve` Python bridge) and serves workflows via Axum. Uses hush-icore engine for execution and hush-providers for native provider ops.

## Module Structure

```
hush-serve/src/
├── main.rs              # Entry point: CLI parse, config load, bind & serve
├── config.rs            # Cli (clap), ServerConfig, EndpointDef (serde)
├── router.rs            # build_router() — Axum route generation from config
├── execute.rs           # run_workflow() — spawn_blocking + Hush::new/run_json
├── state.rs             # AppState (DashMap<path, EndpointState>)
├── error.rs             # ServeError enum (Execution, Internal)
└── routes/
    ├── mod.rs             # Route module declarations
    ├── sync_handler.rs    # POST /path → JSON result
    ├── stream_handler.rs  # POST /path/stream → SSE text/event-stream
    └── ws_handler.rs      # WS /path/ws → bidirectional WebSocket
```

## Key Files to Read First

1. `main.rs` — CLI entry point: parse args, load config, start Axum server
2. `router.rs` — Route builder: iterates endpoints, creates Axum routes
3. `execute.rs` — Bridge: `tokio::spawn_blocking` → `Hush::new(json)` + `Hush::run_json(inputs)`
4. `config.rs` — `ServerConfig` / `EndpointDef` deserialization from JSON

## Architecture

### Request Flow

```
hush-serve (Python)                  hush-serve (Rust)
├── Define graphs via @graph         ├── Parse config JSON
├── Register endpoints               ├── Build Axum routes
├── serialize_for_rust() ──JSON──→   ├── For each request:
├── spawn hush-serve binary          │   ├── Hush::new(graph_json)
└── Wait for process                 │   ├── Hush::run_json(inputs)
                                     │   └── Return JSON result
                                     └── Serve HTTP + SSE + WS
```

### Execution Model

hush-icore's engine is sync/rayon-based, so Axum handlers use `tokio::spawn_blocking`:

```rust
// execute.rs
let result = tokio::task::spawn_blocking(move || {
    let engine = Hush::new(&graph_json)?;
    engine.run_json(inputs, request_id, None, None)
}).await??;
```

Each request creates a fresh `Hush` engine — no mutable state leaks between requests.

### Route Generation

Same pattern as hush-serve (Python). For each endpoint in config:

| Route | Condition |
|-------|-----------|
| `POST /path` | Always |
| `POST /path/stream` | `stream: true` |
| `WS /path/ws` | `websocket: true` |

Plus: `GET /health`, `GET /endpoints`.

## Usage

### Spawned by hush-serve (typical)

```python
app = HushApp()
app.endpoint("/double", graph=graph)
app.serve(backend="rust")  # auto-builds and spawns hush-serve
```

### Standalone CLI

```bash
hush-serve --config config.json
hush-serve --config config.json --host 0.0.0.0 --port 8080
```

Environment variables: `HUSH_SERVE_CONFIG`, `HUSH_SERVE_HOST`, `HUSH_SERVE_PORT`.

## Dependencies

| Crate | Purpose |
|-------|---------|
| `hush-icore` | Workflow engine (Hush::new + run_json) |
| `hush-providers` | Native provider implementations |
| `axum 0.8` | HTTP framework (with WebSocket support) |
| `tokio 1` | Async runtime (full features) |
| `tower-http 0.6` | CORS middleware |
| `clap 4` | CLI argument parsing |
| `serde / serde_json` | JSON serialization |
| `uuid 1` | Request ID generation |
| `chrono 0.4` | Timestamps for job tracking |
| `dashmap 6` | Concurrent endpoint state |
| `futures 0.3` | Stream utilities for SSE |

## Build & Test

```bash
cd hush-serve

# Build
cargo build --release

# Run
./target/release/hush-serve --config config.json
```

## Deep Documentation Links

| Topic | File |
|-------|------|
| Python serve layer | [python/hush-serve/CLAUDE.md](../../python/hush-serve/CLAUDE.md) |
| Rust engine | [hush-icore/CLAUDE.md](../hush-icore/CLAUDE.md) |
| Rust providers | [hush-providers/CLAUDE.md](../hush-providers/CLAUDE.md) |
