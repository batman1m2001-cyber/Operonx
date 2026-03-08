# Streaming Architecture — Status

Last updated: 2026-03-09
Branch: `feat/stream-architecture` @ commit `289bca1`

## What's Done

### Phase 1-4: Core Streaming ✅
- Scheduler drives generator ops via `_drive_generator()`
- `engine.stream()` async generator for real-time token delivery
- LLMOp generator conversion for streaming
- hush-serve streaming handlers (SSE + WebSocket)
- Tutorial examples 19 (streaming tracing) and 20 (callbot)

### collect_tree() Redesign ✅
All changes in `hush-core/hush/core/tracing/collector.py`:

- **Change A — Named contexts**: `audio:2` not `[2]`, named after spawning generator via `_find_context_spawner()`
- **Change B — Drop yield records**: Generator re-executions in stream contexts (yield records) are skipped. A yield record = `kind=="stream_item" AND is_gen AND (op_name, parent_ctx) in executed_pairs`
- **Change C — Drop zero-yield generators**: Generators with `yield_count==0` inside stream contexts are removed (e.g. VAD on silence chunks)
- **Change D — Upgrade kinds**: Generators in stream contexts upgraded from `stream_item` → `generator`. GraphOps upgraded to `graph`
- **Change E — Single-child collapse**: If a `stream_context` has exactly 1 child, child absorbs the context (e.g. `fmt[0]` instead of `gen:0 > fmt`)
- **Change F — Flatten single-yield**: If a generator has `yield_count==1`, its inner context is removed and children promoted up

### Generator Output Aggregation Fix ✅
- **Bug**: `collect_tree()` line 405 used `kind == "generator"` for output aggregation, but nested generators (v, speak) still have `kind == "stream_item"` at that point — Change D upgrade happens later
- **Fix**: Changed to `is_gen` (checks `op_name in gen_ops` directly)
- **Impact**: Without this, `v` and `speak` had null outputs in Langfuse traces

### Langfuse HTTP API Fix ✅
All changes in `hush-telemetry/`:

**Root cause of missing traces**: After switching from Langfuse SDK to custom HTTP client, traces were accepted (201) but silently dropped by Langfuse cloud.

**Fix 1 — Required headers** (`backends/langfuse/client.py`):
```python
headers={
    "Content-Type": "application/json",
    "Authorization": f"Basic {self._auth}",
    "x_langfuse_sdk_name": "python",        # ← REQUIRED
    "x_langfuse_sdk_version": "hush",        # ← REQUIRED
    "x_langfuse_public_key": self._config.public_key,  # ← REQUIRED
}
```
Discovered by reverse-engineering the Langfuse Python SDK (`langfuse.request.LangfuseClient.generate_headers()`). Without these 3 headers, Langfuse cloud silently drops all events.

**Fix 2 — UTC timestamp format** (`collector.py`, `tracers/langfuse.py`):
- Langfuse SDK serializes UTC as `...Z`, not `...+00:00`
- `_format_time()` now does `.replace("+00:00", "Z")`
- `now_iso` fallback also uses this format

**Fix 3 — Error propagation** (`flush_worker.py`, `tracers/langfuse.py`):
- `FlushWorker.submit()` now returns `concurrent.futures.Future`
- `FlushWorker.wait(timeout)` collects errors from all futures
- `LangfuseTracer.flush()` raises `RuntimeError` on partial API errors (was silently swallowed)
- Examples use `get_flush_worker().wait(timeout=30)` instead of `asyncio.sleep(3)`

**Fix 4 — Sibling ordering** (`tracers/langfuse.py`):
- Langfuse sorts children by `startTime`, truncates to milliseconds
- Fast ops within same ms appear in arbitrary (alphabetical) order
- Fix: assign `parent_start + (child_index * 1ms)` per sibling group
- Nodes list from `collect_tree()` is already in execution order

### Tests ✅
- `hush-core/tests/tracing/test_collector.py` — 15 tests (basic, nested graph, callbot integration)
- `hush-telemetry/tests/test_langfuse_tracer.py` — 21 tests (error propagation, batch building)
- All pass: `cd hush-core && uv run -m pytest tests/tracing/ -v` (40 passed)
- All pass: `cd hush-telemetry && uv run -m pytest -v` (21 passed)

## Current Trace Output (Callbot Example)

Pipeline: `audio(yields=5) >> vad(yields=0or1) >> stt >> llm_router(graph) >> tts(yields=N)`

```
callbot (trace)
├── audio (generator, yields=5)
├── audio:2 (context)                    ← chunks 0,1,3 dropped (vad yields=0)
│   ├── v (generator, yields=1)
│   ├── transcribe
│   ├── router (graph)
│   │   ├── c (classify_intent)
│   │   └── h (handle_intent)
│   └── speak (generator, yields=7)
└── audio:4 (context)
    ├── v (generator, yields=1)
    ├── transcribe
    ├── router (graph)
    │   ├── c
    │   └── h
    └── speak (generator, yields=7)
```

All nodes have proper I/O data. Ordering matches execution flow in Langfuse UI.

## Open Issues

### Remaining ordering edge case
Langfuse ordering fix uses `parent_start + (child_index * 1ms)`. First child keeps real time, subsequent children get bumped. This works for most cases but could be improved — currently skips `idx==0` (first child keeps original time which might equal parent's time).

### Display names from @graph
Inside `@graph def llm_router`, ops are named by variable: `c = classify_intent(...)` → display name is `c`, not `classify_intent`. This is auto-naming behavior — the variable name becomes the op name. Users should use descriptive variable names or explicit `name=` parameter.

## Key Files

| File | What |
|------|------|
| `hush-core/hush/core/tracing/collector.py` | `collect_tree()` — Changes A-F, generator aggregation fix |
| `hush-core/hush/core/tracing/flush_worker.py` | `Future` return, `wait()`, stream sampling |
| `hush-core/hush/core/tracing/models.py` | `TraceNode` dataclass |
| `hush-telemetry/hush/telemetry/tracers/langfuse.py` | HTTP batch builder, sibling ordering |
| `hush-telemetry/hush/telemetry/backends/langfuse/client.py` | HTTP client with required headers |
| `hush-core/tests/tracing/test_collector.py` | collect_tree() tests |
| `hush-telemetry/tests/test_langfuse_tracer.py` | Langfuse tracer tests |
| `tutorial/examples/19_streaming_tracing.py` | Simple streaming + Langfuse |
| `tutorial/examples/20_callbot_streaming.py` | Callbot pipeline + trace tree |

## How to Resume

```bash
cd ~/Work/Hush-ai
git checkout feat/stream-architecture
git pull

# Run tests
cd hush-core && uv run -m pytest tests/tracing/ -v
cd ../hush-telemetry && uv run -m pytest -v

# Run callbot example (shows trace tree + Langfuse trace)
cd ../tutorial && uv run python examples/20_callbot_streaming.py
```
