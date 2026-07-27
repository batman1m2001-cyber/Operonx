# Operonx

<p align="center">
  <a href="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/tests.yaml"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/tests.yaml/badge.svg?branch=main" alt="Tests"></a>
  <a href="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/format.yaml"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/format.yaml/badge.svg?branch=main" alt="Format"></a>
  <a href="https://batman1m2001-cyber.github.io/Operonx/"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/docs.yaml/badge.svg?branch=main" alt="Docs"></a>
  <a href="https://codecov.io/gh/batman1m2001-cyber/Operonx"><img src="https://codecov.io/gh/batman1m2001-cyber/Operonx/branch/main/graph/badge.svg" alt="Coverage"></a>
  <a href="https://pypi.org/project/operonx/"><img src="https://img.shields.io/pypi/v/operonx?label=PyPI" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python">
  <a href="https://github.com/batman1m2001-cyber/Operonx/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License"></a>
</p>

**Operonx** is a workflow engine where ops can `yield` — so the same async DAG handles **batch jobs** (Airflow-style) and **event-driven streaming pipelines** (pipecat-style callbot / voice / STT → LLM → TTS).

> The Rust execution backend now lives in its own repo:
> [batman1m2001-cyber/operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs)
> ([crates.io](https://crates.io/crates/operonx)). It shares the shared JSON
> spec fixtures with this repo but ships independently.

## Why Operonx

- **Yield-based streaming.** Generator ops emit per-item; downstream dispatches per-frame, not per-batch. The `for_loop` / `map_op` / VAD → STT → LLM → TTS shapes work without bolt-on map/reduce ops.
- **Operator reference syntax.** `op["key"]`, `PARENT["key"]`, `op["src"] >> PARENT["dst"]`, `outputs={"*": PARENT}` — explicit and local. No `xcom_pull` per node, no JSON serialisation per hop.
- **Multi-provider LLM / embedding / rerank.** OpenAI, Azure, Gemini, Anthropic, vLLM, TEI, HuggingFace, ONNX, Pinecone — swap with one line in `resources.yaml`. Built-in weighted load balancing + fallback chains.
- **Tracing built-in.** Langfuse, OpenTelemetry, and a local file consumer. All async-flushed; never blocks the run.
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
from operonx.providers import LLMOp

async def main():
    operonx.bootstrap()  # loads ./.env + ./resources.yaml

    with GraphOp(name="qa") as graph:
        c = LLMOp(
            name="llm",
            resource="gpt-4o-mini",
            inputs={
                "prompt": {"system": "You are a helpful assistant.", "user": "{question}"},
                "*": PARENT,
            },
            outputs={"*": PARENT},
        )
        START >> c >> END

    result = await Operon(graph).run(inputs={"question": "What is Python?"})
    print(result["content"])

asyncio.run(main())
```

`LLMOp.prompt` accepts a string, `{"system": ..., "user": ...}` dict, or a full messages list — every non-reserved kwarg becomes a `{var}` substitution.

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
| `standard`   | OpenAI + Langfuse + OTEL (production bundle)     |
| `all`        | Every provider + tracer except `huggingface`      |
| `dev`        | pytest, ruff, pre-commit                          |

## Tracing

```python
import operonx
from operonx.core import Operon

operonx.bootstrap()  # registers consumer configs from resources.yaml

engine = Operon(graph, trace=["trace_langfuse:default"])
```

Consumers are configured in `resources.yaml` (`trace_local:`, `trace_langfuse:`) and referenced by key. See [docs/api/telemetry.md](docs/api/telemetry.md) for the full V3 tracing API.

## Documentation

| Need                       | Go to                                                   |
| -------------------------- | ------------------------------------------------------- |
| Runnable examples (Python) | [examples/python/](examples/python/)                    |
| Architecture               | [docs/architecture/](docs/architecture/)                |
| User guide                 | [docs/guide/](docs/guide/)                              |
| API reference              | [https://batman1m2001-cyber.github.io/Operonx/](https://batman1m2001-cyber.github.io/Operonx/) |
| Rust runtime               | [operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs) |

## Contributing

```bash
git clone https://github.com/batman1m2001-cyber/Operonx.git
cd Operonx
uv sync --all-extras
pre-commit install
uv run pytest tests/ -m "not integration"
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor guide.

## License

Apache 2.0
