# Operonx

**Operonx** is a workflow engine that runs anything as a workflow — from
IO-bound AI tasks (LLMs, agents, RAG) to CPU-bound workloads needing native
performance. Define complex pipelines as DAGs with async execution and
built-in tracing.

## Why Operonx

- **DAG-based workflows** — nodes and edges, inspired by Airflow operators.
- **Yield-based streaming** — the same engine handles batch jobs and
  event-driven pipelines (VAD → STT → LLM → TTS).
- **Built-in tracing** — Langfuse + OpenTelemetry, plus a local viewer.
- **Provider agnostic** — OpenAI, Azure, Gemini, Anthropic, vLLM, ONNX —
  swap with one line.
- **Type-safe state** — O(1) state access with schema validation.

## Quick start

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
    print(result["message"])

asyncio.run(main())
```

## LLM integration

```bash
pip install "operonx[standard]"
```

Configure resources in `resources.yaml`, credentials in `.env`, then:

```python
import asyncio
import operonx
from operonx.core import Operon, GraphOp, START, END, PARENT
from operonx.providers import LLMOp

async def main():
    operonx.bootstrap()  # loads ./.env + ./resources.yaml

    with GraphOp(name="chat") as graph:
        c = LLMOp(
            name="llm",
            resource="gpt-4o",
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

See [Resource hub](architecture/resource-hub.md) for the full setup model.

## Where to go next

- **New users:** start with [Installation](guide/00-installation.md) and
  [First workflow](guide/01-first-workflow.md).
- **LLM workflows:** see [LLM chat](guide/02-llm-chat.md) and [RAG](guide/04-rag.md).
- **Internals:** [Architecture overview](architecture/overview.md) explains
  how the engine, scheduler, and state model fit together.
- **API reference:** auto-generated from docstrings under
  [API reference](api/core.md).

## Repository

- [GitHub](https://github.com/batman1m2001-cyber/Operonx)
- [Issues](https://github.com/batman1m2001-cyber/Operonx/issues)
- [Changelog](https://github.com/batman1m2001-cyber/Operonx/blob/main/CHANGELOG.md)
- License: [Apache-2.0](https://github.com/batman1m2001-cyber/Operonx/blob/main/LICENSE)
