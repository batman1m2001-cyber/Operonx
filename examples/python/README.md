# Python examples

Each subdirectory is a **standalone project** — its own
`pyproject.toml`, `.env` (gitignored), and `resources.yaml` where
relevant. Copy any directory out of the repo and it just works.

## Run an example

```bash
cd examples/python/exNN_<name>
uv sync
uv run python main.py
```

(Or `pip install -e .` + `python main.py` if you prefer plain pip.)

For LLM examples, copy `.env.example` to `.env` and fill in your key
before `uv run`.

## Index

| # | Example | Install | What it teaches |
|---|---------|---------|-----------------|
| 01 | `ex01_hello_world` | `operonx` (tier 1) | `@op`, `@graph`, `>>`, `START` / `END` |
| 02 | `ex02_data_pipeline` | `operonx` (tier 1) | Linear pipelines |
| 03 | `ex03_llm_chat` | `operonx[openai]` | `LLMOp` (unified prompt + call) |
| 04 | `ex04_llm_advanced` | `operonx[openai]` | Structured output, tool calling, multi-turn |
| 05 | `ex05_loops_and_branches` | `operonx` (tier 1) | Generator ops + `if_()` routing |
| 06 | `ex06_state_and_streaming` | `operonx` (tier 1) | `SCRATCH` state, `EmitOp` side channels, `InterruptOp` HITL |
| 07 | `ex07_embeddings_and_rag` | `operonx[openai]` | `EmbeddingOp` + cosine RAG + optional reranker |
| 08 | `ex08_error_handling` | `operonx[openai]` (subset of scenarios runs on tier 1) | Capture, route, retry, LLM fallback |
| 09 | `ex09_agent_workflow` | `operonx[openai]` | Tool-calling agent on `@graph.loop` |
| 10 | `ex10_multi_model` | `operonx[openai]` | Parallel, routing, load-balance, fallback, ensemble |
| 11 | `ex11_parallel_advanced` | `operonx` (tier 1) | Fan-out / fan-in, generator iteration, partial failure |
| 12 | `ex12_rag_advanced` | `operonx[openai]` | Keyword RRF + hybrid (vector + keyword) RAG |
| 13 | `ex13_graph` | `operonx` (tier 1) | `@graph` composition + nesting |
| 14 | `ex14_streaming_tracing` | `operonx` (tier 1) | Generator pipelines, sync + async |
| 15 | `ex15_callbot_streaming` | `operonx` (tier 1) | Multi-level streaming pipeline |

ex06 previously held a tracing tutorial, retired once every LLM example
documented its own tracing setup; the slot now covers run-scoped state,
side channels, and interrupts.

## Install tiers

Same as the top-level [`examples/README.md`](../README.md) — tier 1
(`operonx`) is engine-only (no provider SDKs). `[openai]`,
`[anthropic]`, `[gemini]`, `[bedrock]` add a single provider.
`[faiss]`, `[pgvector]`, `[qdrant]`, `[postgres]` add a retrieval
backend. `[standard]` and `[all]` are pre-bundled meta combos.

## Resources

`resources.yaml` is per-example so each demo proves a real install
slice — no shared config, no umbrella inheritance. Provider examples
ship a minimal config naming only the resources that example uses.
