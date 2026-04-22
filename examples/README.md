# Operon Examples — Usage Guide

This directory is a learning resource for people picking up either engine. It does **not** orchestrate cross-language benchmarks — each side stands on its own:

```
examples/
├── python/                ← runnable Python demos
│   ├── ex01_hello_world/
│   ├── ex02_data_pipeline/
│   └── …
├── rust/                  ← runnable Rust demos
│   ├── ex01_hello_world/
│   ├── ex02_data_pipeline/
│   └── …
└── bench_results/         ← latency reports land here as JSON
```

## Running an example

Pick a language, pick an example, run it. Each demo is fully self-contained.

```bash
# Python
uv run python -m examples.python.ex01_hello_world.demo

# Rust
cargo run --release -p operonx --example ex01_hello_world
```

Each run writes a JSON report to `examples/bench_results/<name>_<lang>.json` containing per-scenario latencies.

Both entry points accept the same pair of flags:

```bash
# 20 timed iterations per scenario, with Langfuse tracing attached
uv run python -m examples.python.ex01_hello_world.demo --runs 20 --langfuse
cargo run --release -p operonx --example ex01_hello_world -- --runs 20 --langfuse
```

## Timing discipline

The reported latency covers **only** the engine execution — `engine.run(...)` in Python, `engine.run_json(...)` in Rust. Everything before (graph authoring, schema building, JSON parsing) happens outside the timed span so numbers reflect pure runtime performance.

Each scenario goes through:

1. One **untimed warmup** run — populates caches, resolves providers, pays any first-run cost.
2. N **timed runs** (default 5) — latencies recorded, p50 / p95 / mean reported.

## Report format

`examples/bench_results/<example>_<lang>.json`:

```json
{
  "example": "ex01_hello_world",
  "language": "python",
  "timestamp": "2026-04-22T14:30:00Z",
  "scenarios": {
    "hello":    { "warmup_ms": 2.3, "runs_ms": [0.4, 0.3, 0.4, 0.5, 0.4],
                  "p50_ms": 0.4, "p95_ms": 0.5, "mean_ms": 0.40,
                  "output": { "greeting": "Xin chào, Operon!" } },
    "chain":    { … },
    "parallel": { … }
  }
}
```

Run both sides, then diff / plot / tabulate the two files however you like.

## Langfuse

Set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (+ optional `LANGFUSE_HOST`) in `.env` and pass `--langfuse`. Workflow names are suffixed `_python` / `_rust` in the Langfuse dashboard so the two runs don't collide.

## Example index

| # | Folder | Highlights | Needs API keys? |
|---|--------|-----------|:---:|
| 01 | `ex01_hello_world` | Single op, chain, parallel-merge | – |
| 02 | `ex02_data_pipeline` | Data + text pipelines | – |
| 03 | `ex03_llm_chat` | `PromptOp` + `LLMOp`, `chat()` builder | ✓ |
| 04 | `ex04_llm_advanced` | Streaming, multi-turn, structured output | ✓ |
| 05 | `ex05_loops_and_branches` | `GraphOp.loop()`, branch routing | – |
| 07 | `ex07_embeddings_and_rag` | Embedding + RAG retrieval | ✓ |
| 08 | `ex08_error_handling` | Retry, fallback, error routing | – |
| 09 | `ex09_agent_workflow` | Tool-calling agent | ✓ |
| 10 | `ex10_multi_model` | Weighted load balancing, fallback chain | ✓ |
| 11 | `ex11_parallel_advanced` | Stream policies, `collect` / `parallel` | – |
| 12 | `ex12_rag_advanced` | Multi-stage RAG with rerank | ✓ |
| 13 | `ex13_graph` | `@graph` modular workflows | – |
| 14 | `ex14_streaming_tracing` | Streaming + media + trace collection | ✓ |
| 15 | `ex15_callbot_streaming` | End-to-end callbot pipeline | ✓ |

(ex06 — dedicated tracing tutorial — removed; every example carries its own `--langfuse` toggle.)
