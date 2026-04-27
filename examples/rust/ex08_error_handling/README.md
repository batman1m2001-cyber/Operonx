# 08 — Error Handling (Rust)

Mirrors the Python side.

| Scenario       | Ops                                                  | Status       |
|----------------|------------------------------------------------------|--------------|
| `capture`      | `failing`                                            | OK           |
| `routing`      | `safe_divide → if_ → handle_success / handle_error` | Rust-limited |
| `retry`        | `retry_with_backoff → with_fallback`                 | OK           |
| `llm_fallback` | `PromptOp → LLMOp`                                   | OK           |

## Rust-limited

- **Branch ops (`if_`)**: `OpType::Branch` is stubbed in the Rust scheduler — the `routing` scenario will fail until branch dispatch lands.

## Run

```bash
cargo run --release -p operonx --example ex08_error_handling
cargo run --release -p operonx --example ex08_error_handling -- --runs 20
```

Writes `examples/bench_results/ex08_error_handling_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operonx.core import Operon
from examples.python.ex08_error_handling.workflow import build_error_capture, build_error_routing, build_retry_fallback, build_llm_fallback

def dump(g):
    Operon(g, resources='resources.yaml')
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex08_error_handling/graph.json').write_text(
    json.dumps({'capture': dump(build_error_capture()), 'routing': dump(build_error_routing()), 'retry': dump(build_retry_fallback()), 'llm_fallback': dump(build_llm_fallback())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
