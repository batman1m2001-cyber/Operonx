# 02 — Data Pipeline (Rust)

Two pure-compute pipelines mirroring the Python `ex02_data_pipeline`.
No API keys.

| Scenario | Ops                                    | Shape          |
|----------|----------------------------------------|----------------|
| `data`   | `fetch_data → transform → aggregate`   | 3 nodes linear |
| `text`   | `clean_text → count_words → summarize` | 3 nodes linear |

## Project layout

```
ex02_data_pipeline/
├── Cargo.toml         # operonx + inventory + serde_json
├── src/main.rs        # #[op] declarations + run loop
├── graph.json         # graph specs (one per scenario)
└── inputs.json        # illustrative inputs
```

## Run

```bash
cargo run --release
```

## Authoring graph specs

`graph.json` was generated from the matching Python builders. To
regenerate after editing Python ops, see `tools/dump-graph.py` at the
repo root.
