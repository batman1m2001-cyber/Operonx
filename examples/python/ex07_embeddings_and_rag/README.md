# 07 — Embeddings & RAG (Python)

Embedding-only, simple RAG with cosine search, and optional RAG with reranker.

| Scenario | Ops                                                                 | Notes                                      |
|----------|---------------------------------------------------------------------|--------------------------------------------|
| `embed`  | `EmbeddingOp`                                                       | Plain embedding of two short texts.        |
| `rag`    | `EmbeddingOp → retrieve → PromptOp → LLMOp`                         | Cosine search against pre-embedded docs.   |
| `rerank` | `RerankOp → PromptOp → LLMOp`                                       | Uses a `bge-m3` reranker resource.         |

Requires `OPENAI_API_KEY` in `.env`. The `rerank` scenario additionally needs a reranker resource (e.g. `bge-m3`) exposed in `resources.yaml`.

## Run

```bash
uv run python -m examples.python.ex07_embeddings_and_rag.demo
uv run python -m examples.python.ex07_embeddings_and_rag.demo --runs 5
```

Writes `examples/bench_results/ex07_embeddings_and_rag_python.json`.
