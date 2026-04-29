# 14 — Streaming & Tracing (Rust)

Mirrors `examples/python/ex14_streaming_tracing`. No API keys.

| Scenario        | Status         |
|-----------------|----------------|
| `text`          | runs (limited) |
| `async_counter` | runs (limited) |

## Rust-runtime limitations

- **Generator ops** — per-item dispatch is not yet implemented in the
  Rust scheduler. `chunk_text` and `async_counter` accumulate and
  return single-shot, so downstream ops see a list rather than one
  frame at a time.
- **Streaming handle** — the Rust engine does not yet expose
  `engine.start(...)` for real-time frame delivery.

## Project layout

```
ex14_streaming_tracing/
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
