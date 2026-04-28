# 12 — RAG Advanced (Python)

Keyword RRF (pure compute) + hybrid (vector + keyword) RAG.

| Scenario      | Shape                                                            | Needs key?       |
|---------------|------------------------------------------------------------------|------------------|
| `keyword_rrf` | Two keyword searches in parallel → RRF merge                     | No               |
| `hybrid`      | `[keyword, EmbeddingOp → vec_search]` → merge → prompt → LLM     | `OPENAI_API_KEY` |

The `hybrid` scenario gracefully skips if no API key is present.

## Project layout

```
ex12_rag_advanced/
├── pyproject.toml      # operonx[providers]>=0.6.2 (numpy + openai)
├── README.md
├── .env.example        # OPENAI_API_KEY (only for hybrid)
├── resources.yaml      # embedding:openai + llm:gpt-4o-mini
└── main.py
```

## Run

```bash
uv sync
cp .env.example .env
uv run python main.py
```
