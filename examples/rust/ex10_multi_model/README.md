# 10 — Multi-Model (Rust)

Mirrors the Python side. Requires `OPENAI_API_KEY`.

| Scenario        | Shape                                                     | Status       |
|-----------------|-----------------------------------------------------------|--------------|
| `parallel`      | Prompt → two LLMs parallel → compare                      | OK           |
| `routing`       | Classify → `if_` → route to mini or full model            | Rust-limited |
| `load_balanced` | Weighted model selection                                  | OK           |
| `fallback`      | `gpt-4o` with fallback to `gpt-4o-mini`                   | OK           |
| `ensemble`      | Two answers → judge                                       | OK           |

## Rust-limited

- **`routing`** depends on `OpType::Branch` (`if_`) — stubbed in the Rust scheduler; scenario will fail until branch dispatch lands.

## Run

```bash
cargo run --release -p operonx --example ex10_multi_model
cargo run --release -p operonx --example ex10_multi_model -- --runs 3
```

Writes `examples/bench_results/ex10_multi_model_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operonx.core import Operon
from examples.python.ex10_multi_model.workflow import build_parallel_comparison, build_cost_routing, build_load_balanced, build_fallback, build_ensemble

def dump(g):
    Operon(g, resources='resources.yaml')
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex10_multi_model/graph.json').write_text(
    json.dumps({'parallel': dump(build_parallel_comparison()), 'routing': dump(build_cost_routing()), 'load_balanced': dump(build_load_balanced()), 'fallback': dump(build_fallback()), 'ensemble': dump(build_ensemble())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
