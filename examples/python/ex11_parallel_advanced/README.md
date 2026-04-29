# 11 — Parallel Advanced (Python)

Fan-out/fan-in, generator iteration, and partial-failure handling.
Tier 1 — pure compute, no API keys.

| Scenario          | Shape                                                 |
|-------------------|-------------------------------------------------------|
| `fan_out`         | `[sentiment, keywords, stats]` parallel → merge       |
| `iteration`       | Generator → per-item squaring, collected              |
| `partial_failure` | Generator → odd succeed, even error; both collected   |

## Project layout

```
ex11_parallel_advanced/
├── pyproject.toml    # operonx>=0.6.2 (tier 1)
├── README.md
└── main.py
```

## Run

```bash
uv sync
uv run python main.py
```
