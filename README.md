# Operonx

<p align="center">
  <a href="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/tests.yaml"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/tests.yaml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/format.yaml"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/format.yaml/badge.svg" alt="Format"></a>
  <a href="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/rust-runtime.yaml"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/rust-runtime.yaml/badge.svg" alt="Rust"></a>
  <a href="https://codecov.io/gh/batman1m2001-cyber/Operonx"><img src="https://codecov.io/gh/batman1m2001-cyber/Operonx/branch/main/graph/badge.svg" alt="Coverage"></a>
  <a href="https://pypi.org/project/operonx/"><img src="https://img.shields.io/pypi/v/operonx?label=PyPI" alt="PyPI"></a>
  <a href="https://crates.io/crates/operonx"><img src="https://img.shields.io/crates/v/operonx?label=crates.io" alt="crates.io"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python">
  <a href="https://github.com/batman1m2001-cyber/Operonx/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License"></a>
</p>

**Operonx** is a workflow engine that runs anything as a workflow — from IO-bound AI tasks (LLMs, agents, RAG) to CPU-bound workloads needing native performance. Define complex pipelines as DAGs with async execution, built-in tracing, and a dual Python/Rust backend.

## Why Operonx?

- **DAG-based workflows** — nodes and edges, inspired by Airflow operators
- **Dual backend** — Python for flexibility, Rust for raw speed (~8x faster on pure-compute)
- **Built-in tracing** — Langfuse + OpenTelemetry, plus a local viewer
- **Provider agnostic** — OpenAI, Azure, Gemini, Anthropic, vLLM, ONNX — swap with one line
- **Type-safe state** — O(1) state access with schema validation

## Quick Start

```bash
pip install operonx
```

```python
import asyncio
from operonx.core import Operon, GraphOp, op, START, END, PARENT

@op
def greet(name: str):
    return {"message": f"Hello, {name}!"}

async def main():
    with GraphOp(name="hello") as graph:
        step = greet(name=PARENT["name"])
        START >> step >> END

    result = await Operon(graph).run(inputs={"name": "World"})
    print(result["message"])  # Hello, World!

asyncio.run(main())
```

## LLM Integration

```bash
pip install "operonx[standard]"
```

Configure resources in `resources.yaml` and credentials in `.env`, then:

```python
import asyncio
import operonx
from operonx.core import Operon, GraphOp, START, END, PARENT
from operonx.providers import chain

async def main():
    operonx.bootstrap()  # loads ./.env + ./resources.yaml

    with GraphOp(name="chat") as graph:
        chat = chain(
            resource="gpt-4o",
            template={"system": "You are a helpful assistant.", "user": "{question}"},
            question=PARENT["question"],
        )
        START >> chat >> END

    result = await Operon(graph).run(inputs={"question": "What is Python?"})
    print(result["content"])

asyncio.run(main())
```

See [Resource Setup](CLAUDE.md#resource-setup-bootstrap--resourcehub) for details on `bootstrap()` and `resources.yaml`.

## Installation

Operonx is a single Python package with optional extras for each integration:

```bash
pip install operonx                    # Core engine, no providers
pip install "operonx[standard]"        # Recommended — OpenAI + Langfuse + OTEL + serve
pip install "operonx[anthropic]"       # Anthropic-only
pip install "operonx[onnx]"            # Local ONNX inference
pip install "operonx[serve]"           # FastAPI + uvicorn HTTP server
pip install "operonx[all]"             # All providers and tracers (excludes huggingface)
```

| Extra | Contents |
|-------|----------|
| `standard` | OpenAI, Langfuse, OpenTelemetry, FastAPI/uvicorn |
| `anthropic` | Anthropic SDK |
| `gemini` | Google Vertex AI |
| `bedrock` | AWS Bedrock |
| `onnx` | ONNX Runtime + tokenizers |
| `huggingface` | transformers + torch (heavy — ~2.5 GB) |
| `langfuse` | Langfuse tracer |
| `otel` | OpenTelemetry tracer |
| `serve` | FastAPI + uvicorn HTTP server |
| `all` | Everything except `huggingface` |
| `dev` | pytest, ruff, mkdocs |

Rust users:

```bash
cargo add operonx
```

## Tracing

```python
from operonx.telemetry.tracers import LangfuseTracer

engine = Operon(graph, tracer=LangfuseTracer(resource="langfuse:default"))
```

Backends supported: Langfuse, OpenTelemetry. Configure via `resources.yaml`.

## Documentation

| Need | Go to |
|------|-------|
| Runnable examples | [examples/](examples/) |
| Architecture | [docs/architecture/](docs/architecture/) |
| User guide | [docs/guide/](docs/guide/) |
| API reference | [https://batman1m2001-cyber.github.io/Operonx/](https://batman1m2001-cyber.github.io/Operonx/) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
git clone https://github.com/batman1m2001-cyber/Operonx.git
cd Operonx
uv sync --all-extras
pre-commit install
uv run pytest tests/ -m "not integration"
```

## License

Apache 2.0
