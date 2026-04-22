# 09 — Agent Workflow (Rust)

Tool-calling agent mirroring the Python side. Requires `OPENAI_API_KEY`.

| Scenario   | Tool exercised    | Status       |
|------------|-------------------|--------------|
| `calc`     | `calculator`      | Rust-limited |
| `search`   | `search`          | Rust-limited |
| `combined` | both              | Rust-limited |

## Rust-limited

- **`@graph.loop`**: the Rust engine returns empty for `OpType::Graph` loop wrappers, so the agent loop will not iterate (Python's loop body runs until `done == True`; Rust will stop at the nested graph without looping).
- **Calculator tool**: Rust does not ship a safe expression evaluator, so the `calculator` tool returns a placeholder `<computed:...>` string. Python uses `eval(..., {"__builtins__": {}}, {})`.

## Run

```bash
cargo run --release -p operonx --example ex09_agent_workflow
cargo run --release -p operonx --example ex09_agent_workflow -- --runs 5
```

Writes `examples/bench_results/ex09_agent_workflow_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operon.core import Operon
from examples.python.ex09_agent_workflow.workflow import build_agent

def dump(g):
    Operon(g, resources='resources.yaml')
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

g = dump(build_agent())
pathlib.Path('examples/rust/ex09_agent_workflow/graph.json').write_text(
    json.dumps({'calc': g, 'search': g, 'combined': g}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
