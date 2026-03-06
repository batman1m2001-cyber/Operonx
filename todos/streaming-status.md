# Streaming Architecture — Implementation Status

Last updated: 2026-03-06

## Phase 1 — Streaming Scheduler ✅ COMPLETE
## Phase 2 — Iteration Op Removal ✅ COMPLETE
## Phase 3 — GraphOp.loop() ✅ COMPLETE

## Phase 4 — Output Layer + Telemetry ✅ COMPLETE

### engine.stream() async generator ✅
- `engine.py`: Added `stream()` method yielding `{"type": "token"/"done", ...}` events
- `context.py`: Added `_output_queue` ContextVar for streaming event delivery
- `scheduler.py`: Forward yield/done events to `_output_queue` when set

### LLMOp generator conversion ✅
- `llm.py`: When `stream=True`, LLMOp uses `_create_stream_core()` async generator
- Scheduler drives it via `_drive_generator()`, yields per-token dicts
- Final yield includes complete metadata (usage, model, cost, etc.)

### hush-serve streaming handlers ✅
- Removed `from __future__ import annotations` from all 5 route handlers (broke FastAPI dynamic Pydantic models)
- `stream_handler.py`: Uses `engine.stream()` for SSE delivery
- `ws_handler.py`: Uses `engine.stream()` for WebSocket delivery
- `test_stream.py`: Added comprehensive stream/ws tests

### hush-telemetry streaming-aware tracers ✅
- Fixed `context_id` → `context` field name mismatch in both tracers
- Langfuse tracer: stream_items nest under spawning generator (via `spawned_by`)
- OTEL tracer: same spawned_by nesting fix
- Both tracers surface streaming metadata (kind, yield_count, spawned_by, depth)
- Stream items from LLMOp generators rendered as spans, not generations

### Trace collector generator output aggregation ✅
- `collector.py`: Generator records aggregate outputs from stream contexts (no more nulls)

### Tutorial example ✅
- `tutorial/examples/19_streaming_tracing.py`: 4 examples (run, stream, async gen, Langfuse tracing)

---

## Remaining Work

### Tests to validate
- [ ] `cd hush-core && uv run -m pytest` — full core test suite
- [ ] `cd hush-providers && uv run -m pytest` — provider tests
- [ ] `cd hush-serve && uv run -m pytest` — serve tests
- [ ] `cd hush-telemetry && uv run -m pytest` — telemetry tests

### Documentation
- [ ] Update `tutorial/docs/` with streaming chapter (Vietnamese)
- [ ] Update `architecture/` with engine.stream() docs
- [ ] Update CLAUDE.md files with streaming patterns
