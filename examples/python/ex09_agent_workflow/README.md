# 09 — Agent Workflow (Python)

Tool-calling agent driven by `@graph.loop`. Requires `OPENAI_API_KEY`.

| Scenario   | Example query                                                     |
|------------|-------------------------------------------------------------------|
| `calc`     | `What is 25 * 4 + 100?` — exercises `calculator` tool             |
| `search`   | `Tell me about Python programming language.` — exercises `search` |
| `combined` | Combined math + search in one turn                                |

The agent body is identical across scenarios; only inputs vary.

## Run

```bash
uv run python -m examples.python.ex09_agent_workflow.demo
uv run python -m examples.python.ex09_agent_workflow.demo --runs 5
uv run python -m examples.python.ex09_agent_workflow.demo --langfuse
```

Writes `examples/bench_results/ex09_agent_workflow_python.json`.
