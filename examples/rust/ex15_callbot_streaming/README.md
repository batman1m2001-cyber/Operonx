# 15 — Callbot Streaming (Rust)

Mirrors the Python side.

| Scenario  | Shape                                                              | Status       |
|-----------|--------------------------------------------------------------------|--------------|
| `callbot` | `customer_audio → vad → stt → llm_router (@graph) → tts`           | Rust-limited |

## Rust-limited

This example stacks three currently-unsupported features:

- **Generator per-item dispatch** (`customer_audio`, `vad`, `tts`) — ops return accumulated lists instead of yielding one item per frame.
- **Nested `@graph`** (`llm_router`) — the Rust scheduler returns empty for `OpType::Graph`.
- **`engine.stream(...)`** — no streaming handle in the Rust engine yet.

The demo source is provided as a learning reference so you can port it once these features land.

## Run

```bash
cargo run --release -p operonx --example ex15_callbot_streaming
cargo run --release -p operonx --example ex15_callbot_streaming -- --runs 5
```

Writes `examples/bench_results/ex15_callbot_streaming_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operon.core import Operon
from examples.python.ex15_callbot_streaming.workflow import build_callbot

def dump(g):
    Operon(g, resources='resources.yaml')
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex15_callbot_streaming/graph.json').write_text(
    json.dumps({'callbot': dump(build_callbot())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
