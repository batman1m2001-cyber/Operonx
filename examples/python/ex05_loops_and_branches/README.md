# 05 — Loops & Branches (Python)

Generator ops + `if_` branch routing. No API keys.

| Scenario     | Ops                          | Shape                         |
|--------------|------------------------------|-------------------------------|
| `for_loop`   | `each_item → process_item`   | Generator sequential.         |
| `map_op`     | `each_number → square`       | Generator parallel fan-out.   |
| `while_loop` | `halve_until`                | Generator while loop.         |
| `branch`     | `if_ → [excellent/…/fail]`   | Conditional routing.          |

## Run

```bash
uv run python -m examples.python.ex05_loops_and_branches.demo
uv run python -m examples.python.ex05_loops_and_branches.demo --runs 20
```

Writes `examples/bench_results/ex05_loops_and_branches_python.json`.
