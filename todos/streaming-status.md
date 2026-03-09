# Streaming Architecture — Status

Last updated: 2026-03-10
Branch: `feat/stream-architecture`

## What's Done

### Phase 1-4: Core Streaming ✅
- Scheduler drives generator ops via `_drive_generator()`
- `engine.stream()` async generator for real-time token delivery
- LLMOp generator conversion for streaming
- hush-serve streaming handlers (SSE + WebSocket)
- Tutorial examples 19 (streaming tracing) and 20 (callbot)

### Stream-as-Node Refactor ✅ (2026-03-10)

**Goal**: Treat streaming as normal node-based ops. Remove tuple-context broadcast logic, stream depths, and complex collector transformations. Replace with Cell hierarchy fallback + PENDING sentinel.

#### 1. PENDING Sentinel ✅
`hush-core/hush/core/ops/__init__.py`

Ops return `PENDING` to absorb input without triggering downstream:
```python
from hush.core import PENDING

@op
def vad(audio_chunk):
    if is_silence(audio_chunk):
        return PENDING
    return {"speech": extract_speech(audio_chunk)}
```

Scheduler handles `done_pending` event — skips `_activate_successors()`.

#### 2. Cell Context Hierarchy Fallback ✅
`hush-core/hush/core/states/cell.py`

When reading state in context `("main", "[0]")`, if value not found, walks up to `("main",)`:
```python
ctx = context_id
while ctx:
    if ctx in self.contexts:
        return self.contexts[ctx]
    ctx = ctx[:-1]
```

Eliminates `_resolve_ctx()` broadcast logic entirely. Downstream ops in context `[n]` read generator output from `[n]` (direct hit) and batch ops from `("main",)` (fallback).

#### 3. Simplified BaseOp ✅
`hush-core/hush/core/ops/base.py`

- Deleted `_resolve_ctx()` entirely (36 lines)
- Deleted `_stream_depths` slot
- `get_inputs()` simplified: PARENT refs use `parent_context`, everything else uses `context_id` directly — Cell fallback handles the rest

#### 4. Simplified Predecrements ✅
`hush-core/hush/core/ops/graph/graph_op.py`

- Deleted `_build_streaming()` (stream depth computation via topo sort)
- Replaced with `_build_predecrements()` — simple graph reachability per generator
- For each generator, find downstream ops whose predecessors are batch-level (not reachable through generator)
- Deleted `_has_streaming_ops`, `_stream_depths` slots

#### 5. Context Naming `[N]` ✅
`hush-core/hush/core/ops/graph/scheduler.py`

- Stream contexts renamed from `s0`, `s1` to `[0]`, `[1]`
- Human-readable in traces (matches synthetic node display names)

#### 6. Scheduler PENDING Support ✅
`hush-core/hush/core/ops/graph/scheduler.py`

- `_run_op()` checks for `PENDING` return, emits `done_pending` event
- `_dispatch_one()` inline ops: PENDING returns empty successor list
- Event loop handles `done_pending` — decrements active_count, skips successors
- `_collect_outputs()` uses leaf-context filtering (not max-depth)

#### 7. Auto-build GraphOp ✅
`hush-core/hush/core/ops/graph/graph_op.py`

- `_is_building` guard now auto-calls `self.build()` instead of raising ValueError
- Fixes chain() and @graph functions returning unbuilt graphs

### Collector Simplification ✅
`hush-core/hush/core/tracing/collector.py`

- Context grouping still creates synthetic `[N]` nodes for hierarchical visualization
- Generator-parented: `[N]` nodes are children of the spawning generator, not siblings
- Skip pending: generators with `yield_count==0` removed by default
- Removed `_is_stream_segment` for `sN` format, uses `[N]` format
- Simplified `_sample_stream_nodes()` in flush_worker.py — caps context groups per generator parent, cascade-removes descendants via BFS

### chain() @graph Refactor ✅
`hush-providers/hush/providers/ops/chain.py`

- Replaced manual GraphOp wrapper with `@graph` decorator directly on `chain()`
- Removed `register_skip(chain)` (handled by `@graph`)
- Removed `enable_thinking` param (LLMOp doesn't support it)
- `contain_generation` no longer defaults to True (must be explicit)
- Template variables reach PromptOp via `{"*": PARENT}` wildcard on graph state

### Telemetry Cleanup ✅
- `langfuse.py`: Removed `stream_context`/`stream_item` kind comments
- `otel.py`: Removed `spawned_by`, `depth` attributes from streaming spans

### Langfuse HTTP API Fix ✅ (prior commits)
- Required headers, UTC timestamp format, error propagation, sibling ordering

### Tests ✅
- `hush-core`: 619 passed, 1 skipped
- `hush-telemetry`: 53 passed
- Tutorial examples 01-16: all pass (except 08/15 network timeout, 13 pre-existing bug)

## Current Trace Output (Callbot Example)

Pipeline: `audio(yields=5) >> vad(yields=0or1) >> stt >> router(graph) >> tts(yields=N)`

```
callbot (trace)
└── audio (generator, yields=5)
    ├── [2] (context)                    ← chunks 0,1,3 dropped (vad yields=0)
    │   └── v (generator, yields=1)
    │       └── [0] (context)
    │           ├── transcribe
    │           ├── router (graph)
    │           │   ├── c (classify_intent)
    │           │   └── h (handle_intent)
    │           └── speak (generator, yields=7)
    └── [4] (context)
        └── v (generator, yields=1)
            └── [0] (context)
                ├── transcribe
                ├── router (graph)
                │   ├── c
                │   └── h
                └── speak (generator, yields=7)
```

## Open Issues

### Display names from @graph
Inside `@graph def llm_router`, ops are named by variable: `c = classify_intent(...)` → display name is `c`, not `classify_intent`. Auto-naming behavior — use descriptive variable names or explicit `name=` parameter.

## Key Files

| File | What |
|------|------|
| `hush-core/hush/core/ops/__init__.py` | PENDING sentinel |
| `hush-core/hush/core/states/cell.py` | Cell hierarchy fallback |
| `hush-core/hush/core/ops/base.py` | Simplified get_inputs() (no _resolve_ctx) |
| `hush-core/hush/core/ops/graph/graph_op.py` | Simplified predecrements, auto-build |
| `hush-core/hush/core/ops/graph/scheduler.py` | PENDING support, [N] contexts, leaf-context collection |
| `hush-core/hush/core/tracing/collector.py` | Context grouping, generator parenting, skip pending |
| `hush-core/hush/core/tracing/flush_worker.py` | Simplified stream sampling |
| `hush-providers/hush/providers/ops/chain.py` | chain() with @graph decorator |
| `hush-telemetry/hush/telemetry/tracers/langfuse.py` | HTTP batch builder, sibling ordering |
| `hush-telemetry/hush/telemetry/tracers/otel.py` | Cleaned streaming attributes |

## How to Resume

```bash
cd ~/Work/Hush-ai
git checkout feat/stream-architecture

# Run tests
cd hush-core && uv run -m pytest -v
cd ../hush-telemetry && uv run -m pytest -v
cd ../hush-providers && uv run -m pytest -v

# Run callbot example (shows trace tree + Langfuse trace)
cd ../tutorial && uv run python examples/20_callbot_streaming.py
```
