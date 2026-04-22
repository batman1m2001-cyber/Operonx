# 13 — @graph (Rust)

Modular, reusable workflow components via `@graph`. Mirrors the Python side.

| Scenario       | Description                                                    | Status       |
|----------------|----------------------------------------------------------------|--------------|
| `basic`        | `@graph` basic — auto-naming + `>> END` forwarding.            | Rust-limited |
| `chained`      | Three `double_flow` instances chained.                         | Rust-limited |
| `renamed`      | Output renaming.                                               | Rust-limited |
| `multi_params` | `@graph` taking two parameters.                                | Rust-limited |
| `nested`       | `quad_flow` = `double_flow(double_flow(x))`.                   | Rust-limited |

## Rust-limited

- **Nested `@graph` composition**: the Rust scheduler currently returns empty for `OpType::Graph`. All scenarios will effectively short-circuit at the nested graph boundary until nested graph dispatch lands.

## Run

```bash
cargo run --release -p operonx --example ex13_graph
cargo run --release -p operonx --example ex13_graph -- --runs 20
```

Writes `examples/bench_results/ex13_graph_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operon.core import Operon
from examples.python.ex13_graph.workflow import build_basic, build_chained, build_renamed, build_multi_params, build_nested

def dump(g):
    Operon(g, resources='resources.yaml')
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex13_graph/graph.json').write_text(
    json.dumps({'basic': dump(build_basic()), 'chained': dump(build_chained()), 'renamed': dump(build_renamed()), 'multi_params': dump(build_multi_params()), 'nested': dump(build_nested())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
