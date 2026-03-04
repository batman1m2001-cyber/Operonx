<p align="center">
  <img src="assets/banner.png" alt="Hush">
</p>
<hr>
<p align="center">
  <a href="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/tests.yaml"><img src="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/tests.yaml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/format.yaml"><img src="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/format.yaml/badge.svg" alt="Format"></a>
  <a href="https://codecov.io/gh/batman1m2001-cyber/hush-ai"><img src="https://codecov.io/gh/batman1m2001-cyber/hush-ai/branch/main/graph/badge.svg" alt="Codecov"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python">
  <a href="https://github.com/batman1m2001-cyber/Hush-ai/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License"></a>
</p>

## ⚡ Hush: High-Performance Workflow Engine for AI

**Hush** is a high-performance workflow engine for building AI applications. Orchestrate LLMs, agents, embeddings, and CPU-bound workloads as DAG-based pipelines — with async execution, built-in tracing, and provider-agnostic design.

> Want to dive right in? Jump to the [Quick Start](#quick-start).

## Why Hush?

- **DAG-based workflows** — define complex pipelines with nodes and edges, inspired by Airflow operators
- **Async-first** — native async execution with automatic parallel processing
- **Built-in tracing** — full observability via ui-hush-eyes server + external backends (Langfuse, OpenTelemetry)
- **Provider agnostic** — OpenAI, Azure, Gemini, vLLM, ONNX — swap with one line
- **Type-safe state** — O(1) state access with compile-time validation, zero magic

## What You Can Build

- **LLM pipelines** — chain prompts, parsers, and tools into reliable workflows
- **AI agents** — loops, branches, and dynamic routing with full observability
- **RAG systems** — embeddings, reranking, and retrieval in a single graph
- **Multi-model workflows** — mix OpenAI, Gemini, vLLM, ONNX — swap with one line
- **CPU-bound tasks** — data processing, transformations, and custom code nodes

## Quick Start

```bash
pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"
# Or with uv: uv pip install "hush-core @ git+..."
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

    engine = Hush(graph)
    result = await engine.run(inputs={"name": "World"})
    print(result["message"])  # Hello, World!

asyncio.run(main())
```

> **Core philosophy:** `GraphOp`, `FuncOp`, and `BranchOp` handle nearly every workflow pattern.
> LLM, embedding, and other specialized nodes are optional add-ons — install and learn them as needed.

> Want more? See the [quickstart guide](tutorial/docs/02-quickstart.md) or [runnable examples](tutorial/examples/).

## LLM Integration

```bash
pip install "hush-providers @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-providers"
```

```python
from hush.core import Hush, GraphOp, START, END, PARENT
from hush.providers import ChainOp

async def main():
    with GraphOp(name="chat") as graph:
        chat = ChainOp.of(
            resource="gpt-4o",
            template={"system": "You are a helpful assistant.", "user": "{question}"},
            question=PARENT["question"],
        )
        START >> chat >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"question": "What is Python?"})
    print(result["content"])
```

> **Requires setup:** `.env` (API keys) + `resources.yaml` (provider config). See the [setup guide](tutorial/docs/01-cai-dat-va-thiet-lap.md#3-hiểu-resourcehub--trung-tâm-cấu-hình-của-hush).

## Installation

Hush is a monorepo with 3 separate packages. Install what you need:

**With pip:**

```bash
# Core only (workflow engine, no LLM)
pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"

# Core + LLM providers + Langfuse tracing
pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"
pip install "hush-providers @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-providers"
pip install "hush-telemetry[langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-telemetry"
```

**With uv (recommended):**

```bash
# Core only
uv pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"

# Core + LLM providers + Langfuse tracing
uv pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"
uv pip install "hush-providers @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-providers"
uv pip install "hush-telemetry[langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-telemetry"
```

> **Note:** `hush-providers` and `hush-telemetry` depend on `hush-core`, so always install `hush-core` first.

See [installation guide](tutorial/docs/01-cai-dat-va-thiet-lap.md) for details on extras, `requirements.txt` / `pyproject.toml` templates, and project setup.

## Packages

| Package | Description |
|---------|-------------|
| [hush-core](hush-core/) | Core workflow engine — nodes, state, tracing, execution |
| [rush-core](rush-core/) | High-performance Rust execution backend (1.9x–6.2x speedup) |
| [hush-providers](hush-providers/) | LLM, embedding, reranking provider integrations (Python) |
| [rush-providers](rush-providers/) | Rust provider implementations (native HTTP, ONNX, per-provider) |
| [hush-telemetry](hush-telemetry/) | External tracing backends (Langfuse, OpenTelemetry) |
| [tutorial](tutorial/) | Documentation (Vietnamese) and runnable examples |
| [ui-hush-eyes](ui-hush-eyes/) | Standalone Rust server for trace visualization |

## Trace Viewer

Run the [ui-hush-eyes](ui-hush-eyes/) server to visualize workflow traces:

```bash
cd ui-hush-eyes && cargo build --release
./target/release/ui-hush-eyes serve --port 8420
# Open http://localhost:8420
```

## Documentation

| Need | Go to |
|------|-------|
| Learning from scratch | [tutorial/docs/](tutorial/docs/) |
| Runnable examples | [tutorial/examples/](tutorial/examples/) |
| Deep internals | [architecture/](architecture/) |

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions.

```bash
git clone https://github.com/batman1m2001-cyber/Hush-ai.git
cd Hush-ai/hush-core && uv sync --all-extras && uv run pytest
```

## License

Apache 2.0
