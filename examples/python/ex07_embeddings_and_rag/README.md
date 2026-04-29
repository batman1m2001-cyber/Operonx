# 07 — Embeddings & RAG (Python)

Embed text into vectors, run cosine search RAG, and (optionally) gate
results through a reranker.

| Scenario | Ops                                                       | Notes                                      |
|----------|-----------------------------------------------------------|--------------------------------------------|
| `embed`  | `EmbeddingOp`                                             | Plain embedding of two short texts         |
| `rag`    | `EmbeddingOp → retrieve → PromptOp → LLMOp`               | Cosine search against pre-embedded docs    |
| `rerank` | `RerankOp → PromptOp → LLMOp`                             | Uses a `bge-m3` reranker resource          |

The `rerank` scenario gracefully skips if `reranker:bge-m3` isn't
configured in `resources.yaml`.

## Project layout

```
ex07_embeddings_and_rag/
├── pyproject.toml      # operonx[providers]>=0.6.2 (tier 4 meta — pulls numpy + aiohttp)
├── README.md
├── .env.example        # OPENAI_API_KEY
├── resources.yaml      # embedding:openai + llm:gpt-4o-mini (+ optional reranker:bge-m3)
└── main.py
```

## Run

```bash
uv sync
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY
uv run python main.py
```
