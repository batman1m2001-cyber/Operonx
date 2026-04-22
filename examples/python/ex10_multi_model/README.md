# 10 — Multi-Model (Python)

Patterns for running multiple LLMs together. Requires `OPENAI_API_KEY`.

| Scenario        | Shape                                                                 |
|-----------------|-----------------------------------------------------------------------|
| `parallel`      | Same prompt → `gpt-4o` + `gpt-4o-mini` in parallel → compare.         |
| `routing`       | Classify → `if_` → route to `gpt-4o-mini` or `gpt-4o`.                |
| `load_balanced` | Weighted model selection (70/30).                                     |
| `fallback`      | `gpt-4o` with fallback to `gpt-4o-mini`.                              |
| `ensemble`      | Two answers + judge picks the better one.                             |

## Run

```bash
uv run python -m examples.python.ex10_multi_model.demo
uv run python -m examples.python.ex10_multi_model.demo --runs 3
uv run python -m examples.python.ex10_multi_model.demo --langfuse
```

Writes `examples/bench_results/ex10_multi_model_python.json`.
