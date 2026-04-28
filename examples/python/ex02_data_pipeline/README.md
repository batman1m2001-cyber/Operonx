# 02 — Data Pipeline (Python)

Two tiny pure-compute pipelines, no API keys. Demonstrates linear
chains of ops feeding output of one into input of the next.

| Scenario | Ops                                    | Shape          |
|----------|----------------------------------------|----------------|
| `data`   | `fetch_data → transform → aggregate`   | 3 nodes linear |
| `text`   | `clean_text → count_words → summarize` | 3 nodes linear |

## Project layout

```
ex02_data_pipeline/
├── pyproject.toml    # depends only on operonx (tier 1, no providers)
├── README.md
└── main.py           # ops + @graph factories + asyncio.run
```

## Run

```bash
uv sync
uv run python main.py
```
