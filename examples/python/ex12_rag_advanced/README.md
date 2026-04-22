# 12 — RAG Advanced (Python)

Keyword RRF (pure compute) + hybrid (vector + keyword) RAG.

| Scenario      | Shape                                                            | Needs key?              |
|---------------|------------------------------------------------------------------|-------------------------|
| `keyword_rrf` | Two keyword searches in parallel → RRF merge                     | No                      |
| `hybrid`      | `[keyword, EmbeddingOp → vec_search]` → merge → prompt → LLM     | `OPENAI_API_KEY`        |

## Run

```bash
uv run python -m examples.python.ex12_rag_advanced.demo
uv run python -m examples.python.ex12_rag_advanced.demo --runs 5
```

Writes `examples/bench_results/ex12_rag_advanced_python.json`.
