# 12 — RAG Advanced (Rust)

Keyword RRF + hybrid (vector + keyword) RAG. Mirrors
`examples/python/ex12_rag_advanced`.

| Scenario      | Status         | Notes                                         |
|---------------|----------------|-----------------------------------------------|
| `keyword_rrf` | runs           | Pure compute                                  |
| `hybrid`      | runs (limited) | Doc vectors stubbed to zeros — see below      |

## Rust-runtime limitation

The Rust demo doesn't pre-compute `doc_vectors`; it injects zero
vectors so the keyword arm of the hybrid graph still contributes. For
full hybrid behaviour, run the Python side or wire up your own
precompute step.

## Project layout

```
ex12_rag_advanced/
├── Cargo.toml
├── README.md
├── .env.example       # OPENAI_API_KEY (for hybrid)
├── resources.yaml     # embedding:openai + llm:gpt-4o-mini
├── src/main.rs
├── graph.json
└── inputs.json
```

## Run

```bash
cp .env.example .env  # only for hybrid
cargo run --release
```
