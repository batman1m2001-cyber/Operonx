# 02 — Data Pipeline (Rust)

Two pure-compute pipelines mirroring the Python side. All `#[op]` bodies are inline in `demo.rs`; pre-serialized graphs live in `graph.json`.

| Scenario | Ops                                    | Shape             |
|----------|----------------------------------------|-------------------|
| `data`   | `fetch_data → transform → aggregate`   | 3 nodes in series |
| `text`   | `clean_text → count_words → summarize` | 3 nodes in series |

## Run

```bash
cargo run --release -p operonx --example ex02_data_pipeline
cargo run --release -p operonx --example ex02_data_pipeline -- --runs 20
cargo run --release -p operonx --example ex02_data_pipeline -- --langfuse
```

Writes `examples/bench_results/ex02_data_pipeline_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from examples.python.ex02_data_pipeline.workflow import build_data_pipeline, build_text_pipeline

def dump(g):
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex02_data_pipeline/graph.json').write_text(
    json.dumps({'data': dump(build_data_pipeline()), 'text': dump(build_text_pipeline())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
