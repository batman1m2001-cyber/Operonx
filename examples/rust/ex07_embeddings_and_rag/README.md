# 07 — Embeddings & RAG (Rust)

Mirrors the Python side. All provider ops (`EmbeddingOp`, `PromptOp`, `LLMOp`, `RerankOp`) are runtime-built from `graph.json`; only the plain `retrieve` op is inline.

| Scenario | Ops                                                                 |
|----------|---------------------------------------------------------------------|
| `embed`  | `EmbeddingOp`                                                       |
| `rag`    | `EmbeddingOp → retrieve → PromptOp → LLMOp`                         |
| `rerank` | `RerankOp → PromptOp → LLMOp`                                       |

Requires `OPENAI_API_KEY` + a `resources.yaml` with `openai` embedding and `gpt-4o-mini` LLM. `rerank` additionally needs a `bge-m3` reranker.

## Run

```bash
cargo run --release -p operonx --example ex07_embeddings_and_rag
cargo run --release -p operonx --example ex07_embeddings_and_rag -- --runs 5
```

Writes `examples/bench_results/ex07_embeddings_and_rag_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operonx.core import Operon
from examples.python.ex07_embeddings_and_rag.workflow import build_basic_embedding, build_simple_rag, build_rag_with_rerank

def dump(g):
    Operon(g, resources='resources.yaml')
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex07_embeddings_and_rag/graph.json').write_text(
    json.dumps({'embed': dump(build_basic_embedding()), 'rag': dump(build_simple_rag()), 'rerank': dump(build_rag_with_rerank())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
