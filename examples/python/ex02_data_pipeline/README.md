# 02 — Data Pipeline (Python)

Two pure-compute pipelines, no API keys. Demonstrates linear ops chained through a graph.

| Scenario | Ops                                    | Shape             |
|----------|----------------------------------------|-------------------|
| `data`   | `fetch_data → transform → aggregate`   | 3 nodes in series |
| `text`   | `clean_text → count_words → summarize` | 3 nodes in series |

## Run

```bash
uv run python -m examples.python.ex02_data_pipeline.demo
uv run python -m examples.python.ex02_data_pipeline.demo --runs 20
uv run python -m examples.python.ex02_data_pipeline.demo --langfuse
```

Writes `examples/bench_results/ex02_data_pipeline_python.json`.
