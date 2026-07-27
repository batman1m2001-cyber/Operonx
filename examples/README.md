# Operonx Examples

Each subdirectory under `python/` is an **independent project
template** — self-contained `pyproject.toml`, its own `.env.example`
and `resources.yaml` where relevant, no shared boilerplate. Copy any
example out of the repo and it just works.

```
examples/
└── python/
    ├── ex01_hello_world/         (tier 1, no API)
    ├── ex02_data_pipeline/       (tier 1)
    ├── ex03_llm_chat/            (tier 2, [openai])
    ├── …
    └── ex15_callbot_streaming/   (tier 1, streaming demo)
```

For Rust examples see
[operonx-rs/examples/](https://github.com/batman1m2001-cyber/operonx-rs/tree/main/examples).

## Running an example

Pick an example, run it. Every demo flows top to bottom in a single
file.

```bash
cd examples/python/ex01_hello_world
uv sync
uv run python main.py
```

Examples that need API keys ship a `.env.example`. Copy it to `.env`
and fill in the values before running.

## Index

| # | Example | Tier (Python) | What it teaches |
|---|---------|---------------|-----------------|
| 01 | `ex01_hello_world` | tier 1 | `@op`, `@graph`, `>>`, START/END |
| 02 | `ex02_data_pipeline` | tier 1 | Linear pipelines |
| 03 | `ex03_llm_chat` | `[openai]` | `PromptOp` / `LLMOp` / `chat()` |
| 04 | `ex04_llm_advanced` | `[openai]` | Structured output, tool calling, multi-turn |
| 05 | `ex05_loops_and_branches` | tier 1 | Generator ops + `if_()` routing |
| 07 | `ex07_embeddings_and_rag` | `[providers]` | `EmbeddingOp` + cosine RAG + optional reranker |
| 08 | `ex08_error_handling` | `[openai]` | Capture, route, retry, LLM fallback |
| 09 | `ex09_agent_workflow` | `[openai]` | Tool-calling agent on `@graph.loop` |
| 10 | `ex10_multi_model` | `[openai]` | Parallel, routing, load-balance, fallback, ensemble |
| 11 | `ex11_parallel_advanced` | tier 1 | Fan-out/fan-in, generator iteration, partial failure |
| 12 | `ex12_rag_advanced` | `[providers]` | Keyword RRF + hybrid (vector + keyword) RAG |
| 13 | `ex13_graph` | tier 1 | `@graph` composition + nesting |
| 14 | `ex14_streaming_tracing` | tier 1 | Generator pipelines, sync + async |
| 15 | `ex15_callbot_streaming` | tier 1 | Multi-level streaming pipeline |

ex06 is intentionally omitted (was a tracing tutorial; every example
that uses an LLM now documents its own tracing setup).

## Install tiers

| Tier | Install | What's in |
|------|---------|-----------|
| 1 | `pip install operonx` | Engine + ops DSL only (no provider SDKs). ~10 MB. |
| 2 | `operonx[openai]` / `[anthropic]` / `[gemini]` / `[bedrock]` | Tier 1 + that one provider |
| 3 | `operonx[langfuse]` / `[otel]` / `[serve]` / `[onnx]` | Feature extras, additive |
| 4 | `operonx[providers]` / `[standard]` / `[all]` | Pre-bundled meta combos |
