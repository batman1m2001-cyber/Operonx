# 13 — @graph (Python)

Modular, reusable workflow components via `@graph`. Tier 1 — pure
compute, no API keys.

| Scenario       | Description                                                |
|----------------|------------------------------------------------------------|
| `basic`        | `@graph` basic — auto-naming + `>> END` forwarding         |
| `chained`      | Three `double_flow` instances chained (3 → 6 → 12 → 24)    |
| `renamed`      | Output renaming via `op["key"] >> PARENT["new_key"]`       |
| `multi_params` | `@graph` taking two parameters                             |
| `nested`       | `quad_flow` = `double_flow(double_flow(x))`                |

## Project layout

```
ex13_graph/
├── pyproject.toml    # operonx>=1.3.0 (tier 1)
├── README.md
└── main.py
```

## Run

```bash
uv sync
uv run python main.py
```
