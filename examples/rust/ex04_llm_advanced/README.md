# 04 — LLM Advanced (Rust)

Structured output, tool calling, multi-turn chat. Mirrors the Python
`ex04_llm_advanced`. Requires `OPENAI_API_KEY` in `.env`.

| Scenario     | Ops                              |
|--------------|----------------------------------|
| `structured` | `PromptOp → LLMOp`               |
| `tool`       | `PromptOp → LLMOp → process`     |
| `multi_turn` | `PromptOp → LLMOp → update`      |

## Rust-limited

`process_response` returns a stub `<computed:expr>` rather than running
the calculator — the Python side uses `eval()`, which we don't
replicate here. The LLM-call + tool-routing path runs end-to-end.

## Project layout

```
ex04_llm_advanced/
├── Cargo.toml         # operonx + inventory + serde_json
├── README.md
├── .env.example       # OPENAI_API_KEY
├── resources.yaml     # llm:gpt-4o-mini
├── src/main.rs        # process_response + update_history #[op]s
├── graph.json
└── inputs.json
```

## Run

```bash
cp .env.example .env
cargo run --release
```

## Authoring graph specs

`graph.json` was generated from the matching Python builders. To
regenerate after editing Python ops, see `tools/dump-graph.py` at the
repo root.
