# Hush

**High-Performance Workflow Engine for AI**

Hush is an async-first workflow engine for building AI applications. Orchestrate LLMs, agents, embeddings, and CPU-bound workloads as DAG-based pipelines — with built-in tracing and provider-agnostic design.

## Features

- **DAG-based workflows** — define complex pipelines with nodes and edges
- **Async-first** — native async execution with automatic parallel processing
- **Built-in tracing** — full observability via SQLite + external backends (Langfuse, OpenTelemetry)
- **Provider agnostic** — OpenAI, Azure, Gemini, vLLM, ONNX — swap with one line
- **Type-safe state** — O(1) state access with compile-time validation

## Quick Example

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

## Documentation

| Section | Description |
|---------|-------------|
| [Getting Started](tutorial/00-tong-quan.md) | Overview, installation, and quick start |
| [User Guide](tutorial/03-core-concepts.md) | Core concepts, LLM integration, loops, tracing |
| [Architecture](architecture/index.md) | Deep technical documentation for contributors |
| [Contributing](CONTRIBUTING.md) | How to contribute to Hush |

## Packages

| Package | Description |
|---------|-------------|
| **hush-core** | Core workflow engine — nodes, state, tracing, execution |
| **hush-providers** | LLM, embedding, reranking provider integrations |
| **hush-ops** | External tracing backends (Langfuse, OpenTelemetry) |

## License

Hush is licensed under [Apache 2.0](https://github.com/batman1m2001-cyber/Hush-ai/blob/main/LICENSE).
