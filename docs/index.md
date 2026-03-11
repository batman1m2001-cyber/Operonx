# Hush

**High-performance workflow engine that runs anything as a workflow.**

From IO-bound AI tasks like LLMs and agents to CPU-bound workloads needing native performance. Inspired by Airflow operators, Hush enforces clear, consistent coding conventions for building scalable workflows.

## Quick Example

```python
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def double(x: int):
    return {"result": x * 2}

with GraphOp(name="my-workflow") as graph:
    step = double(x=PARENT["input"])
    START >> step >> END

engine = Hush(graph)
result = await engine.run(inputs={"input": 5})
print(result["result"])  # 10
```

## Key Features

- **DAG-based workflows** — define ops, wire them with `>>`, run with `engine.run()`
- **Async-first** — built on asyncio for IO-bound tasks
- **Rust backend** — optional native execution via rush-core for CPU-bound workloads
- **Streaming** — generator ops yield tokens in real-time
- **Middleware** — extensible hooks for tracing, retry, caching, validation
- **Observability** — built-in tracing with Langfuse, OpenTelemetry, and HushEyes backends
- **Serve** — one-line HTTP API via `engine.serve()`

## Getting Started

<div class="grid cards" markdown>

- :material-rocket-launch: **[Quickstart](guide/02-quickstart.md)** — build your first workflow
- :material-book-open-variant: **[Core Concepts](guide/03-core-concepts.md)** — ops, graphs, state, edges
- :material-brain: **[LLM Integration](guide/04-llm-integration.md)** — connect to OpenAI, Gemini, etc.
- :material-api: **[API Reference](api/engine.md)** — full API documentation

</div>

## Architecture

```
Python (build time)              Rust (run time, optional)
─────────────────                ──────────────────────────
GraphOp DSL                      Rush(config)
  │                                │
  ▼                                ▼
graph.serialize() ──JSON──→   GraphConfig::from_json()
                                │
                                ▼
                            run_graph() → async event-queue scheduler
```

See [Architecture docs](architecture/execution-flow.md) for deep dives.
