# 11 — Parallel Advanced (Rust)

Mirrors `examples/python/ex11_parallel_advanced`. No API keys.

| Scenario          | Shape                                            | Status         |
|-------------------|--------------------------------------------------|----------------|
| `fan_out`         | `[sentiment, keywords, stats]` parallel → merge  | runs           |
| `iteration`       | Generator → per-item squaring                    | runs (limited) |
| `partial_failure` | Generator → odd/even branching                   | runs (limited) |

## Rust-runtime limitation

Generator ops (`each_item`) accumulate the list and return single-shot;
the Rust streaming scheduler does not yet fan out per yield. Downstream
ops see a list rather than one element per frame.

## Project layout

```
ex11_parallel_advanced/
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
