# 05 — Loops & Branches (Rust)

Generator ops + `if_` branch routing. Mirrors the Python side.

| Scenario     | Ops                          | Status     |
|--------------|------------------------------|------------|
| `for_loop`   | `each_item → process_item`   | Rust-limited |
| `map_op`     | `each_number → square`       | Rust-limited |
| `while_loop` | `halve_until`                | Rust-limited |
| `branch`     | `if_ → [excellent/…/fail]`   | Rust-limited |

## Rust-limited

- **Generator ops**: the Rust scheduler does not yet dispatch generator `yield`s per item. `each_item`, `each_number`, and `halve_until` return the collected list as a single-shot value instead — so downstream ops that expect `src["item"]` may see a list rather than one element per frame.
- **Branch ops (`if_`)**: `OpType::Branch` is stubbed in Rust — running the `branch` scenario will fail until branch execution lands.

The demo source is still useful as a learning reference; just expect divergence from Python output.

## Run

```bash
cargo run --release -p operonx --example ex05_loops_and_branches
cargo run --release -p operonx --example ex05_loops_and_branches -- --runs 20
```

Writes `examples/bench_results/ex05_loops_and_branches_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operon.core import Operon
from examples.python.ex05_loops_and_branches.workflow import build_for_loop, build_map_op, build_while_loop, build_branch

def dump(g):
    Operon(g, resources='resources.yaml')
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex05_loops_and_branches/graph.json').write_text(
    json.dumps({'for_loop': dump(build_for_loop()), 'map_op': dump(build_map_op()), 'while_loop': dump(build_while_loop()), 'branch': dump(build_branch())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
