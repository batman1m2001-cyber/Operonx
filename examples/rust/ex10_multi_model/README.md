# 10 — Multi-Model (Rust)

Mirrors the Python `ex10_multi_model`. Requires `OPENAI_API_KEY`.

| Scenario        | Shape                                                     | Status      |
|-----------------|-----------------------------------------------------------|-------------|
| `parallel`      | Prompt → two LLMs parallel → compare                      | runs        |
| `routing`       | Classify → `if_` → route to mini or full                  | not run yet |
| `load_balanced` | Weighted model selection                                  | runs        |
| `fallback`      | `gpt-4o` with fallback to `gpt-4o-mini`                   | runs        |
| `ensemble`      | Two answers → judge                                       | runs        |

`routing` is excluded from `main.rs` until `if_()` branch dispatch
lands in the Rust scheduler.

## Project layout

```
ex10_multi_model/
├── Cargo.toml
├── README.md
├── .env.example       # OPENAI_API_KEY
├── resources.yaml     # llm:gpt-4o + llm:gpt-4o-mini
├── src/main.rs
├── graph.json
└── inputs.json
```

## Run

```bash
cp .env.example .env
cargo run --release
```
