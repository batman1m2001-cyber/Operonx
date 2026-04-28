# 14 — Streaming & Tracing (Python)

Generator pipelines — sync and async. Tier 1 — pure compute, no API
keys.

| Scenario        | Shape                                        |
|-----------------|----------------------------------------------|
| `text`          | `chunk_text` (generator) → `analyze_chunk`   |
| `async_counter` | `async_counter` (async gen) → `format_square`|

`engine.run(...)` accumulates yielded frames into lists. The streaming
`engine.start(...)` real-time delivery path is covered in the
streaming docs; this demo exercises the generator scheduler plumbing
itself. Add a `LangfuseTracer` (or any tracer) at `Operon(g, tracer=...)`
to see frame-by-frame spans.

## Project layout

```
ex14_streaming_tracing/
├── pyproject.toml    # operonx>=0.6.2 (tier 1)
├── README.md
└── main.py
```

## Run

```bash
uv sync
uv run python main.py
```
