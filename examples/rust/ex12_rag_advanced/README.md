# 12 — RAG Advanced (Rust)

Keyword RRF (pure compute) + hybrid (vector + keyword) RAG. Mirrors the Python side.

| Scenario      | Shape                                                            | Status       |
|---------------|------------------------------------------------------------------|--------------|
| `keyword_rrf` | Two keyword searches parallel → RRF merge                        | OK           |
| `hybrid`      | `[keyword, EmbeddingOp → vec_search]` → merge → prompt → LLM     | Rust-limited |

## Rust-limited

- **Hybrid precomp**: the Rust demo injects a placeholder `doc_vectors` (column of zeros). The graph still executes end-to-end, but the `vec_search_fn` branch returns no meaningful cosine matches — only the keyword branch drives the RRF merge. Python's demo runs an untimed embedding pass on the documents before the timed hybrid run; the Rust equivalent would need a second engine instance to do the same.

## Run

```bash
cargo run --release -p operonx --example ex12_rag_advanced
cargo run --release -p operonx --example ex12_rag_advanced -- --runs 5
```

Writes `examples/bench_results/ex12_rag_advanced_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operon.core import Operon
from examples.python.ex12_rag_advanced.workflow import build_keyword_rrf, build_hybrid_rag

def dump(g):
    Operon(g, resources='resources.yaml')
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex12_rag_advanced/graph.json').write_text(
    json.dumps({'keyword_rrf': dump(build_keyword_rrf()), 'hybrid': dump(build_hybrid_rag())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
