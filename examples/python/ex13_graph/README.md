# 13 — @graph (Python)

Modular, reusable workflow components via `@graph`. No API keys.

| Scenario       | Description                                                    |
|----------------|----------------------------------------------------------------|
| `basic`        | `@graph` basic — auto-naming + `>> END` forwarding.            |
| `chained`      | Three `double_flow` instances chained (3 → 6 → 12 → 24).       |
| `renamed`      | Output renaming via `op["key"] >> PARENT["new_key"]`.          |
| `multi_params` | `@graph` taking two parameters.                                |
| `nested`       | `quad_flow` = `double_flow(double_flow(x))`.                   |

## Run

```bash
uv run python -m examples.python.ex13_graph.demo
uv run python -m examples.python.ex13_graph.demo --runs 20
```

Writes `examples/bench_results/ex13_graph_python.json`.
