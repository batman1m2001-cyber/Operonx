# 14 — Streaming & Tracing (Python)

Generator pipelines — sync and async. No API keys.

| Scenario        | Shape                                        |
|-----------------|----------------------------------------------|
| `text`          | `chunk_text` (generator) → `analyze_chunk`   |
| `async_counter` | `async_counter` (async gen) → `format_square`|

`engine.run(...)` accumulates yielded frames into lists. The streaming
`engine.start(...)` real-time delivery path is exercised in the Hush
tutorial but not timed here — add it yourself once you're ready to see
the frame-by-frame view.

## Run

```bash
uv run python -m examples.python.ex14_streaming_tracing.demo
uv run python -m examples.python.ex14_streaming_tracing.demo --runs 20
uv run python -m examples.python.ex14_streaming_tracing.demo --langfuse
```

Writes `examples/bench_results/ex14_streaming_tracing_python.json`.
