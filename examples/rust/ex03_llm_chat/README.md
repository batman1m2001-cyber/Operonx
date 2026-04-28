# 03 — LLM Chat (Rust)

Three LLM chat graphs mirroring the Python `ex03_llm_chat`. Requires
`OPENAI_API_KEY` in `.env` and `llm:gpt-4o-mini` in `resources.yaml`
(both shipped alongside this crate).

| Scenario    | Ops                                 | Notes                           |
|-------------|-------------------------------------|---------------------------------|
| `basic`     | `PromptOp → LLMOp`                  | Explicit two-op form            |
| `chain`     | `chat()`                            | Single-op all-in-one helper     |
| `summarize` | `clean_text → PromptOp → LLMOp`     | Pre-processing + summarization  |

`PromptOp` and `LLMOp` are provider ops — the Rust engine builds them
at runtime from `graph.json`. Only plain `#[op]`s are declared in
`src/main.rs`.

## Project layout

```
ex03_llm_chat/
├── Cargo.toml         # operonx + inventory + serde_json
├── README.md
├── .env.example       # OPENAI_API_KEY
├── resources.yaml     # llm:gpt-4o-mini
├── src/main.rs        # #[op] declarations + run loop
├── graph.json         # graph specs (one per scenario)
└── inputs.json        # illustrative inputs
```

## Run

```bash
cp .env.example .env  # fill in OPENAI_API_KEY
cargo run --release
```

## Authoring graph specs

`graph.json` was generated from the matching Python builders. To
regenerate after editing Python ops, see `tools/dump-graph.py` at the
repo root.
