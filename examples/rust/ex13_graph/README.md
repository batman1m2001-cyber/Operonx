# 13 — @graph (Rust)

Modular, reusable workflow components via `@graph`. Mirrors
`examples/python/ex13_graph`. No API keys.

| Scenario       | Status       |
|----------------|--------------|
| `basic`        | runs (limited) |
| `chained`      | runs (limited) |
| `renamed`      | runs (limited) |
| `multi_params` | runs (limited) |
| `nested`       | runs (limited) |

## Rust-runtime limitation

Nested `@graph` composition: the Rust scheduler currently returns empty
for nested `OpType::Graph` ops, so every scenario short-circuits at the
nested-graph boundary until nested-graph dispatch lands. Python runs
all five scenarios end-to-end.

## Project layout

```
ex13_graph/
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
