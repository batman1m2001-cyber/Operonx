# 09 — Agent Workflow (Rust)

Tool-calling agent mirroring `examples/python/ex09_agent_workflow`.
Requires `OPENAI_API_KEY`.

| Scenario   | Tool exercised    | Status         |
|------------|-------------------|----------------|
| `calc`     | `calculator`      | runs (limited) |
| `search`   | `search`          | runs (limited) |
| `combined` | both              | runs (limited) |

## Rust-runtime limitations

- **`@graph.loop`** — the Rust engine returns empty for `OpType::Graph`
  loop wrappers, so the agent loop will not iterate. The Python side
  loops until `done == True`; Rust currently stops after one pass.
- **`calculator` tool** — Rust ships a stub returning `<computed:...>`
  rather than a real evaluator. Python uses
  `eval(..., {"__builtins__": {}}, {})`.

## Project layout

```
ex09_agent_workflow/
├── Cargo.toml
├── README.md
├── .env.example       # OPENAI_API_KEY
├── resources.yaml     # llm:gpt-4o-mini
├── src/main.rs
├── graph.json
└── inputs.json
```

## Run

```bash
cp .env.example .env
cargo run --release
```
