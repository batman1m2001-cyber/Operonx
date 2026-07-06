# Tracing V2 — Simplification Plan (stripped)

> **Purpose**: Replace the ~3.5k LOC tracing/telemetry surface with a **~40 LOC engine module + ~30 LOC reference sink**. Same debug + monitor capabilities. Zero deprecation shims. Log-like explicit ergonomics.

Owner: thanglq. Sole consumer: `educa-reminder-agent`.

---

## 1. Design principles

1. **Tracing is structured logging.** Explicit `event()` or `span()` calls, no auto-instrumentation.
2. **The engine emits events; the sink renders them.** Engine emits a `TraceEvent` stream; sink turns it into Langfuse spans (or logs, or whatever).
3. **Trace hierarchy is author-declared via paths.** Not derived from graph edges, GraphOp containment, or runtime dispatch chains.
4. **Absent config = op invisible.** No auto-emission. Author calls `event()` or `span()` when they want to see something.
5. **No deprecation warnings, no shim window.** One API, cleanly cut over.

---

## 2. The whole engine surface — 5 fields, 2 functions

All in **`operonx/core/trace.py`** (~120 LOC):

```python
@dataclass
class TraceEvent:
    trace_id: str
    path: str          # e.g. "speech/stt"  — where in the trace tree
    kind: str          # "input" | "output" | "log" | "error" | "call"
    time: float        # perf_counter timestamp
    data: dict         # for "call": {"inputs":..., "outputs":...};
                       # for others: user-supplied dict

Sink = Callable[[TraceEvent], None]

_current: ContextVar[tuple[str, Sink] | None] = ContextVar("t", default=None)

# Individual timeline point → Langfuse EVENT
def event(path: str, data: dict, kind: str = "log") -> None: ...

# Paired atomic call → Langfuse SPAN with input+output
def span(path: str, *, input: dict = None, output: dict = None) -> None: ...

class TraceRecorder:
    """In-memory sink for tests / dev."""
    def __init__(self): self.events: list[TraceEvent] = []
    def __call__(self, ev): self.events.append(ev)
```

Public export: `from operonx import event, span, TraceEvent, TraceRecorder`.

**Two verbs, two Langfuse observation types:**

| Verb | Signature | Renders as | Use for |
|---|---|---|---|
| `event()` | `(path, data, kind=...)` | Langfuse **EVENT** | streaming, M-in/N-out, mid-op logs |
| `span()` | `(path, *, input=..., output=...)` | Langfuse **SPAN** | atomic sync-shaped call |

---

## 3. Author API — two verbs, always explicit

### 3.1 Atomic sync op → `span()`

```python
@op
def add(a, b):
    result = a + b
    span("math/add", input={"a": a, "b": b}, output={"result": result})
    return {"result": result}
```

One Langfuse SPAN with both `input` and `output` fields set. Best for sync ops where input+output are known together.

### 3.2 Streaming (M inputs / N outputs) → `event()`

```python
@op
async def stt(audio_stream):
    event("speech/stt", {"audio_id": id}, kind="input")
    async for chunk in audio_stream:
        partial = recognize(chunk)
        event("speech/stt", {"partial": partial}, kind="output")   # N Langfuse events
    event("speech/stt", {"final": full}, kind="output")
```

Each `event()` fires a distinct Langfuse EVENT with `body.input` (for `kind="input"`) or `body.output` (for `kind="output"`) filled.

### 3.3 Error

```python
@op
def risky(x):
    try:
        return process(x)
    except Exception as e:
        event("risky", {"error": str(e)}, kind="error")
        raise
```

### 3.4 Mid-op log (annotation)

```python
@op
def stt(audio):
    result = recognize(audio)
    event("speech/stt", {"confidence": result.conf}, kind="log")  # timeline annotation
    return {"result": result.text}
```

Default `kind="log"` — no need to specify.

### 3.5 Selective — you write only what you want

You pick per call. No `vars` whitelist, no config, no filtering rules. If you want a subset:

```python
event("math/foo", {"a": a, "c": c}, kind="output")   # a and c, no b
```

Or for a `span()`:

```python
span("math/foo", input={"a": a}, output={"c": c})   # only these keys land on the span
```

### 3.6 Grouping via path

Path segments create folder-like hierarchy in the sink:

```python
span("speech/normalize", input=..., output=...)
span("speech/stt",       input=..., output=...)
span("state/merge",      input=..., output=...)   # "speech" and "state" become virtual folders
```

### 3.7 Same path, multiple events

Multiple `event()` at the same path land as multiple Langfuse events under the leaf container span. All appear on the timeline in emit order.

Multiple `span()` at the same path is fine but usually indicates the wrong shape — spans represent atomic calls; if you're firing several at the same path, consider distinct paths or switch to `event()` calls.

### 3.8 Same path, different ctx → distinct sibling spans

For loop iterations, retries, or concurrent invocations at the same op path, pass a distinct ``ctx=``:

```python
for i, item in enumerate(items):
    span("loop/step", input={"item": item},
         output={"result": process(item)}, ctx=f"iter_{i}")
```

Sink renders as sibling spans named ``step[iter_0]``, ``step[iter_1]``, …
under one shared ``loop`` container. ``ctx=None`` (default) is the single
context — all emits at that path aggregate as usual.

Works for ``event()`` too:

```python
event("speech/stt", {"audio_id": "A"}, kind="input", ctx="call_A")
event("speech/stt", {"partial": "..."}, kind="output", ctx="call_A")
event("speech/stt", {"audio_id": "B"}, kind="input", ctx="call_B")   # → stt[call_B]
```

### 3.9 Debug mode — just add event() or span() calls where you need them

No engine-level "trace everything" flag. Same principle as `log.debug()` — drop the calls where you want visibility.

---

## 4. Sink protocol

### 4.1 Contract

```python
sink(event: TraceEvent) -> None
```

Sync callable. Fires from the op's execution context (ContextVar-scoped). Batching / queueing / async offload is the sink's problem.

### 4.2 Optional `sink.flush(trace_id: str) -> None`

Called by the consumer at trace end (e.g. ws handler `finally` block) to send any queued span updates and close open observations.

### 4.3 Rendering rule for kinds

Sink keys observations on `(trace_id, path)`. Path segments always create nested container spans (folder tree). The leaf position is either upgraded to a call span (`kind="call"` from `span()`) or holds a stream of Langfuse events (from `event()`):

| Kind (from) | Rendering |
|---|---|
| `"call"` (from `span()`) | **Langfuse SPAN** at leaf path with `body.input` + `body.output` + `start_time` + `end_time`. Atomic. |
| `"input"` (from `event()`) | Langfuse EVENT under leaf span with `body.input=data`, distinguishing name (`input:first_key`). |
| `"output"` (from `event()`) | Langfuse EVENT with `body.output=data`, name (`output:first_key`). |
| `"error"` (from `event()`) | Langfuse EVENT with `body.output=data` + `level=ERROR`. Also marks leaf container span ERROR. |
| `"log"` (from `event()`) | Langfuse EVENT with `body.metadata=data`. Doesn't close anything. |

`flush(trace_id)` — closes any observations without `end_time` set with a WARNING and drains the ingestion batch.

### 4.4 Media handling (sink policy)

Sink decides. Reference `LangfuseSink` policy:

- Auto-detect binary values (`bytes` / `bytearray` / `memoryview` > 4 KB, `numpy.ndarray`) → upload via `client.upload_media()`, replace with `@@@langfuseMedia:...@@@` token.
- Everything else → inline in span I/O.

---

## 5. Reference `LangfuseSink` (~60 LOC target)

Lives in `operonx/telemetry/sinks/langfuse.py`. Builds Langfuse ingestion events per TraceEvent, batches, flushes on demand.

Wire in consumer (callbot):

```python
from operonx.telemetry.sinks import LangfuseSink
from operonx.core.registry import ResourceHub

sink = LangfuseSink(client=ResourceHub.instance().get("langfuse:edupia"))

async def ws_call(...):
    trace_id = call_id
    try:
        await engine.run(inputs=..., sink=sink, trace_id=trace_id)
    finally:
        sink.flush(trace_id)
```

---

## 6. Langfuse rendering — what you see in the UI

### 6.1 Sync op with 1 input + 1 output

```
Span: math/add
  input:  {a:1, b:2}
  output: {result:3}
  duration: 0.1 ms
  Timeline:
    t=0.0  input   {a:1, b:2}
    t=0.1  output  {result:3}
```

### 6.2 Streaming op with M inputs, N outputs at the same path

```
Span: speech/stt
  input:  {chunk:0}      ← first input (summary)
  output: {final:"..."}  ← last output (summary)
  Timeline (all M+N events preserved):
    t=0.0  input   {chunk:0}
    t=0.1  input   {chunk:1}
    t=0.2  output  {partial:"..."}
    t=0.3  input   {chunk:2}
    ...
    t=0.7  output  {final:"..."}
```

### 6.3 Path grouping (folder-like)

```
call_c1 (trace)
├── speech               ← virtual container span (no fields of own)
│   ├── normalize
│   ├── stt
│   └── quick_detect
├── state
│   ├── merge
│   ├── state_transition
│   └── generate_rule
└── tts
```

Intermediate segments (`speech`, `state`) become virtual container spans, created lazily by the sink. Their `start_time` = earliest child event; `end_time` = latest child event.

### 6.4 Media (binary blob)

```
Span: speech/stt
  input:  {audio: @@@langfuseMedia:type=audio/wav|id=<mediaId>|source=bytes@@@}
                                    ↓ click in Langfuse UI to play/download
  output: {result: "xin chào"}
```

### 6.5 Error

```
Span: risky
  level: ERROR
  output: {error: "TypeError: ..."}
  Timeline:
    t=0.0  input   {x: bad_value}
    t=0.5  error   {error: "..."}
```

---

## 7. Engine integration

Two changes to `operonx.core.engine.Operon`:

1. **`run(inputs, *, sink=None, trace_id=None, ...)`** — accept sink + trace_id. Delete `tracer=`.
2. Set the tracing ContextVar around the run:
   ```python
   token = _current.set((trace_id or str(uuid.uuid4()), sink) if sink else None)
   try:
       return await self._run_inner(...)
   finally:
       _current.reset(token)
   ```

No BaseOp changes needed — ops just call `event()` or `span()` in their bodies. If no sink is set, the ContextVar returns None and `event()` or `span()` no-ops (zero cost).

---

## 8. Test coverage

### 8.1 Unit tests (`tests/internal/core/test_tracing.py`)

- `event()` or `span()` with no sink installed → no-op, no errors
- `event()` or `span()` fires event to sink when installed
- Kind defaults to `"log"`
- `data` payload passed through unchanged
- `path` passed through unchanged
- `trace_id` matches the one from ContextVar
- `time` is a monotonic float from `perf_counter()`
- `TraceRecorder` accumulates events in order
- ContextVar reset after sink scope
- Multiple events same path (M/N) — all delivered
- Nested paths in events

### 8.2 Engine integration tests (`tests/internal/core/test_tracing_engine.py`)

- `Operon.run(sink=recorder, trace_id="t1")` — events tagged with `"t1"`
- No `sink=` provided — event() no-ops during run
- Trace_id auto-generated if not provided (UUID)
- Concurrent runs get isolated trace_ids
- Sink called for every event() call inside an op body
- Exceptions inside sink don't break op execution (sink is best-effort)

### 8.3 Langfuse mock (integration — hits real Edupia Langfuse)

Runnable script: `scripts/mock_trace_langfuse.py`. Emits every case documented in §6 as separate traces. Prints Langfuse UI URLs. See §11.

---

## 9. What we delete

- `operonx/core/tracing/` — everything: `emitter.py`, `pipeline.py`, `legacy.py`, `trace_filter.py`, `events.py`, `processors/*`, `exporters/*`
- `operonx/telemetry/tracers/langfuse.py`
- `operonx/telemetry/exporters/langfuse.py`
- `operonx/telemetry/backends/langfuse/prompt_manager.py`
- All tests under `tests/internal/core/tracing/`, `tests/internal/telemetry/`
- All `TraceFilter` / `LangfuseTracer` imports from callbot

Target: **~3.4k LOC removed**.

## 10. What we keep

- `operonx/telemetry/backends/langfuse/client.py` — raw HTTP client (ingest + upload_media); reference sink uses it
- `operonx/telemetry/backends/langfuse/config.py` — Pydantic config for creds
- `operonx/core/registry/` — resource hub still resolves `langfuse:default` for the client

## 11. What we add

- `operonx/core/tracing.py` — ~40 LOC
- `operonx/telemetry/sinks/__init__.py` — new package
- `operonx/telemetry/sinks/langfuse.py` — reference sink (~60 LOC)
- `tests/internal/core/test_tracing.py` — unit tests
- `tests/internal/core/test_tracing_engine.py` — engine integration
- `scripts/mock_trace_langfuse.py` — hits Langfuse Edupia with every documented case; prints URLs

Net delta: ~3400 removed, ~200 added.

---

## 12. Execution phases

Test suite green (or expected N-fewer for removed tests) between each phase.

- **Phase 1** — write `operonx/core/tracing.py` + unit tests. No engine wiring yet.
- **Phase 2** — wire engine (`sink=`, `trace_id=` on `run()` / `start()`). Delete old `tracer=` param.
- **Phase 3** — write reference `LangfuseSink` + mock test script hitting Edupia. **Ship the URL for review.**
- **Phase 4** — review Langfuse rendering. Adjust sink if needed based on visual.
- **Phase 5** — migrate callbot: swap `LangfuseTracer(...)` for `LangfuseSink(...)`, add `event()` or `span()` calls in ops that need them.
- **Phase 6** — delete the old tracing/telemetry world (§9). Full test run.

---

## 13. Success criteria

- **Langfuse Edupia shows all §6 case renderings** correctly from the mock script.
- **Callbot end-to-end call** produces a visible trace tree with input/output/media where expected.
- **~3k LOC net removed** from operonx.
- **Zero deprecation warnings** in the codebase.
- **Test suite green** (except pre-existing `test_audio_input` failure).

---

## 14. Open questions

1. **Path segment separator** — `/` chosen. Alt: `.`, `>`. Confirmed `/` per prior discussion.
2. **`event()` or `span()` called outside any op body / sink scope** — no-op (safe drop). Confirmed.
3. **Sink exceptions** — engine catches, logs, continues. Tracing never breaks execution.
4. **Async sinks** — not supported. Sink is sync. Consumer wraps in queue if needed.
5. **Sampling / percentage drop** — sink's problem. User writes `if random.random() > 0.1: return` in their sink.
