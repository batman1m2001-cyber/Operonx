# 15 — Callbot Streaming (Rust)

Mirrors `examples/python/ex15_callbot_streaming`. No API keys.

| Scenario  | Status         |
|-----------|----------------|
| `callbot` | runs (limited) |

## Rust-runtime limitations

Three currently-unsupported features stack here:

- **Generator per-item dispatch** (`customer_audio`, `vad`, `tts`) —
  ops return accumulated lists instead of yielding one item per frame.
- **Nested `@graph`** (`llm_router`) — the Rust scheduler returns
  empty for nested `OpType::Graph` ops.
- **`engine.stream(...)`** — no streaming handle in the Rust engine.

The Rust source is shipped as a learning reference; the Python side is
the canonical implementation today.

## Project layout

```
ex15_callbot_streaming/
├── Cargo.toml
├── README.md
├── src/main.rs
├── graph.json
└── inputs.json
```

## Run

```bash
cargo run --release
```
