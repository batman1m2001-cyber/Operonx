# 01 — Hello World (Python)

Three tiny graphs, no API keys. Good first read if you're learning Operon's authoring DSL.

| Scenario   | Ops                       | Shape             |
|------------|---------------------------|-------------------|
| `hello`    | `greet`                   | 1 node            |
| `chain`    | `greet_en → upper`        | 2 nodes in series |
| `parallel` | `step_a + step_b → merge` | fan-out + fan-in  |

## Run

```bash
uv run python -m examples.python.ex01_hello_world.demo
uv run python -m examples.python.ex01_hello_world.demo --runs 20
uv run python -m examples.python.ex01_hello_world.demo --langfuse
```

Prints a per-scenario latency summary and writes `examples/bench_results/ex01_hello_world_python.json`.
