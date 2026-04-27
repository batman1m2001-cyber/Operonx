# 03 — LLM Chat (Rust)

Three LLM chat graphs mirroring the Python side. Requires `OPENAI_API_KEY` in `.env` and a `resources.yaml` exposing `gpt-4o-mini`.

| Scenario    | Ops                                 | Notes                           |
|-------------|-------------------------------------|---------------------------------|
| `basic`     | `PromptOp → LLMOp`                  | Explicit two-op form.           |
| `chain`     | `chat()`                            | Single-op all-in-one helper.    |
| `summarize` | `clean_text → PromptOp → LLMOp`     | Pre-processing + summarization. |

`PromptOp` and `LLMOp` are provider ops — the Rust engine builds them at runtime from `graph.json`; only plain `@op`s are declared inline in `demo.rs`.

## Run

```bash
cargo run --release -p operonx --example ex03_llm_chat
cargo run --release -p operonx --example ex03_llm_chat -- --runs 5
cargo run --release -p operonx --example ex03_llm_chat -- --langfuse
```

Writes `examples/bench_results/ex03_llm_chat_rust.json`.

## Regenerating `graph.json`

```bash
uv run python -c "
import json, pathlib, sys
sys.path.insert(0, '.')
from operonx.core import Operon
from examples.python.ex03_llm_chat.workflow import build_basic_chat, build_chain_chat, build_summarize

def dump(g):
    Operon(g, resources='resources.yaml')  # initialize ResourceHub for provider ops
    if hasattr(g, 'build'): g.build()
    data = g.serialize()
    def walk(n):
        if isinstance(n, dict): return {k: walk(v) for k, v in n.items() if k != 'python_callable'}
        if isinstance(n, list): return [walk(v) for v in n]
        return n
    out = walk(data); out['schema_version'] = '1.0'; return out

pathlib.Path('examples/rust/ex03_llm_chat/graph.json').write_text(
    json.dumps({'basic': dump(build_basic_chat()), 'chain': dump(build_chain_chat()), 'summarize': dump(build_summarize())}, indent=2, ensure_ascii=False),
    encoding='utf-8')"
```
