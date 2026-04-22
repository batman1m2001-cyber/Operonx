# 04 — LLM Advanced (Rust)

Structured output, tool calling, multi-turn chat. Mirrors the Python side.

| Scenario     | Ops                              |
|--------------|----------------------------------|
| `structured` | `PromptOp → LLMOp`               |
| `tool`       | `PromptOp → LLMOp → process`     |
| `multi_turn` | `PromptOp → LLMOp → update`      |

## Rust-limited

- `process_response`: the Rust tool executor is a stub (`<computed:expr>`); the Python side uses `eval()`, which we don't replicate here. The LLM-call + tool-calls routing still runs end-to-end.

## Run

```bash
cargo run --release -p operonx --example ex04_llm_advanced
cargo run --release -p operonx --example ex04_llm_advanced -- --runs 5
```

Writes `examples/bench_results/ex04_llm_advanced_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operon.core import Operon
from examples.python.ex04_llm_advanced.workflow import build_structured_output, build_tool_calling, build_multi_turn

def dump(g):
    Operon(g, resources='resources.yaml')
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex04_llm_advanced/graph.json').write_text(
    json.dumps({'structured': dump(build_structured_output()), 'tool': dump(build_tool_calling()), 'multi_turn': dump(build_multi_turn())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
