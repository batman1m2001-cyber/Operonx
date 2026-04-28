# 15 — Callbot Streaming (Python)

Multi-level streaming pipeline: audio → VAD → STT → LLM router → TTS.
Tier 1 — pure compute, all stages are mocked, no API keys.

| Scenario  | Shape                                                              |
|-----------|--------------------------------------------------------------------|
| `callbot` | `customer_audio → vad → stt → llm_router (@graph) → tts`           |

Demonstrates the streaming-scheduler patterns most production
voice-bots need: an N-to-M generator (`vad` emits a variable number of
segments per chunk), a nested `@graph` in the middle of the pipeline,
and an async generator on the TTS tail.

## Project layout

```
ex15_callbot_streaming/
├── pyproject.toml    # operonx>=0.6.2 (tier 1)
├── README.md
└── main.py
```

## Run

```bash
uv sync
uv run python main.py
```
