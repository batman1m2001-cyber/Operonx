# 11 — Parallel Advanced (Python)

Fan-out/fan-in, generator iteration, partial-failure handling. No API keys.

| Scenario          | Shape                                                 |
|-------------------|-------------------------------------------------------|
| `fan_out`         | `[sentiment, keywords, stats]` parallel → merge       |
| `iteration`       | Generator → per-item squaring, collected              |
| `partial_failure` | Generator → even numbers error, odd succeed           |

## Run

```bash
uv run python -m examples.python.ex11_parallel_advanced.demo
uv run python -m examples.python.ex11_parallel_advanced.demo --runs 20
```

Writes `examples/bench_results/ex11_parallel_advanced_python.json`.
