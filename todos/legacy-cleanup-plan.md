# Legacy Cleanup & callback Refactor Plan

## Context

The streaming architecture (event-queue scheduler, tuple contexts, generator ops) has made
three legacy packages redundant. This plan covers their removal and the replacement of
`streams/` with the `callback` callback pattern.

**Branch**: `feat/stream-architecture`
**Depends on**: streaming-tracing-plan.md (Steps 1-5 complete)

---

## Phase 1: Delete `tracers/` (legacy tracing)

**Why**: Fully replaced by `tracing/` — same functionality, cleaner design. No external
consumers remain except 2 examples and `__init__.py` re-exports.

### 1.1 Delete directory

```
DELETE hush-core/hush/core/tracers/
  ├── __init__.py
  ├── base.py        # BaseTracer, _TRACER_REGISTRY, register_tracer
  ├── local.py       # LocalTracer (replaced by tracing/local.py)
  ├── media.py       # MediaAttachment (self-contained in hush-telemetry)
  └── store.py       # TracerStore (replaced by tracing/flush_worker.py)
```

### 1.2 Delete legacy tests

```
DELETE hush-core/tests/tracers/test_background.py   # tests BackgroundProcess (legacy)
```

Review `hush-core/tests/tracers/` — delete entire directory if all tests cover legacy code.

### 1.3 Update `hush-core/hush/core/__init__.py`

Remove:
```python
from hush.core.tracers import (
    MEDIA_KEY,
    BaseTracer,
    MediaAttachment,
    get_registered_tracers,
    register_tracer,
    serialize_media_attachments,
)
```

Remove from `__all__`:
```python
"MEDIA_KEY", "BaseTracer", "MediaAttachment",
"get_registered_tracers", "register_tracer", "serialize_media_attachments",
```

**Decision**: Do NOT re-export `MediaAttachment` — it's only used by `hush-telemetry`'s
`LangfuseTracer`, which handles media independently via `langfuse.media.LangfuseMedia`.

### 1.4 Update example files

| File | Change |
|------|--------|
| `hush-core/examples/hello_world.py` | `from hush.core.tracers import LocalTracer` → `from hush.core.tracing import LocalTracer` |
| `hush-core/examples/trace_viewer_demo.py` | Same import fix + remove `DEFAULT_DB_PATH` refs from `background` |

---

## Phase 2: Delete `background/` (legacy flush system)

**Why**: Fully replaced by `tracing/flush_worker.py`. The old system used a subprocess
with SQLite queue — over-engineered. New system uses a simple ThreadPoolExecutor.

### 2.1 Delete directory

```
DELETE hush-core/hush/core/background/
  ├── __init__.py
  ├── db.py           # SQLite queue (init_db, write_traces_batch, etc.)
  ├── flush.py         # dispatch_flush, rebuild_flush_data
  ├── process.py       # BackgroundProcess, _PipeQueue
  └── worker.py        # _background_worker loop
```

### 2.2 Update `hush-core/hush/core/__init__.py`

Remove:
```python
from hush.core.background import (
    BackgroundProcess,
    get_background,
    shutdown_background,
)
```

Remove from `__all__`:
```python
"BackgroundProcess", "get_background", "shutdown_background",
```

### 2.3 Update dependents

`tracers/store.py` imports `background` — but `tracers/` is already deleted in Phase 1.
No remaining dependents.

---

## Phase 3: Delete `streams/` (queue-based streaming)

**Why**: The `STREAM_SERVICE` pattern (session/request/channel queues) is replaced by the
streaming architecture's built-in queue cells + scheduler. The only remaining use case —
pushing LLM token chunks to consumers — will be handled by `callback` callbacks (Phase 4).

### 3.1 Delete directory

```
DELETE hush-core/hush/core/streams/
  ├── __init__.py      # STREAM_SERVICE singleton
  ├── base.py          # BaseStreamingService ABC
  └── memory.py        # InMemoryStreamService (asyncio.Queue)
```

### 3.2 Update `hush-core/hush/core/__init__.py`

Remove:
```python
from hush.core.streams import (
    STREAM_SERVICE,
)
```

Remove from `__all__`:
```python
"STREAM_SERVICE",
```

### 3.3 Update `hush-core/hush/core/engine.py`

Remove:
```python
from hush.core.streams import STREAM_SERVICE
# ...
await STREAM_SERVICE.end_request(request_id, session_id=session_id)
```

### 3.4 Update `hush-providers/hush/providers/ops/llm.py`

Remove `STREAM_SERVICE` import and all `STREAM_SERVICE.push()` / `STREAM_SERVICE.end()`
calls from `_accumulate_stream()` and `run()`.

**Important**: LLMOp streaming (token-by-token to consumers) is deferred to Phase 4.
For now, LLMOp accumulates chunks internally and returns the final result — streaming
to external consumers (WebSocket, SSE) is not yet supported in the new architecture.

### 3.5 Update `hush-serve/`

| File | Change |
|------|--------|
| `hush-serve/hush/serve/routes/ws_handler.py` | Remove `STREAM_SERVICE` import + consumption loop. Add `# TODO: replace with callback` |
| `hush-serve/hush/serve/routes/stream_handler.py` | Same |

These handlers will be rewritten in Phase 4 when `callback` is implemented.

### 3.6 Update `hush-providers/tests/test_llm_op.py`

Remove or skip `test_llm_streaming_with_token_verification` (line ~105) — it tests
`STREAM_SERVICE` integration which no longer exists.

### 3.7 Delete architecture docs

```
DELETE architecture/streams/streaming-system.md
```

Update cross-references in:
- `architecture/providers/workflow-ops.md` (remove STREAM_SERVICE refs)
- `architecture/engine/execution-flow.md` (remove STREAM_SERVICE.end_request ref)

### 3.8 Update tutorial docs

- `tutorial/docs/04-llm-integration.md` — remove `STREAM_SERVICE.subscribe()` example,
  add note that streaming to consumers uses `callback` (coming in Phase 4)

---

## Phase 4: Implement `callback` callback

**Why**: Replace `STREAM_SERVICE` with a simpler, closure-based pattern. The scheduler
already knows when a generator yields — it just needs to fire a callback.

### 4.1 Design

```python
# User API
async def on_chunk(op_name: str, result: dict, ctx: tuple):
    """Called each time a generator op yields."""
    await websocket.send_json({"op": op_name, "data": result})

result = await engine.run(
    inputs={"query": "hello"},
    callback=on_chunk,        # optional callback
)
```

**Key properties**:
- **Zero overhead when unused**: No callback = no cost (simple `if` check)
- **Closure-based isolation**: Each `engine.run()` call gets its own callback scope.
  No session/request/channel key management needed.
- **Concurrent-safe**: Multiple `engine.run()` calls each have their own callback —
  no shared state, no cross-talk.
- **Multi-stream**: If multiple generators yield, the callback receives `op_name` + `ctx`
  so the consumer can route chunks to the right stream.

### 4.2 Engine changes (`engine.py`)

```python
class Hush:
    async def run(
        self,
        inputs: dict,
        tracer=None,
        callback: Optional[Callable] = None,   # NEW
        **kwargs,
    ):
        # ... existing code ...
        scheduler = EventQueueScheduler(graph, state, callback=callback)
        result = await scheduler.run()
        # ...
```

### 4.3 Scheduler changes (`scheduler.py`)

In the event loop where generator yields are processed:

```python
class EventQueueScheduler:
    def __init__(self, graph, state, callback=None):
        self._callback = callback
        # ...

    async def _process_yield(self, op, result, ctx):
        # ... existing: enqueue downstream ops ...

        # Fire callback if registered
        if self._callback is not None:
            if inspect.iscoroutinefunction(self._callback):
                await self._callback(op.full_name, result, ctx)
            else:
                self._callback(op.full_name, result, ctx)
```

### 4.4 Update `hush-serve/` handlers

Rewrite `ws_handler.py` and `stream_handler.py` to use `callback`:

```python
# ws_handler.py
async def websocket_endpoint(ws: WebSocket, workflow, inputs):
    await ws.accept()

    async def on_chunk(op_name, result, ctx):
        await ws.send_json({"op": op_name, "chunk": result, "ctx": ctx})

    result = await engine.run(inputs=inputs, callback=on_chunk)
    await ws.send_json({"type": "done", "result": result})
    await ws.close()
```

```python
# stream_handler.py (SSE)
async def sse_endpoint(request, workflow, inputs):
    async def event_generator():
        queue = asyncio.Queue()

        async def on_chunk(op_name, result, ctx):
            await queue.put({"op": op_name, "chunk": result})

        async def run_workflow():
            result = await engine.run(inputs=inputs, callback=on_chunk)
            await queue.put(None)  # signal done

        task = asyncio.create_task(run_workflow())

        while True:
            item = await queue.get()
            if item is None:
                break
            yield {"event": "chunk", "data": json.dumps(item)}

    return EventSourceResponse(event_generator())
```

### 4.5 Update LLMOp (optional, future)

If LLMOp needs to push per-token chunks through `callback`, it would need access to the
callback. Options:
1. Pass `callback` through state (cleanest — scheduler sets it)
2. LLMOp yields tokens as a generator (scheduler handles it naturally)

Option 2 is preferred — make LLMOp a generator when `stream=True`:
```python
async def run(self, **inputs):
    if self.stream:
        async for chunk in self._stream_completion(inputs):
            yield {"chunk": chunk["content"], "done": False}
        yield {"content": full_content, "done": True}
    else:
        return await self._run_completion(inputs)
```

This integrates naturally with the streaming scheduler — no special cases needed.

---

## Phase 5: Update docs & CLAUDE.md

### 5.1 `hush-core/CLAUDE.md`

- Remove `background/` and `tracers/` from module structure (mark as deleted)
- Remove `streams/` from module structure
- Add `callback` callback documentation
- Update tracing section if needed

### 5.2 Root `CLAUDE.md`

- No changes needed (doesn't reference these modules directly)

### 5.3 `hush-serve/CLAUDE.md`

- Update streaming docs to reference `callback` instead of `STREAM_SERVICE`

### 5.4 Architecture docs

- Delete `architecture/streams/streaming-system.md`
- Update `architecture/engine/execution-flow.md` — replace STREAM_SERVICE with callback
- Update `architecture/providers/workflow-ops.md` — replace STREAM_SERVICE streaming docs

---

## Execution Order

| Step | Phase | Risk | Reversible |
|------|-------|------|-----------|
| 1 | Phase 1: Delete `tracers/` | Low — fully replaced | Yes (git) |
| 2 | Phase 2: Delete `background/` | Low — fully replaced | Yes (git) |
| 3 | Phase 3: Delete `streams/` | Medium — breaks hush-serve streaming | Yes (git) |
| 4 | Phase 5: Update docs | Low | Yes |
| 5 | Phase 4: Implement `callback` | Medium — new feature | Yes (git) |

**Phases 1-2** can be done immediately — zero risk, pure cleanup.
**Phase 3** breaks `hush-serve` streaming temporarily until Phase 4 is complete.
**Phase 4** is the main new feature — should be done when `hush-serve` streaming is needed.
**Phase 5** can be done incrementally alongside other phases.

## Validation

After each phase, run:
```bash
cd hush-core && uv run -m pytest          # all core tests pass
cd hush-providers && uv run -m pytest      # provider tests pass (skip STREAM_SERVICE test)
cd hush-serve && uv run -m pytest          # serve tests pass (after Phase 4)
```
