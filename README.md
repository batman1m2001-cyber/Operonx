<p align="center">
  <img src="assets/banner.png" alt="Hush" width="280">
</p>
<hr>
<p align="center">
  <a href="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/tests.yaml"><img src="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/tests.yaml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/format.yaml"><img src="https://github.com/batman1m2001-cyber/Hush-ai/actions/workflows/format.yaml/badge.svg" alt="Format"></a>
  <a href="https://codecov.io/gh/batman1m2001-cyber/Hush-ai"><img src="https://codecov.io/gh/batman1m2001-cyber/Hush-ai/branch/main/graph/badge.svg" alt="Codecov"></a>
  <a href="https://batman1m2001-cyber.github.io/Hush-ai/"><img src="https://img.shields.io/badge/docs-mkdocs-blue" alt="Documentation"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python">
  <a href="https://github.com/batman1m2001-cyber/Hush-ai/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License"></a>
</p>

## ⚡ Hush: High-Performance Workflow Engine for AI

**Hush** is a high-performance workflow engine for building AI applications. Orchestrate LLMs, agents, embeddings, and CPU-bound workloads as DAG-based pipelines — with async execution, built-in tracing, and provider-agnostic design.

> Want to dive right in? Jump to the [Quick Start](#quick-start).

## Why Hush?

- **DAG-based workflows** — define complex pipelines with nodes and edges, inspired by Airflow operators
- **Async-first** — native async execution with automatic parallel processing
- **Built-in tracing** — full observability via SQLite + external backends (Langfuse, OpenTelemetry)
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
uv pip install "hush-ai[core] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"
```

```python
import asyncio
from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT

async def main():
    with GraphNode(name="hello") as graph:
        step1 = CodeNode(
            name="greet",
            code_fn=lambda name: {"message": f"Hello, {name}!"},
            inputs={"name": PARENT["name"]},
            outputs={"message": PARENT}
        )
        START >> step1 >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"name": "World"})
    print(result["message"])  # Hello, World!

asyncio.run(main())
```

> Want more? See the [quickstart guide](hush-tutorial/docs/02-quickstart.md) or [runnable examples](hush-tutorial/examples/).

## LLM Integration

```python
from hush.core import Hush, GraphNode, START, END, PARENT
from hush.providers import PromptNode, LLMNode

async def main():
    with GraphNode(name="chat") as graph:
        prompt = PromptNode(
            name="prompt",
            inputs={
                "prompt": {"system": "You are a helpful assistant.", "user": "{question}"},
                "question": PARENT["question"]
            },
            outputs={"messages": PARENT}
        )
        llm = LLMNode(
            name="llm",
            resource_key="gpt-4o",
            inputs={"messages": PARENT["messages"]},
            outputs={"content": PARENT["answer"]}
        )
        START >> prompt >> llm >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"question": "What is Python?"})
    print(result["answer"])
```

## Installation

```bash
# Full (all providers + observability)
uv pip install "hush-ai[all] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# OpenAI + Langfuse
uv pip install "hush-ai[openai,langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# Core only (no external dependencies)
uv pip install "hush-ai[core] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"
```

See [installation guide](hush-tutorial/docs/01-cai-dat-va-thiet-lap.md) for details.

## Packages

| Package | Description |
|---------|-------------|
| [hush-core](hush-core/) | Core workflow engine — nodes, state, tracing, execution |
| [hush-providers](hush-providers/) | LLM, embedding, reranking provider integrations |
| [hush-observability](hush-observability/) | External tracing backends (Langfuse, OpenTelemetry) |
| [hush-tutorial](hush-tutorial/) | Documentation (Vietnamese) and runnable examples |
| [hush-vscode-traceview](hush-vscode-traceview/) | VS Code extension for trace visualization |

## Trace Viewer

Traces are automatically saved to `~/.hush/traces.db`. View them in VS Code:

1. Install the extension from [hush-vscode-traceview](hush-vscode-traceview/)
2. Open Command Palette → **Hush: Open Trace Viewer**

## Documentation

| Need | Go to |
|------|-------|
| Learning from scratch | [hush-tutorial/docs/](hush-tutorial/docs/) |
| Runnable examples | [hush-tutorial/examples/](hush-tutorial/examples/) |
| Deep internals | [architecture/](architecture/) |

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions.

```bash
git clone https://github.com/batman1m2001-cyber/Hush-ai.git
cd Hush-ai/hush-core && uv sync --all-extras && uv run pytest
```

## License

Apache 2.0
