# 08 — Error Handling (Rust)

Mirrors `examples/python/ex08_error_handling`.

| Scenario       | Ops                                                  | Status        |
|----------------|------------------------------------------------------|---------------|
| `capture`      | `failing`                                            | runs          |
| `routing`      | `safe_divide → if_ → handle_success / handle_error`  | not run yet   |
| `retry`        | `retry_with_backoff → with_fallback`                 | runs          |
| `llm_fallback` | `PromptOp → LLMOp`                                   | runs (needs key) |

`routing` is excluded from `main.rs` until `if_()` branch dispatch
lands in the Rust scheduler.

## Project layout

```
ex08_error_handling/
├── Cargo.toml
├── README.md
├── .env.example       # OPENAI_API_KEY (only for llm_fallback)
├── resources.yaml     # llm:gpt-4o + llm:gpt-4o-mini
├── src/main.rs
├── graph.json
└── inputs.json
```

## Run

```bash
cargo run --release
```
