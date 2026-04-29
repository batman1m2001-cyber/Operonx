# Operonx

<p align="center">
  <a href="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/tests.yaml"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/tests.yaml/badge.svg?branch=main" alt="Tests"></a>
  <a href="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/format.yaml"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/format.yaml/badge.svg?branch=main" alt="Format"></a>
  <a href="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/rust-runtime.yaml"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/rust-runtime.yaml/badge.svg?branch=main" alt="Rust"></a>
  <a href="https://batman1m2001-cyber.github.io/Operonx/"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/docs.yaml/badge.svg?branch=main" alt="Docs"></a>
  <a href="https://codecov.io/gh/batman1m2001-cyber/Operonx"><img src="https://codecov.io/gh/batman1m2001-cyber/Operonx/branch/main/graph/badge.svg" alt="Coverage"></a>
  <a href="https://pypi.org/project/operonx/"><img src="https://img.shields.io/pypi/v/operonx?label=PyPI" alt="PyPI"></a>
  <a href="https://crates.io/crates/operonx"><img src="https://img.shields.io/crates/v/operonx?label=crates.io" alt="crates.io"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python">
  <a href="https://github.com/batman1m2001-cyber/Operonx/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License"></a>
</p>

**Operonx** is a workflow engine where ops can `yield` — so the same async DAG handles **batch jobs** (Airflow-style) and **event-driven streaming pipelines** (pipecat-style callbot / voice / STT → LLM → TTS). Compose async pipelines as DAGs in Python, ship to production on a drop-in Rust runtime that's **2–3× faster on linear chains, 10–12× on production-shape mixed workloads, 17–38× on pure compute** — same graph, no rewrites.

## Why Operonx

- **Yield-based streaming.** Generator ops emit per-item; downstream dispatches per-frame, not per-batch. The `for_loop` / `map_op` / VAD → STT → LLM → TTS shapes work without bolt-on map/reduce ops.
- **Dual runtime.** Author in Python, run on Python or Rust. `operonx-pack` serialises a `@graph` factory to JSON; the Rust binary reads the same JSON and runs it through a tokio-based scheduler with inline-sync fast-paths and pre-compiled ref dispatch.
- **Operator reference syntax.** `op["key"]`, `PARENT["key"]`, `op["src"] >> PARENT["dst"]`, `outputs={"*": PARENT}` — explicit and local. No `xcom_pull` per node, no JSON serialisation per hop.
- **Multi-provider LLM / embedding / rerank.** OpenAI, Azure, Gemini, Anthropic, vLLM, TEI, HuggingFace, ONNX, Pinecone — swap with one line in `resources.yaml`. Built-in weighted load balancing + fallback chains.
- **Tracing built-in.** Langfuse, OpenTelemetry, and a local file tracer (operon-eyes). All async-flushed; never blocks the run.
- **Lean tier-1.** `pip install operonx` is just `pydantic / pyyaml / rich / orjson`. Provider SDKs are extras.

## Quick Start

```bash
pip install operonx
```

```python
import asyncio
from operonx.core import Operon, GraphOp, op, START, END, PARENT

@op
def greet(who: str):
    return {"message": f"Hello, {who}!"}

async def main():
    with GraphOp(name="hello") as graph:
        step = greet(who=PARENT["who"])
        START >> step >> END

    result = await Operon(graph).run(inputs={"who": "World"})
    print(result["message"])  # Hello, World!

asyncio.run(main())
```

## Streaming with `yield`

The differentiator. A generator op yields per item; downstream ops dispatch on each frame. The same engine that runs a batch DAG runs a callbot pipeline.

```python
from operonx.core import Operon, GraphOp, op, START, END, PARENT

@op
def chunk_text(text: str, chunk_size: int):
    for i, words in enumerate(words_in(text, chunk_size)):
        yield {"chunk": " ".join(words), "index": i}

@op
def analyze(chunk: str, index: int):
    return {"result": f"[{index}] {len(chunk.split())} words"}

with GraphOp(name="pipeline") as g:
    src = chunk_text(text=PARENT["text"], chunk_size=PARENT["chunk_size"])
    step = analyze(chunk=src["chunk"], index=src["index"])
    START >> src >> step >> END
```

Each yield triggers a dispatch on a fresh `(parent_ctx, "yield_N")` sub-context. Empty yield = zero downstream dispatches (matches Python's skipped `yield`). N-to-M flows (one VAD chunk → multiple speech segments) work because each yield is independent.

See [examples/python/ex14](examples/python/ex14_streaming_tracing/) for the streaming + tracing demo, [examples/python/ex15](examples/python/ex15_callbot_streaming/) for the callbot pipeline (audio → VAD → STT → intent → handler → TTS).

## LLMs in one line

```bash
pip install "operonx[standard]"
```

```python
import asyncio
import operonx
from operonx.core import Operon, GraphOp, START, END, PARENT
from operonx.providers import chat

async def main():
    operonx.bootstrap()  # loads ./.env + ./resources.yaml

    with GraphOp(name="qa") as graph:
        c = chat(
            resource="gpt-4o-mini",
            template={"system": "You are a helpful assistant.", "user": "{question}"},
            question=PARENT["question"],
        )
        START >> c >> END

    result = await Operon(graph).run(inputs={"question": "What is Python?"})
    print(result["content"])

asyncio.run(main())
```

`chat()` is a `@graph` factory that wires `PromptOp → LLMOp` and forwards every output. For lower-level control use `LLMOp.of(resource=..., messages=...)` directly.

### Multi-model load balancing + fallback

```python
from operonx.providers import LLMOp

llm = LLMOp.of(
    resource=["gpt-4o", "gpt-4o-mini"],
    ratios=[0.7, 0.3],          # 70 / 30 split
    fallback=["claude-haiku"],  # tried in order on failure
    messages=PARENT["messages"],
)
```

### Branching

```python
from operonx.core import START, END, GraphOp, PARENT
from operonx.core.ops.flow.branch_op import if_

router = (if_(PARENT["score"] >= 90, "excellent")
          .if_(PARENT["score"] >= 70, "good")
          .else_("fail"))
START >> router >> excellent >> merge >> END
router >> good >> merge
router >> fail >> merge
```

`if_()` evaluates conditions in order; the first match routes through a soft edge (`>>~` semantically — branch outputs use soft edges so non-matching branches don't block downstream).

### Loops

```python
from operonx.core import GraphOp, START, END, PARENT

with GraphOp.loop(until="count >= 5", count=0) as loop:
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    START >> inc >> END
```

`until` accepts a string expression evaluated against graph outputs.

## Python → Rust

Same graph, two runtimes. Author in Python, run in production on Rust:

```bash
# 1. Pack the @graph factory to JSON (ships as `operonx-pack` CLI)
operonx-pack my_module::my_graph -o graph.json

# 2. Rust binary reads graph.json + inputs.json
cargo run --release
```

```rust
use operonx::{op, Operon};
use serde_json::Value;

#[op(name = "greet")]
fn greet(who: String) -> Value {
    serde_json::json!({ "message": format!("Hello, {}!", who) })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let graph_json = std::fs::read_to_string("graph.json")?;
    let inputs = serde_json::json!({ "who": "World" }).as_object().unwrap().clone();
    let engine = Operon::builder(&graph_json).auto_register().build()?;
    let result = engine.run_json(inputs, None, None, None)?;
    println!("{result}");
    Ok(())
}
```

The Rust runtime supports the full scheduler surface — generators (yield-per-item), branching (`if_()`), nested `@graph` (inline-dispatch fast-path), loops, sync inline-fast-path for non-blocking ops, async tokio dispatch for I/O. Provider ops (LLM, embedding, rerank, ONNX, Keycloak) ship native Rust implementations via `operonx::bootstrap()`.

### Benchmarks (Python ↔ Rust, identical graphs)

| Pattern                      | Python   | Rust     | Speedup |
| ---------------------------- | -------- | -------- | ------- |
| linear_500 (500-op chain)    | 16.4 ms  | 6.4 ms   | 2.6×    |
| matrix_chain_5x100           | 574 ms   | 17.9 ms  | 32×     |
| cpu_contention_5h_10l_30m    | 17.2 ms  | 1.0 ms   | 17×     |
| nested_10 (10-deep nesting)  | 4.3 ms   | 2.8 ms   | 1.5×    |
| branching_10                 | 2.4 ms   | 0.86 ms  | 2.7×    |
| production_5                 | 17.7 ms  | 1.6 ms   | 11×     |

Verified by `scripts/bench/parity.py` — every pattern produces byte-equal output on both runtimes.

## Installation

Single Python package, optional extras for each integration:

```bash
pip install operonx                  # Tier 1 — engine only, ~10 MB
pip install "operonx[openai]"        # OpenAI / Azure
pip install "operonx[anthropic]"     # Anthropic via httpx
pip install "operonx[gemini]"        # Vertex AI
pip install "operonx[onnx]"          # Local ONNX inference
pip install "operonx[langfuse]"      # Langfuse tracing
pip install "operonx[otel]"          # OpenTelemetry tracing
pip install "operonx[standard]"      # Recommended — providers + Langfuse + OTEL
pip install "operonx[all]"           # Everything except torch / HuggingFace
```

Rust:

```bash
cargo add operonx
```

| Extra        | Contents                                          |
| ------------ | ------------------------------------------------- |
| `openai`     | OpenAI SDK (also covers Azure)                    |
| `anthropic`  | `httpx` + OpenAI message types                    |
| `gemini`     | `google-cloud-aiplatform` + AsyncOpenAI client    |
| `bedrock`    | `boto3` + OpenAI message types                    |
| `onnx`       | `onnxruntime` + `tokenizers` + `numpy`            |
| `huggingface`| `transformers` + `torch` (~2.5 GB; opt in)        |
| `langfuse`   | Langfuse SDK                                      |
| `otel`       | OpenTelemetry API + SDK + OTLP exporters          |
| `standard`   | OpenAI + Langfuse + OTEL (production bundle)      |
| `all`        | Every provider + tracer except `huggingface`      |
| `dev`        | pytest, ruff, pre-commit                          |

## Tracing

```python
from operonx.telemetry.tracers import LangfuseTracer

engine = Operon(graph, tracer=LangfuseTracer(resource="langfuse:default"))
```

Backends: Langfuse, OpenTelemetry, local file tracer (operon-eyes). Configure credentials in `resources.yaml`.

## Documentation

| Need                       | Go to                                                   |
| -------------------------- | ------------------------------------------------------- |
| Runnable examples (Python) | [examples/python/](examples/python/)                    |
| Runnable examples (Rust)   | [examples/rust/](examples/rust/)                        |
| Architecture               | [docs/architecture/](docs/architecture/)                |
| User guide                 | [docs/guide/](docs/guide/)                              |
| API reference              | [https://batman1m2001-cyber.github.io/Operonx/](https://batman1m2001-cyber.github.io/Operonx/) |
| Benchmarks                 | [scripts/bench/](scripts/bench/)                        |

## Contributing

```bash
git clone https://github.com/batman1m2001-cyber/Operonx.git
cd Operonx
uv sync --all-extras
pre-commit install
uv run pytest tests/ -m "not integration"
cd rust && cargo test --workspace
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor guide.

## License

Apache 2.0
