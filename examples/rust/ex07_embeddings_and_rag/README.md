# 07 — Embeddings & RAG (Rust)

Mirrors the Python `ex07_embeddings_and_rag`. Only the plain `retrieve`
op is declared in Rust; `EmbeddingOp`, `PromptOp`, `LLMOp`, `RerankOp`
are runtime-built provider ops.

| Scenario | Ops                                                       |
|----------|-----------------------------------------------------------|
| `embed`  | `EmbeddingOp`                                             |
| `rag`    | `EmbeddingOp → retrieve → PromptOp → LLMOp`               |
| `rerank` | `RerankOp → PromptOp → LLMOp`                             |

Requires `OPENAI_API_KEY` and `resources.yaml` with `embedding:openai`
+ `llm:gpt-4o-mini`. The rerank scenario also needs `reranker:bge-m3`.

## Project layout

```
ex07_embeddings_and_rag/
├── Cargo.toml         # operonx + inventory + serde_json
├── README.md
├── .env.example       # OPENAI_API_KEY
├── resources.yaml     # embedding:openai + llm:gpt-4o-mini (+ optional reranker:bge-m3)
├── src/main.rs        # retrieve #[op] + run loop
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
regenerate, see `tools/dump-graph.py` at the repo root.
