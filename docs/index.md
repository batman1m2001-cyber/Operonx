# Hush

**High-Performance Workflow Engine for AI**

Hush is an async-first workflow engine for building AI applications. Orchestrate LLMs, agents, embeddings, and CPU-bound workloads as DAG-based pipelines — with built-in tracing and provider-agnostic design.

## Features

- **DAG-based workflows** — define complex pipelines with nodes and edges
- **Async-first** — native async execution with automatic parallel processing
- **Built-in tracing** — full observability via ui-hush-eyes server + external backends (Langfuse, OpenTelemetry)
- **Provider agnostic** — OpenAI, Azure, Gemini, vLLM, ONNX — swap with one line
- **Type-safe state** — O(1) state access with compile-time validation
- **Rust backend** — optional high-performance execution via rush-core (1.9x–6.2x speedup)

## Quick Example

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

## Documentation

| Section | Description |
|---------|-------------|
| [Getting Started](../tutorial/docs/00-tong-quan.md) | Overview, installation, and quick start |
| [User Guide](../tutorial/docs/03-core-concepts.md) | Core concepts, LLM integration, loops, tracing |
| [Architecture](../architecture/index.md) | Deep technical documentation for contributors |
| [Contributing](../CONTRIBUTING.md) | How to contribute to Hush |

## Packages

| Package | Description |
|---------|-------------|
| **hush-core** | Core workflow engine — nodes, state, tracing, execution |
| **rush-core** | High-performance Rust execution backend (PyO3 + rayon) |
| **hush-providers** | LLM, embedding, reranking provider integrations (Python) |
| **rush-providers** | Rust provider implementations (native HTTP, ONNX, per-provider) |
| **hush-telemetry** | External tracing backends (Langfuse, OpenTelemetry) |
| **ui-hush-eyes** | Standalone Rust server for trace visualization |

## License

Hush is licensed under [Apache 2.0](https://github.com/batman1m2001-cyber/Hush-ai/blob/main/LICENSE).
