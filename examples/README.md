# Operonx Examples

Each subdirectory under `python/` and `rust/` is an **independent
project template** — self-contained `pyproject.toml` / `Cargo.toml`,
its own `.env.example` and `resources.yaml` where relevant, no shared
boilerplate. Copy any example out of the repo and it just works.

```
examples/
├── python/
│   ├── ex01_hello_world/         (tier 1, no API)
│   ├── ex02_data_pipeline/       (tier 1)
│   ├── ex03_llm_chat/            (tier 2, [openai])
│   ├── …
│   └── ex15_callbot_streaming/   (tier 1, streaming demo)
└── rust/
    ├── ex01_hello_world/
    ├── …
    └── ex15_callbot_streaming/
```

## Running an example

Pick a language, pick an example, run it. Every demo flows top to
bottom in a single file.

```bash
# Python
cd examples/python/ex01_hello_world
uv sync
uv run python main.py

# Rust
cd examples/rust/ex01_hello_world
cargo run --release
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

## Install tiers (Python)

| Tier | Install | What's in |
|------|---------|-----------|
| 1 | `pip install operonx` | Engine + ops DSL only (no provider SDKs). ~10 MB. |
| 2 | `operonx[openai]` / `[anthropic]` / `[gemini]` / `[bedrock]` | Tier 1 + that one provider |
| 3 | `operonx[langfuse]` / `[otel]` / `[serve]` / `[onnx]` | Feature extras, additive |
| 4 | `operonx[providers]` / `[standard]` / `[all]` | Pre-bundled meta combos |

## Rust dev mode

Each `examples/rust/exNN_*/Cargo.toml` depends on
`operonx = "0.6.x"` from crates.io. When you run from inside this
repo, `examples/rust/.cargo/config.toml` patches that to the local
workspace via `[patch.crates-io]` so engine devs see their unpublished
changes immediately. Users copying an example dir out of the repo drop
that override and the registry version takes over.

## Rust runtime parity

Some examples flag scenarios as **runs (limited)** or **not run yet**
on the Rust side. Today's known gaps:

- Generator per-item dispatch (the streaming scheduler accumulates
  yields into a list rather than fanning out per frame).
- `if_()` branch routing — `OpConfig` deserialises `cases` / `default`
  / `candidates` (so graphs using `if_()` parse cleanly), but the
  scheduler fires *every* case target and lets a soft-edge merge pick
  the answer. Real selective routing is blocked on the ref-transform
  evaluator (`eq` / `ne` / `ge` / `gt` / `le` / `lt` / `getitem`).
- `engine.stream(...)` real-time delivery handle.

Recently closed:

- ✅ Nested `@graph` composition — `OpType::Graph` now dispatches via
  a process-wide cached sub-`Operon` (built lazily on first call,
  reused thereafter). Python's nested @graph still wins for pure-noop
  trees because Python pre-builds the child scheduler at parent
  build time and just calls `child._scheduler.run(state, ctx)` —
  Rust's per-call `tokio::spawn` + `mpsc::channel` + UUID gen is
  measurable. Real fix (precompute child engines + `run_json_nested`
  fast-path) is logged in `REFACTOR_post_v0.6.2.md`. See
  `scripts/bench/` for the parity table.

These are all v0.7+ work; the Python side is the canonical
implementation. See each Rust example's README for per-scenario
status.

## Regenerating `graph.json`

The Rust examples that compose graphs through provider ops ship a
checked-in `graph.json` produced from the matching Python builder.
The `operonx-pack` CLI (registered by `pip install operonx`) re-emits
it. Targets use pytest-style `module::symbol`. From inside the
example dir:

```bash
cd examples/python/ex03_llm_chat
operonx-pack main::basic_chat main::chain_chat main::summarize_pipeline \
    -o ../../rust/ex03_llm_chat/graph.json
```

Pass `--no-bootstrap` for pure-compute examples that don't need a
`resources.yaml` lookup at build time.
