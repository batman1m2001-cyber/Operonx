# 14 — Streaming & Tracing (Rust)

Mirrors the Python side.

| Scenario        | Shape                                        | Status       |
|-----------------|----------------------------------------------|--------------|
| `text`          | `chunk_text` (generator) → `analyze_chunk`   | Rust-limited |
| `async_counter` | `async_counter` (async gen) → `format_square`| Rust-limited |

## Rust-limited

- **Generator ops**: per-item dispatch is not yet implemented in the Rust scheduler — `chunk_text` and `async_counter` return accumulated lists as single-shot values. Downstream ops see a list rather than one item per frame.
- **`engine.stream(...)`**: the Rust engine does not yet expose a streaming handle; the Python demo uses `engine.start(...)` for real-time frame delivery, which has no direct Rust counterpart.

## Run

```bash
cargo run --release -p operonx --example ex14_streaming_tracing
cargo run --release -p operonx --example ex14_streaming_tracing -- --runs 20
```

Writes `examples/bench_results/ex14_streaming_tracing_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operonx.core import Operon
from examples.python.ex14_streaming_tracing.workflow import build_text_pipeline, build_async_pipeline

def dump(g):
    Operon(g, resources='resources.yaml')
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex14_streaming_tracing/graph.json').write_text(
    json.dumps({'text': dump(build_text_pipeline()), 'async_counter': dump(build_async_pipeline())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
