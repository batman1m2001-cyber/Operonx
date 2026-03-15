<p align="center">
  <img src="assets/banner.png" alt="Hush">
</p>
<hr>
<p align="center">
  <a href="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/tests.yaml"><img src="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/tests.yaml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/format.yaml"><img src="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/format.yaml/badge.svg" alt="Format"></a>
  <a href="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/rust-runtime.yaml"><img src="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/rust-runtime.yaml/badge.svg" alt="Rust"></a>
  <a href="https://pypi.org/project/hush-icore/"><img src="https://img.shields.io/pypi/v/hush-icore?label=PyPI" alt="PyPI"></a>
  <a href="https://crates.io/crates/hush-icore"><img src="https://img.shields.io/crates/v/hush-icore?label=crates.io" alt="crates.io"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python">
  <a href="https://github.com/batman1m2001-cyber/Hush-ai/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License"></a>
</p>

## Hush: High-Performance Workflow Engine for AI

**Hush** is a workflow engine that runs anything as a workflow — from IO-bound AI tasks (LLMs, agents, RAG) to CPU-bound workloads needing native performance. Define complex pipelines as DAGs with async execution, built-in tracing, and a dual Python/Rust backend.

### Why Hush?

- **DAG-based workflows** — nodes and edges, inspired by Airflow operators
- **Dual backend** — Python (FastAPI) for flexibility, Rust (Axum) for raw speed (~8x faster on pure-compute)
- **Built-in tracing** — ui-hush-eyes local viewer + Langfuse + OpenTelemetry
- **Provider agnostic** — OpenAI, Azure, Gemini, vLLM, ONNX — swap with one line
- **Type-safe state** — O(1) state access with compile-time validation

## Quick Start

```bash
pip install hush-icore
```

```python
import asyncio
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def greet(name: str):
    return {"message": f"Hello, {name}!"}

async def main():
    with GraphOp(name="hello") as graph:
        step = greet(name=PARENT["name"])
        START >> step >> END

    result = await Hush(graph).run(inputs={"name": "World"})
    print(result["message"])  # Hello, World!

asyncio.run(main())
```

## LLM Integration

```bash
pip install hush-providers
```

```python
from hush.core import Hush, GraphOp, START, END, PARENT
from hush.providers import chain

async def main():
    with GraphOp(name="chat") as graph:
        chat = chain(
            resource="gpt-4o",
            template={"system": "You are a helpful assistant.", "user": "{question}"},
            question=PARENT["question"],
        )
        START >> chat >> END

    result = await Hush(graph).run(inputs={"question": "What is Python?"})
    print(result["content"])
```

## Serve as HTTP API

```bash
pip install hush-serve
```

```python
from hush.serve import HushApp

app = HushApp()
app.endpoint("/greet", graph=graph)

# Python backend (FastAPI + uvicorn)
app.serve(port=8000)

# Rust backend (Axum — ~8x faster)
app.serve(port=8000, backend="rust")
```

## Installation

All packages are on [PyPI](https://pypi.org/search/?q=hush-):

```bash
pip install hush-icore                     # Core workflow engine
pip install hush-providers                 # LLM, embedding, reranking
pip install hush-telemetry                 # Langfuse, OpenTelemetry tracing
pip install hush-serve                     # HTTP API server
```

Rust crates are on [crates.io](https://crates.io/search?q=hush-):

```bash
cargo install hush-serve                   # Standalone Rust HTTP server
```

## Packages

### Python (PyPI)

| Package | Description |
|---------|-------------|
| [hush-icore](https://pypi.org/project/hush-icore/) | Core workflow engine — ops, state, tracing, execution |
| [hush-providers](https://pypi.org/project/hush-providers/) | LLM, embedding, reranking integrations |
| [hush-telemetry](https://pypi.org/project/hush-telemetry/) | External tracing backends (Langfuse, OTEL) |
| [hush-serve](https://pypi.org/project/hush-serve/) | HTTP API server (Python + Rust backends) |

### Rust (crates.io)

| Crate | Description |
|-------|-------------|
| [hush-icore](https://crates.io/crates/hush-icore) | High-performance execution backend (DashMap, tokio) |
| [hush-providers](https://crates.io/crates/hush-providers) | Native HTTP providers + ONNX inference |
| [hush-serve](https://crates.io/crates/hush-serve) | Standalone Axum HTTP server |
| [hush-telemetry](https://crates.io/crates/hush-telemetry) | Rust tracing backends |
| [hush-plugin](https://crates.io/crates/hush-plugin) | Plugin SDK for custom Rust ops |
| [hush-eyes](https://crates.io/crates/hush-eyes) | Trace visualization server (SQLite) |

## Trace Viewer

```bash
cargo install hush-eyes
hush-eyes --port 8420
# Open http://localhost:8420
```

Or use Langfuse / OpenTelemetry:

```python
from hush.telemetry import LangfuseTracer

engine = Hush(graph, tracer=LangfuseTracer(resource="langfuse:default"))
```

## Benchmarks

Pure-compute workflows (1000 requests, 50 concurrent):

| Example | Python (FastAPI) | Rust (Axum) | Speedup |
|---------|-----------------|-------------|---------|
| Hello World | 20.5ms avg | 2.4ms avg | ~8.4x |
| Data Pipeline | 2.5ms avg | 0.6ms avg | ~4.2x |

## Documentation

| Need | Go to |
|------|-------|
| Learning from scratch | [docs/guide/](docs/guide/) (Vietnamese) |
| Runnable examples | [examples/](examples/) |
| Deep internals | [docs/architecture/](docs/architecture/) |
| Standalone examples | [hush-examples](https://github.com/batman1m2001-cyber/hush-examples) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions.

```bash
git clone https://github.com/batman1m2001-cyber/Hush-ai.git
cd Hush-ai/python/hush-icore && uv sync --all-extras && uv run -m pytest
```

## License

Apache 2.0
