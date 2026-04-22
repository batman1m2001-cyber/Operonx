# 15 — Callbot Streaming (Python)

Multi-level streaming callbot: audio → VAD → STT → LLM router → TTS. No API keys.

| Scenario  | Shape                                                              |
|-----------|--------------------------------------------------------------------|
| `callbot` | `customer_audio → vad → stt → llm_router (@graph) → tts`           |

## Run

```bash
uv run python -m examples.python.ex15_callbot_streaming.demo
uv run python -m examples.python.ex15_callbot_streaming.demo --runs 5
uv run python -m examples.python.ex15_callbot_streaming.demo --langfuse
```

Writes `examples/bench_results/ex15_callbot_streaming_python.json`.
