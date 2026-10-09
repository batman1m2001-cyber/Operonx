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

## Start a project

You build with operonx through a coding assistant. `operonx init` makes a
project it can work in at once: a layout, a first feature with its tests,
the rules in `AGENTS.md`, and the API guide of the installed operonx in
`.operonx/guide/`.

```bash
# with uv
uvx operonx init myapp
cd myapp && uv sync && uv run pytest

# with pip only
pip install operonx
operonx init myapp && cd myapp
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[test]" && pytest
```

Then open the folder in your coding assistant and ask for what you want.
[Installation](guide/00-installation.md) shows what the project holds and
which files the assistant reads.

## Where to go next

- **New users:** start with [Installation](guide/00-installation.md), then
  [First workflow](guide/01-first-workflow.md) to read how a graph works.
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
