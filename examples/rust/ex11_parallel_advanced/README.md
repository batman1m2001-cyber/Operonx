# 11 — Parallel Advanced (Rust)

Mirrors the Python side.

| Scenario          | Shape                                            | Status       |
|-------------------|--------------------------------------------------|--------------|
| `fan_out`         | `[sentiment, keywords, stats]` parallel → merge  | OK           |
| `iteration`       | Generator → per-item squaring                    | Rust-limited |
| `partial_failure` | Generator → odd/even branching                   | Rust-limited |

## Rust-limited

- **Generator ops**: the Rust scheduler does not yet dispatch per-item yields. `each_item` returns the collected list as a single-shot value, so downstream ops see a list rather than one element per frame.

## Run

```bash
cargo run --release -p operonx --example ex11_parallel_advanced
cargo run --release -p operonx --example ex11_parallel_advanced -- --runs 20
```

Writes `examples/bench_results/ex11_parallel_advanced_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operonx.core import Operon
from examples.python.ex11_parallel_advanced.workflow import build_fan_out, build_iteration, build_partial_failure

def dump(g):
    Operon(g, resources='resources.yaml')
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex11_parallel_advanced/graph.json').write_text(
    json.dumps({'fan_out': dump(build_fan_out()), 'iteration': dump(build_iteration()), 'partial_failure': dump(build_partial_failure())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
