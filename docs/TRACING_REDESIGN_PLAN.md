# Tracing Redesign — Event Stream + Processors + Exporters

**Status**: design proposal — for discussion
**Scope**: `operonx` core (Python first; Rust mirror later)
**Driving consumer**: educa-reminder-agent (callbot — long-lived stream graphs)
**Author**: thanglq + claude (2026-05-05)

This proposal replaces the current tracer/collector/filter/rewriter stack with a three-layer pipeline: **flat event stream → composable processors → exporters that render their own shapes**. Tree-shaped traces remain the default; everything else (timeline, turn-grouped, partial, custom) falls out without touching core.

---

## §1. What's wrong today

| Pain | Where it lives | Why it hurts |
|---|---|---|
| Tree shape is hardcoded into `TraceCollector` | [`collector.py:170-215`](../operonx/core/tracing/collector.py#L170) | Any consumer wanting a different shape (timeline, grouped) has to reverse-engineer the tree |
| `TraceFilter` mixes 7 concerns | [`trace_filter.py`](../operonx/core/tracing/trace_filter.py) | `skip_empty`, `exclude_ops`, `exclude_kinds`, `max_io_size`, `preserve_children_of`, `protected_types`, `rewriters` — none compose; ordering is implicit |
| `rewriters` are an escape hatch bolted onto filtering | same | Educa's `turn_rewriter` is 200 lines doing what should be a 30-line processor |
| `node_type` (trace/span/generation) baked into data | [`models.py:9-67`](../operonx/core/tracing/models.py#L9) | Langfuse-specific concept leaks into every consumer |
| Single flush at scheduler exit | [`engine.py:454-459`](../operonx/core/engine.py#L454) | A 5-min callbot call buffers all spans in memory and emits one giant POST at hangup |
| Cancelled tasks leave spans with `end_time=None` | Phase B brutal cleanup | Langfuse renders phantom open spans; turn_rewriter `min/max` over `None` silently drops timing |
| Media upload blocks the flush thread per-blob | [`langfuse.py:114-156`](../operonx/telemetry/tracers/langfuse.py#L114) | Long calls with many audio refs serialize HTTP uploads at hangup |
| Tracing flag (`state.tracing`) gates per-op metrics | [`engine.py:429`](../operonx/core/engine.py#L429) | Tracer presence and metric collection are coupled — can't sample, can't conditionally trace |

Root cause: **the collected data is already shaped for one consumer (Langfuse)**.

---

## §2. The new contract — four abstractions

```
                    ┌──────────────────┐
                    │  EventEmitter    │  what ops + scheduler call
                    └────────┬─────────┘
                             │ emit(TraceEvent)
                             ▼
                    ┌──────────────────┐
                    │  TracePipeline   │  owns event buffer, runs processors,
                    │                  │  dispatches to exporters
                    └────────┬─────────┘
                             │ Iterable[TraceEvent]
                             ▼
                    ┌──────────────────┐
                    │   Processor 1    │ ─┐
                    ├──────────────────┤  │  pure functions, composable,
                    │   Processor 2    │  │  no shape assumptions
                    ├──────────────────┤  │
                    │      ...         │ ─┘
                    └────────┬─────────┘
                             │ Iterable[TraceEvent]
                             ▼
                ┌──────────┬──────────┬──────────┐
                │ Langfuse │  File    │  OTel    │  each exporter
                │ Exporter │ Exporter │ Exporter │  renders its own shape
                └──────────┴──────────┴──────────┘
```

**Three rules:**
1. Events are **flat and immutable**. No parent pointers, no children — just timestamps and `(op, ctx)` pairs.
2. Processors are **pure**. `Iterable[Event] → Iterable[Event]`. They never know what comes after.
3. Exporters **render their own shape**. Tree, timeline, grouped timeline, custom — whatever the backend wants.

---

## §3. The four abstractions

### 3.1 `TraceEvent`

```python
@dataclass(frozen=True, slots=True)
class TraceEvent:
    event_id: str                # UUID — opaque, used for dedup
    request_id: str              # per engine.start() call
    kind: EventKind              # see below
    op_name: Optional[str]       # None for synthetic events
    ctx: Tuple[str, ...]         # ("main", "[0]", ...) — scheduler ctx tuple
    timestamp: datetime          # always UTC
    payload: Dict[str, Any]      # kind-specific


class EventKind(str, Enum):
    OP_START      = "op_start"
    OP_END        = "op_end"        # payload: {outputs, status: ok|error|cancelled, duration_ms}
    OP_YIELD      = "op_yield"      # payload: {yielded, idx}
    ANNOTATION    = "annotation"    # arbitrary metadata, scoped to current op via ContextVar
    GROUP_START   = "group_start"   # synthetic — emitted by processors
    GROUP_END     = "group_end"
    LLM_USAGE     = "llm_usage"     # payload: {model, prompt_tokens, completion_tokens, cost}
    MEDIA_REF     = "media_ref"     # payload: {handle, mime, size_bytes} — reserved; ops attach Media via OP_START/OP_END payload.media_refs (§3.8)
```

**Why no `node_type` (trace/span/generation)?** Those are Langfuse concepts. The `LangfuseExporter` decides per event by looking at `op_name` + `LLM_USAGE` events. Other backends decide differently.

**Why no parent pointer?** Parent/child is a *rendering* over events, not an attribute. `(op_name, ctx)` + `GROUP_START`/`GROUP_END` markers are enough to reconstruct any tree.

### 3.2 `EventEmitter`

```python
# operonx/core/tracing/emitter.py
_current_emitter_var: ContextVar["EventEmitter"] = ContextVar("operonx_current_emitter")
_current_op_var: ContextVar[Tuple[str, Tuple]] = ContextVar("operonx_current_op")

class EventEmitter:
    """What ops and the scheduler call. Pushes events into the bound pipeline.
    All methods are sync + O(1) — see §3.7 Execution model."""

    def emit(self, event: TraceEvent) -> None: ...                                 # append to pipeline buffer

    # Convenience helpers — construct TraceEvent + emit
    def op_start(self, op_name: str, ctx: Tuple, inputs: Dict) -> None: ...
    def op_end(self, op_name: str, ctx: Tuple, outputs: Dict, status: str, duration_ms: float) -> None: ...
    def op_yield(self, op_name: str, ctx: Tuple, yielded: Dict, idx: int) -> None: ...
    def annotate(self, key: str, value: Any) -> None: ...                          # reads _current_op_var
    def start_time_of(self, op_name: str, ctx: Tuple) -> float: ...                # for cancel-emit (Rule 3)

    @contextmanager
    def group(self, name: str, **metadata): ...                                    # manual logical group


class NullEmitter(EventEmitter):
    """Bound when no tracer is configured. All methods are no-ops.
    Op code calls emit blindly; cost = one method-table dispatch (~ns)."""
    def emit(self, event): pass
    def op_start(self, *_, **__): pass
    def op_end(self, *_, **__): pass
    def op_yield(self, *_, **__): pass
    def annotate(self, *_, **__): pass
    def start_time_of(self, *_): return 0.0
```

`annotate()` reads `_current_op_var` (set by `_pump` around each op invocation) so user code doesn't have to thread `(op, ctx)` through inputs. Separate ContextVar from SCRATCH's `_current_state_var` — different lifetimes, different bind points.

`engine.start()` binds `NullEmitter()` when `tracer is None`. The fast path is exactly today's "no tracing overhead" path.

### 3.3 `Processor`

```python
Processor = Callable[[Iterable[TraceEvent]], Iterable[TraceEvent]]
```

A pure function. Composes by chaining (`reduce(lambda s, p: p(s), processors, events)`).

**Built-in processor library:**

| Processor | Purpose | Replaces TraceFilter field |
|---|---|---|
| `DropOps([names...])` | filter out events for these ops | `exclude_ops` |
| `KeepOps([names...])` | inverse — keep only these | `include_ops` |
| `DropKinds([kinds...])` | drop synthetic kinds | `exclude_kinds` |
| `DropEmpty()` | drop OP_END where outputs all-None | `skip_empty` |
| `TruncateIO(max_bytes=2000)` | clip large strings/bytes in payload | `max_io_size` |
| `RedactKeys([keys...])` | scrub PII | (new) |
| `Sample(rate=0.1)` | random subset by request_id | (new) |
| `GroupBy(boundary, name_fn)` | emit GROUP_START/END at boundaries | `rewriters` (turn-grouping) |
| `Aggregate(op_name, window, init, reduce, emit)` | streaming reducer over a window — emits one ANNOTATION per window (see Rule 2) | `rewriters` (audio/vad summary) |

**`preserve_children_of` and `protected_types` go away entirely.** They existed because filtering happened on a tree. Now filtering is on a flat stream; deletion never orphans anything because tree-building (if any) happens *inside the exporter*.

### 3.4 `Exporter`

```python
class Exporter(Protocol):
    def export(
        self,
        events: List[TraceEvent],   # already processed
        request_id: str,
        metadata: Dict,             # workflow_name, user_id, session_id, tags
    ) -> None: ...
```

Each exporter renders the events into whatever shape its backend needs. There is no separate `View` abstraction — rendering is just a private method on the exporter.

```python
class LangfuseExporter:
    def export(self, events, request_id, metadata):
        tree = self._build_tree(events)              # this exporter's chosen shape
        batch = self._tree_to_langfuse_events(tree)  # backend mapping
        self._client.ingest(batch)                   # transport

class JsonFileExporter:
    def export(self, events, request_id, metadata):
        with open(f"{self.dir}/{request_id}.json", "w") as f:
            json.dump([asdict(e) for e in events], f)   # flat; no rendering needed

class TimelineDebugExporter:
    def export(self, events, request_id, metadata):
        for e in sorted(events, key=lambda e: e.timestamp):
            print(f"{e.timestamp:%H:%M:%S.%f} {e.kind:12} {e.op_name or '-':20} {e.payload}")
```

The Langfuse-specific `trace`/`span`/`generation` distinction lives **inside `LangfuseExporter`** — not in event shape:

```python
def _is_generation(self, event: TraceEvent) -> bool:
    """Generation = op that emitted at least one LLM_USAGE event."""
    return event.op_name in self._llm_ops_seen
```

### 3.5 `TracePipeline`

```python
class TracePipeline:
    """Bound to one engine.start() call. Owns the event buffer + processors + exporters."""

    def __init__(
        self,
        processors: List[Processor] = (),
        exporters: List[Exporter] = (),
        flush_strategy: FlushStrategy = AtScheduledExit(),
        max_buffered_events: int = 100_000,   # ~200MB worst case; tune per benchmark
        # No media_store: Media bytes ride on event payloads as MediaRef
        # entries. The exporter uploads them at flush. See §3.8.
    ): ...

    def emitter(self) -> EventEmitter:
        """Return a fresh emitter bound to this pipeline (used by engine.start)."""
        ...

    async def flush(self, partial: bool = False) -> None:
        """Apply processors → call each exporter.export()."""
        ...
```

`flush_strategy` controls *when* exporters get called. Default `AtScheduledExit` matches today's behavior. `FlushOnGroupEnd` enables per-turn streaming for callbot. See §7.

### 3.6 Three locked-in rules

Pre-implementation review surfaced three rules the design depends on. These are spec, not "we'll figure it out":

**Rule 1 — Per-yield emission threshold (`@op(emit_yields=N)`)**

Each op declares how often per-yield events are emitted. Default `N=1` (every yield). Set higher to sample, `0` to disable.

```python
@op                          # emit_yields=1 — every yield (default)
def vad_detector(...): ...   # ~5 yields/turn — keep all

@op(emit_yields=100)         # every 100th yield
def audio(...): ...          # 50Hz raw chunks — sample

@op(emit_yields=0)            # never (summary only)
def raw_passthrough(...): ...
```

Implementation: `_pump` increments a per-op counter; calls `emitter.op_yield(...)` only when `idx % N == 0`. The op always emits `OP_START` + `OP_END(yield_count=total_idx)`, regardless of N — so total yields are always known.

**Rule 2 — `Aggregate` is a streaming reducer, not a buffered summarizer**

User supplies an init + reducer. Aggregate folds events into running state, emits one ANNOTATION at GROUP_END:

```python
Aggregate(
    op_name="audio",
    window="group",
    init=lambda: {"count": 0, "total_ms": 0},
    reduce=lambda state, event: {
        "count": state["count"] + 1,
        "total_ms": state["total_ms"] + event.payload.get("duration_ms", 0),
    },
    emit=lambda state: {"count": state["count"], "avg_ms": state["total_ms"] / max(state["count"], 1)},
)
```

Memory cost = size of one `state` dict, regardless of how many events flow through. Operations that need full event list (median, percentiles, top-K) are not supported in v1; add `BufferedAggregate` if a real user asks.

**Rule 3 — `OP_END(status="cancelled")` for brutal-cancelled tasks**

Implemented inside `op.run()`'s `except asyncio.CancelledError` clause + finally block — NOT in the scheduler. Python's `try/finally` guarantees finally runs on `CancelledError`, so the op's own teardown emits OP_END reliably. T1.10 implementation:

```python
# operonx/core/ops/base.py — op.run()
try:
    ...
    emitter.op_start(self.full_name, ctx_for_end, _inputs)
    op_started = True
    ...
except asyncio.CancelledError:
    op_cancelled = True
    raise   # propagate normally to scheduler (Phase B sweep)
except Exception:
    error_msg = ...
finally:
    if op_started:
        if op_cancelled:   status = "cancelled"
        elif error_msg:    status = "error"
        else:              status = "ok"
        emitter.op_end(self.full_name, ctx_for_end, ..., status=status)
```

**Cancel-before-start case** (task killed before body runs): `op_started` stays False, no OP_END emitted — correctly so, since no OP_START was emitted either. No orphan event. Matches Phase B §4.3a brutal-cleanup invariant: nothing to clean up if nothing started.

The earlier draft had this in `task_scheduler._sweep_ctx`. Moving it into `op.run` is simpler (one place to reason about), correct under Python cancellation semantics, and idempotent (the emitter's op_end drops a second call for the same `(op, ctx)`).

### 3.7 Execution model — what runs where

**The single most important spec for performance.** Tracing must never block the call. At 100 CCU this is non-negotiable.

```
┌─────────────────────────────────────────────────────────────┐
│  ASYNCIO MAIN LOOP                                          │
│                                                             │
│  op.run() ──► emitter.op_start(...)  ─┐                     │
│                                       │  sync, O(1) append, │
│  op._exec_core() ──► emit_yield ──────┤  ~2μs per call,     │
│                                       │  no I/O, no proc    │
│  op.run().finally ──► emit.op_end ────┘  ────► buffer.append│
│                                                       │     │
│  flush_strategy.should_flush(event) ── true? ─────► create_task(pipeline._run_processors_and_dispatch())
│                                                       │     │
│                          ┌────────────────────────────┘     │
│                          ▼                                  │
│                   processors run on event loop              │
│                   (pure Python, GIL-bound, no thread benefit)│
│                          │                                  │
│                          ▼                                  │
│                   for exporter in exporters:                │
│                     loop.run_in_executor(pool, exporter.export, ...)
│                                       │                     │
└───────────────────────────────────────┼─────────────────────┘
                                        ▼
                            ┌───────────────────────┐
                            │  THREAD POOL          │
                            │  (HTTP POST, file I/O)│
                            └───────────────────────┘
```

**Hard rules:**

1. **`emit()` is sync and O(1).** Just appends a `TraceEvent` to `pipeline._buffer`. No processor runs, no exporter runs, no allocation beyond the event itself. Target: ≤2μs per call.
2. **`flush_strategy.should_flush(event)` is checked per emit** (cheap: bool predicate). On true, `pipeline._schedule_flush()` calls `asyncio.create_task(self._flush_async())`. **Never inline.** The current op continues without waiting.
3. **Processors run on the asyncio loop** inside `_flush_async`, via plain coroutine. They're pure-Python CPU work — a thread pool gives no benefit (GIL).
4. **Only exporter I/O runs on a thread pool**, via `loop.run_in_executor(pool, exporter.export, events, ...)`. HTTP POST, file write, or any blocking call goes there.
5. **`NullEmitter` short-circuits everything** when no tracer is bound. Op code calls `emit_*` methods unconditionally; the no-op cost is one Python method dispatch.
6. **`@op(emit_yields=N)` is read once per op invocation** in `_pump` and cached as a local var. The yield loop checks `idx % N == 0` — no attribute lookup per iteration.
7. **`Aggregate.reduce` runs inside `_flush_async`**, not on emit. The reducer is fast (constant memory by design — Rule 2), and amortized over the window.

**Per-event cost budget**: ≤2μs at emit time. Test gate: `test_emit_overhead_under_2us` (TR13, added below).

**Emit-site placement in `_pump`** (replaces today's `_store_metrics` calls):
- `op_start`: after `get_inputs()` resolves, before `_exec_core()` enters
- `op_yield`: at each yield from `_exec_core`, gated by `idx % emit_yields == 0`
- `op_end`: in `finally` after `store_result`, with `status="ok" | "error" | "cancelled"`

### 3.8 Media handling — IMPLEMENTED

> **Status: shipped.** Legacy media round-trip restored end-to-end on the
> new event-stream pipeline. No `MediaStore` abstraction was needed — the
> existing `Media` primitive + `extract_media` + `substitute_placeholder`
> helpers (already in [operonx/core/media.py](../operonx/core/media.py))
> mapped cleanly into the event/exporter split.
>
> **Wiring:**
>   * `op.run` calls `BaseOp._extract_trace_io(side, root="inputs"|"outputs")`
>     before each `op_start` / `op_end` (and `op_yield`). The helper runs
>     `normalize_trace_io` (LLMOp's OpenAI chat-block hook still applies)
>     then `extract_media`, replacing each `Media` value with a
>     `<media:N>` placeholder and emitting a parallel `media_refs` list
>     of `MediaRef(field_path, data, mime_type, size_bytes)`.
>   * `EventEmitter.op_start` / `op_end` / `op_yield` now accept
>     `media_refs=` and store it on the event payload alongside the
>     stripped I/O dict. `NullEmitter`'s `*_, **__` signature absorbs
>     the new kwarg with no change.
>   * `LangfuseTreeExporter` (and the `LangfuseGroupedTimelineExporter`
>     subclass) call `_apply_media_refs(body, [start_event, end_event],
>     ...)` after building each observation body. The helper iterates
>     the refs, decodes `data:<mime>;base64,...` URIs, calls
>     `client.upload_media(trace_id, field, content_type, content,
>     observation_id)`, and uses `substitute_placeholder` to swap the
>     returned Langfuse token (`@@@langfuseMedia:type=...|id=...|source=bytes@@@`)
>     into `body['input']` / `body['output']` at each ref's `field_path`.
>     Upload failures fall back to a readable string (`"[media upload
>     failed: <mime>, <size>B]"`) so the trace remains interpretable.
>
> **Verified end-to-end** against `langfuse:edupia` via the `--path=media`
> arm of [scripts/probe_langfuse_edupia_roundtrip.py](../scripts/probe_langfuse_edupia_roundtrip.py)
> — fetches the published trace and asserts `output.audio` is a Langfuse
> media token rather than raw bytes.
>
> **Educa migration:** unblocked. The existing `Media`-emitting ops
> (`prepare_stt_input`, `synthesize_tts`) flow through the new pipeline
> unchanged.
>
> **Out of scope (per-yield rendering).** `OP_YIELD` events strip Media
> from the buffer (so raw bytes don't bloat events) but the default
> Langfuse exporter still folds yields into the parent observation's
> `last_yielded` metadata — Media on intermediate yields is not surfaced
> as separate observations. Matches existing yield-rendering behavior;
> revisit if the educa UI needs per-sub-sentence audio playback.

#### How the eight original questions resolved

When this section was deferred we had eight open questions. Looking back
after implementation, the answers all came from "do what legacy did,
mapped onto the event/exporter split":

1. **How does an op signal media?** Auto-detect — `_extract_trace_io` walks
   inputs/outputs/yielded for `Media` instances, identical to the legacy
   collector pass. No new public API.
2. **Where do bytes live, for how long?** No store. The bytes ride on
   `MediaRef` instances inside event payloads. Same lifetime as the rest
   of the buffered events.
3. **When does the exporter upload?** Lazily at flush, inside the
   exporter's `_apply_media_refs` step — same execution model as the rest
   of the pipeline (HTTP via `run_in_executor`, never on the emit path).
4. **What on upload failure?** Fall back to a readable string at the
   placeholder position so the trace stays interpretable. No retries.
5. **`FlushOnGroupEnd` (per-turn streaming)?** Each flush uploads media
   carried by events in that flush. Group boundaries do not affect upload
   semantics — the events that hit the exporter carry their refs, full
   stop.
6. **Audio chunks at 50Hz?** Non-issue for educa. The 50Hz `recv_audio`
   stream was never `Media`-wrapped; only per-turn assembled WAVs are
   (~5–20 uploads per call, not 15,000). If a future op generates Media
   at 50Hz we add an aggregating processor, but YAGNI today.
7. **What does the observation reference?** Same as legacy:
   `<media:N>` placeholder in the I/O dict at producer side, swapped for
   `@@@langfuseMedia:type=...|id=...|source=bytes@@@` after upload. UI
   renders a native preview.
8. **Rust parity?** `MediaRef` is plain data (`field_path`, `data`,
   `mime_type`, `size_bytes`); JSON-serializable; mirrors directly when
   R2 is wired up.

---

## §4. Default tree tracing — full example

```python
from operonx import Operon
from operonx.tracing import TracePipeline
from operonx.tracing.exporters import LangfuseExporter

pipeline = TracePipeline(
    exporters=[LangfuseExporter(resource="langfuse:default")],
)

engine = Operon(graph, tracer=pipeline)
result = await engine.run(inputs={...})
```

What happens:
- No processors → every event survives.
- LangfuseExporter's internal `_build_tree` reconstructs parent/child from `ctx` prefix matching: ctx `("main", "stt", "[0]")` is a child of `("main", "stt")`.
- Posts trace-create + span-create + generation-create events.

Equivalent to today's default — works the same way for hierarchical workflows with no extra config.

---

## §5. Educa flat-grouped timeline — full example

The exact use case the user described: "flat events with timestamps, but preserve logical grouping (events belong to a turn)."

```python
# educa/server/ws_server.py
from operonx.tracing import TracePipeline, FlushOnGroupEnd
from operonx.tracing.processors import (
    DropOps, TruncateIO, GroupBy, Aggregate,
)
from operonx.tracing.exporters import LangfuseExporter

def _turn_boundary(event):
    return event.kind == EventKind.OP_END and event.op_name in ("asr_result", "spoken")

pipeline = TracePipeline(
    processors=[
        DropOps(["picker", "stt_route", "skip_stt", "workflow_route",
                 "turn_route", "skip_turn", "m_intent", "m_response"]),
        TruncateIO(max_bytes=2000),
        GroupBy(boundary=_turn_boundary, name_fn=lambda i: f"turn-{i}"),
        Aggregate(
            op_name="audio", window="group",
            init=lambda: {"count": 0, "total_ms": 0, "max_ms": 0, "overflow": 0},
            reduce=_audio_reduce,
            emit=lambda s: {
                "chunk_count": s["count"],
                "avg_duration_ms": s["total_ms"] / max(s["count"], 1),
                "max_duration_ms": s["max_ms"],
                "overflow_count": s["overflow"],
            },
        ),
        Aggregate(op_name="vad", window="group", init=_vad_init, reduce=_vad_reduce, emit=_vad_emit),
    ],
    exporters=[LangfuseGroupedTimelineExporter(resource="langfuse:edupia")],
    flush_strategy=FlushOnGroupEnd(group="turn-*"),
)

engine = Operon(callbot_graph, tracer=pipeline)
```

`turn_rewriter` (200 lines) → ~10 lines of processor config + 3 small reducer functions:

```python
def _audio_reduce(state, event):
    if event.kind != EventKind.OP_YIELD:
        return state
    dur = event.payload.get("duration_ms", 0)
    return {
        "count":    state["count"] + 1,
        "total_ms": state["total_ms"] + dur,
        "max_ms":   max(state["max_ms"], dur),
        "overflow": state["overflow"] + (1 if dur > 30 else 0),
    }
```

`Aggregate` folds events as they arrive (constant memory, see Rule 2) and emits one `ANNOTATION` per turn at `GROUP_END`. The grouped-timeline exporter renders that annotation as a child of the turn span.

---

## §6. TraceFilter → processors — backward-compat shim

```python
def trace_filter_to_processors(tf: TraceFilter) -> List[Processor]:
    out = []
    if tf.exclude_ops:    out.append(DropOps(tf.exclude_ops))
    if tf.include_ops:    out.append(KeepOps(tf.include_ops))
    if tf.exclude_kinds:  out.append(DropKinds(tf.exclude_kinds))
    if tf.skip_empty:     out.append(DropEmpty())
    if tf.max_io_size:    out.append(TruncateIO(tf.max_io_size))
    for r in (tf.rewriters or []):
        out.append(LegacyRewriter(r))   # adapts old List[Dict]→List[Dict] signature
    return out
```

Concept-by-concept mapping:

| Old `TraceFilter` field | New | Notes |
|---|---|---|
| `skip_empty: True` | `DropEmpty()` | identical |
| `exclude_ops: [...]` | `DropOps([...])` | identical |
| `exclude_kinds: [...]` | `DropKinds([...])` | identical |
| `max_io_size: 2000` | `TruncateIO(max_bytes=2000)` | identical |
| `preserve_children_of: [...]` | **deleted** | no longer needed — flat stream never orphans |
| `protected_types: ["trace","generation"]` | **deleted** | exporter decides what's a generation |
| `rewriters: [fn]` | custom Processor | new API is cleaner; legacy shim provided |

---

## §7. Incremental flushing — the long-call problem

Today: a 5-min callbot buffers every span in memory and emits one batch at hangup. If the call disconnects badly, we may lose visibility entirely.

New: `flush_strategy` decouples *when* exporters run from *when* the scheduler exits.

```python
class FlushStrategy(Protocol):
    def should_flush(self, event: TraceEvent, buffered: int) -> bool: ...

class AtScheduledExit:                  # default — current behavior
    def should_flush(self, event, buffered): return False

class FlushOnGroupEnd:                  # callbot — after every turn
    def __init__(self, group: str): ...
    def should_flush(self, event, buffered):
        return event.kind == EventKind.GROUP_END and matches(event, self.group)

class FlushOnInterval:                  # heartbeat
    def __init__(self, seconds: float): ...

class FlushOnSize:                      # bounded memory
    def __init__(self, max_events: int): ...
```

For educa: each turn's events ship to Langfuse the moment the turn closes. Hangup just flushes the trailing partial group. Sub-second visibility.

**Idempotent partial flushes:** each exporter call carries the same `request_id`; the trace-id stays stable; Langfuse merges add-on observations into the existing trace. (Verify with self-hosted version before enabling.)

---

## §8. Final folder structure

```
operonx/
  core/
    tracing/
      __init__.py              # public API: TracePipeline, EventEmitter, processors, exporters
      events.py                # TraceEvent, EventKind
      emitter.py               # EventEmitter + ContextVar binding
      pipeline.py              # TracePipeline orchestrator
      flush.py                 # FlushStrategy + background flush worker
      processors/
        __init__.py            # re-exports
        drop.py                # DropOps, KeepOps, DropKinds, DropEmpty
        truncate.py            # TruncateIO
        redact.py              # RedactKeys
        sample.py              # Sample
        group.py               # GroupBy, Aggregate
      exporters/
        __init__.py
        base.py                # Exporter Protocol
        local_file.py          # JsonFileExporter (zero-dep)
        timeline_debug.py      # TimelineDebugExporter (stdout — for tests/dev)
      legacy.py                # TraceFilter shim, deprecated old Tracer protocol

  telemetry/
    exporters/
      __init__.py
      langfuse.py              # LangfuseExporter — moved from telemetry/tracers/
      otel.py                  # OtelExporter — moved from telemetry/tracers/
      operon_eyes.py           # OperonEyesExporter — moved from telemetry/tracers/
    backends/
      langfuse/
        client.py              # HTTP client, unchanged
        config.py              # LangfuseConfig
      # otel/...
      # operon_eyes/...

  core/
    ops/
      base.py                  # MODIFIED — calls EventEmitter instead of writing state cells for tracing
    ops/graph/
      task_scheduler.py        # MODIFIED — emits OP_START/END/YIELD around _pump

  __init__.py                  # re-exports stay backward-compatible
```

**What's removed:**

```
operonx/core/tracing/
  base.py                      # Tracer abstract class — replaced by Exporter Protocol
  collector.py                 # TraceCollector — gone, exporters render directly
  flush_worker.py              # absorbed into core/tracing/flush.py
  local.py                     # moved/renamed to exporters/local_file.py
  models.py                    # TraceNode dataclass — gone, exporters render their own shapes
  trace_filter.py              # TraceFilter — moved to legacy.py with deprecation warning

operonx/telemetry/tracers/     # entire folder gone — old Tracer subclasses replaced by Exporters
  _base.py
  langfuse.py                  # → telemetry/exporters/langfuse.py (kept in telemetry/ for optional deps)
  otel.py                      # → telemetry/exporters/otel.py
  operon_eyes.py               # → telemetry/exporters/operon_eyes.py
```

**Naming convention:**
- `core/tracing/` — pipeline machinery + zero-dep exporters
- `telemetry/exporters/` — exporters with optional 3rd-party deps (Langfuse SDK, OTel SDK)
- `telemetry/backends/` — HTTP/grpc clients consumed by exporters

This keeps `pip install operonx` lean — Langfuse and OTel deps stay in `operonx[telemetry]` extras.

**Educa side** (no folder structure change — they import from operonx):

```
educa-reminder-agent/
  server/
    ws_server.py               # MODIFIED — TracePipeline replaces LangfuseTracer + TraceFilter
  src/callbot/
    tracing.py                 # 400 lines → ~50 lines (only _audio_summary + _vad_summary remain)
```

**Rust side** (mirrors Python — same four abstractions):

```
rust/operonx/src/
  core/
    tracing/
      mod.rs                   # public API
      events.rs                # TraceEvent, EventKind
      emitter.rs               # EventEmitter + tokio task_local binding
      pipeline.rs              # TracePipeline orchestrator
      flush.rs                 # FlushStrategy
      processors/
        mod.rs
        drop.rs                # DropOps, KeepOps, DropKinds, DropEmpty
        truncate.rs            # TruncateIO
        redact.rs              # RedactKeys
        sample.rs              # Sample
        group.rs               # GroupBy, Aggregate
      exporters/
        mod.rs
        base.rs                # Exporter trait
        local_file.rs          # JsonFileExporter
        timeline_debug.rs      # TimelineDebugExporter (stderr)
    ops/
      base.rs                  # MODIFIED — emits events
    ops/graph/
      task_scheduler.rs        # MODIFIED — emits OP_START/END/YIELD; OP_END(cancelled) in sweep_ctx

  telemetry/
    exporters/
      langfuse.rs              # LangfuseExporter (uses reqwest + langfuse client)
      otel.rs                  # OtelExporter
    backends/
      langfuse/
        client.rs              # HTTP client (already exists)
        config.rs
```

Same naming convention as Python: zero-dep exporters in `core/tracing/exporters/`, optional-dep ones in `telemetry/exporters/` (gated by Cargo features `langfuse`, `otel`).

---

## §9. Operonx migration — 4 phases, additive, backward-compatible

### Phase T1 — introduce event bus alongside existing collector

**No behavior change.** New types added; old collector still drives flush.

- New files in their final paths under `core/tracing/`: `events.py`, `emitter.py`, `pipeline.py`
- Scheduler emits new events to a per-call pipeline buffer *in parallel with* existing state-cell writes
- Old `TraceCollector` keeps working
- New `TracePipeline` exists but unused by default; gated by feature flag `OPERONX_NEW_TRACING=1` for opt-in testing

Tests: assert event stream contains structurally equivalent data to what the old collector produces, for a sample workflow.

### Phase T2 — re-implement Local + Langfuse on the new pipeline

**Switch exporters to consume events, not the old collector.** Old tracer API preserved (`LangfuseTracer(...)` still importable, internally constructs a `TracePipeline`).

- `LocalTracer` → `JsonFileExporter` in `core/tracing/exporters/local_file.py`
- `LangfuseTracer` → `LangfuseExporter` (with `LangfuseTreeExporter` / `LangfuseGroupedTimelineExporter` subclasses) in `telemetry/exporters/langfuse.py`
- `TraceCollector`, `TraceNode`, `flush_worker.py` deleted; logic absorbed into pipeline + exporters
- All existing tests pass with structurally equivalent output (event_id is a new UUID per call, so byte-identical equivalence is not the gate; semantic equivalence is)

### Phase T3 — `TraceFilter` deprecated, processor adapter ships

- `TraceFilter(...)` emits `DeprecationWarning` and internally converts to processor list via §6 adapter
- New consumers use `TracePipeline(processors=[...])` directly
- Feature flag removed; new pipeline is the default path

### Phase T4 — delete `TraceFilter` and old Tracer Protocol

After one minor release with deprecation warnings. Final folder structure (§8) realized.

---

## §10. Educa migration — 3 phases, hooked to operonx phases

### Educa 1 (concurrent with operonx T2)

**Drop-in replacement.** Educa's existing `LangfuseTracer(resource=..., trace_filter=...)` keeps working unchanged — operonx T2 ships the same surface internally backed by the new pipeline.

Effort: zero. Risk: zero.

### Educa 2 (after operonx T3)

**Switch to explicit pipeline.** Replace `TraceFilter(...)` config with processor list. Delete `turn_rewriter` (replaced by `GroupBy` + `Aggregate`).

```python
# Before — server/ws_server.py
_LANGFUSE_TRACER = LangfuseTracer(
    resource="langfuse:edupia",
    trace_filter=TraceFilter(
        skip_empty=True, max_io_size=2000,
        exclude_kinds=[...], exclude_ops=[...],
        preserve_children_of=[...], rewriters=[turn_rewriter],
    ),
)

# After
_PIPELINE = TracePipeline(
    processors=[
        DropEmpty(),
        TruncateIO(2000),
        DropOps([...]),
        GroupBy(boundary=_turn_boundary, name_fn=lambda i: f"turn-{i}"),
        Aggregate(op_name="audio", window="group", init=..., reduce=_audio_reduce, emit=...),
        Aggregate(op_name="vad",   window="group", init=..., reduce=_vad_reduce,   emit=...),
    ],
    exporters=[LangfuseGroupedTimelineExporter(resource="langfuse:edupia")],
)
```

Net delta: `src/callbot/tracing.py` (turn_rewriter, ~400 lines) → ~50 lines of pure summary functions. Trace shape unchanged in Langfuse UI.

### Educa 3 (optional, after operonx T4)

**Enable per-turn streaming.** Add `flush_strategy=FlushOnGroupEnd(group="turn-*")`. Each turn ships to Langfuse on close. Mid-call visibility.

Risk: self-hosted Langfuse must support add-on observations to an existing trace. Verify with probe script before enabling in prod.

---

## §11. Rust migration — mirrors Python phases

Rust port lags Python by one phase. Same four abstractions, same three locked-in rules, same folder layout (§8).

### Phase R1 — event types + emitter (concurrent with Python T1)

- `core/tracing/events.rs` — `TraceEvent`, `EventKind` (serde-derived for JSON parity)
- `core/tracing/emitter.rs` — `EventEmitter` + `tokio::task_local!` for ContextVar parity
- `core/ops/base.rs` — emit hooks (parallel with existing trace_log writes)
- No exporter changes yet; old code still flushes

### Phase R2 — pipeline + zero-dep exporters (concurrent with Python T2)

- `core/tracing/pipeline.rs` — `TracePipeline`, `FlushStrategy`
- `core/tracing/processors/*` — `DropOps`, `KeepOps`, `DropKinds`, `DropEmpty`, `TruncateIO`, `Sample`, `RedactKeys`, `GroupBy`, `Aggregate` (streaming)
- `core/tracing/exporters/local_file.rs` — `JsonFileExporter`
- `core/tracing/exporters/timeline_debug.rs` — `TimelineDebugExporter`
- Scheduler emits `OP_END(status="cancelled")` from sweep_ctx (Rule 3 — Rust mirror)
- `@op(emit_yields=N)` → Rust attribute `#[op(emit_yields = N)]` on op definitions (Rule 1)
- `Aggregate` streaming reducer signature: `Fn(State, &TraceEvent) -> State` (Rule 2)

### Phase R3 — Langfuse exporter (concurrent with Python T3)

- `telemetry/exporters/langfuse.rs` — `LangfuseExporter` behind `langfuse` Cargo feature
- Reuses existing `telemetry/backends/langfuse/client.rs`
- Tree + grouped-timeline render modes mirror Python
- Cargo: `operonx = { features = ["langfuse"] }` opt-in

### Phase R4 — finalize (concurrent with Python T4)

- Delete old tracer trait + `TraceFilter` Rust stub
- Public API freeze
- Cross-runtime parity test fixtures (see §12)

---

## §12. Tests — Python and Rust at parity

**Cross-cutting parity invariant**: a graph executed in Python and Rust with the same inputs produces equivalent event streams. Where exact equivalence is impossible (timestamps, UUIDs), parity is structural (same kinds in the same order, same op_names, same payload keys).

### Python tests (`tests/internal/tracing/`)

| # | Test | Asserts |
|---|---|---|
| TR1 | `test_emit_yields_threshold` | `@op(emit_yields=1)`: every yield emits OP_YIELD; `=100`: every 100th; `=0`: never. OP_START/OP_END always emit. yield_count on OP_END is total regardless of N |
| TR2 | `test_aggregate_streaming_constant_memory` | run a 10000-event stream through `Aggregate(reduce=...)`; assert peak buffer size is bounded (≤2 events at a time) |
| TR3 | `test_op_end_cancelled_emitted` | Phase B Interrupt sweep cancels long_sleep op mid-flight; assert OP_END(status="cancelled", duration_ms>0) is emitted; no double-emit when finally also runs |
| TR4 | `test_op_end_cancelled_before_start` | Cancel op before its body runs (cancel-before-start case from Phase B §4.3a); assert OP_END(status="cancelled", duration_ms≈0) emitted exactly once |
| TR5 | `test_processor_chain_compose` | `[DropOps, TruncateIO, GroupBy, Aggregate]` composes in declared order; reverse order detected and warning logged (no enforcement in v1) |
| TR6 | `test_default_tree_exporter_equivalence` | Run a sample workflow; assert new TreeExporter output == old TraceCollector output (modulo event_id/timestamp jitter) |
| TR7 | `test_langfuse_exporter_grouped_timeline` | Mock Langfuse HTTP; run educa-shape workflow; assert one `trace-create` + one `span-create` per turn + correct `parentObservationId` linking |
| TR8 | `test_flush_on_group_end` | `FlushOnGroupEnd(group="turn-*")`: assert exporter.export called at each GROUP_END, partial flag set; final flush at scheduler exit clears trailing buffer |
| TR9 | `test_langfuse_partial_flush_idempotent` | (integration, gated by env var) post trace + later post add-on observations; verify Langfuse merges them in API readback |
| TR10 | `test_truncate_io_processor` | events with `payload["text"] > 2000 bytes` get truncated; binary payload also clipped |
| TR11 | `test_legacy_trace_filter_shim` | `TraceFilter(exclude_ops=[...], rewriters=[fn])` produces equivalent output to processor chain via shim; emits DeprecationWarning |
| TR12 | `test_null_emitter_zero_overhead` | `engine.start()` without tracer: assert NullEmitter used; `op_start/op_end` are no-ops with no allocation (verify via `tracemalloc` snapshot) |
| TR13 | `test_emit_overhead_under_2us` | Hot-path budget: 10000 emit calls, assert avg ≤2μs each. Gate for §3.7 Rule 1 |
| TR14 | `test_flush_does_not_block_main_loop` | Mock exporter that sleeps 200ms; assert main asyncio loop continues running ops during flush (executor offload works) |

### Rust tests (`rust/operonx/tests/internal/tracing/`)

Same matrix as Python, ported. Key Rust-specific tests:

| # | Test | Asserts |
|---|---|---|
| TR-R1 | `emit_yields_threshold_attribute` | `#[op(emit_yields = 100)]` annotation: only every 100th OP_YIELD reaches sink |
| TR-R2 | `aggregate_streaming_no_clone` | `Aggregate` with closure-state reducer: profile heap; no Vec<TraceEvent> growth |
| TR-R3 | `cancelled_op_end_via_join_handle_abort` | Rust analogue of TR3 — `JoinHandle::abort()` triggers scheduler emit of OP_END(cancelled) |
| TR-R4 | `task_local_emitter_propagates` | `tokio::task_local!` carries emitter across `tokio::spawn`; `annotate()` works in child tasks |
| TR-R5 | `cross_runtime_event_parity` | Run identical workflow in Python and Rust; serialize event streams to JSON; assert structural equivalence (same event kinds, same op_names, same payload keys per kind) |

### Integration tests against Edupia Langfuse

Config copied to operonx (`resources.yaml` + `.env` — `langfuse:edupia`). Tests gated by env var `OPERONX_INTEGRATION=1` (skipped in CI by default; run pre-merge against the real backend).

| # | Test | Asserts |
|---|---|---|
| TR-INT1 | `test_langfuse_edupia_health` | `GET https://langfuse.edupia.com.vn:1102/api/public/health` returns 200 with creds |
| TR-INT2 | `test_langfuse_edupia_round_trip` | Run small graph; assert trace appears in Langfuse via API readback within 5s |
| TR-INT3 | `test_langfuse_edupia_partial_flush_merge` | Probe for HIGH-8 from review: post trace, later post add-on observations, verify merge. **Gates Educa Phase 3.** |

Probe script: `/home/thanglq/Operon/scripts/probe_langfuse_partial_flush.py`. Built in T2; run as a one-shot manual check before educa enables `FlushOnGroupEnd` in prod.

### Pre-merge gates per phase

- **Python T1**: TR1, TR3, TR4, TR12 + parity sample (event stream from new emitter == data from old collector, verified at the JSON level)
- **Python T2**: + TR2, TR5, TR6, TR7, TR8, TR10, TR-INT1, TR-INT2
- **Python T3**: + TR11 (legacy shim), full educa staging smoke test
- **Python T4**: full suite green; legacy `TraceFilter` deleted
- **Rust R1–R4**: same matrix, plus TR-R5 cross-runtime parity at R3
- **Educa Phase 3**: TR-INT3 must pass on Edupia's actual Langfuse version

---

## §13. Open questions

All previously-open questions resolved in §3 (locked-in rules + execution model + MediaStore). What remains is a single benchmark target — to be done at the start of T2 against the educa workload before public defaults freeze:

1. **Right value for `max_buffered_events`.** Placeholder is `100_000` (≈200MB worst case). Benchmark a 5-min callbot at 100 CCU; pick the value that keeps total RSS under 1GB during burst.

---

## §14. What this is NOT

- Not OpenTelemetry compatibility. OTel is one possible exporter; the core doesn't model OTel's span context.
- Not distributed tracing. Single-process, single-call.
- Not a metrics system. Histograms/counters are out of scope.
- Not a structured logging replacement.
- Not a Langfuse SDK replacement — `LangfuseClient` (HTTP layer) keeps doing what it does.

---

## §15. Status

**Shipped (Python):**
- T1: events, emitter (with `NullEmitter`), pipeline, `_current_emitter_var` ContextVar binding, cancellation contract (`op.run` catches `CancelledError`, emits `OP_END(status="cancelled")`)
- T2.1: processors (`DropOps`, `KeepOps`, `DropKinds`, `DropEmpty`, `TruncateIO`, `RedactKeys`, `Sample`, `GroupBy`, `Aggregate` streaming reducer), `JsonFileExporter`
- T2.2: `LangfuseTreeExporter` + `LangfuseGroupedTimelineExporter` (replaces legacy `LangfuseTracer.flush(trace_data)` logic)
- T2.11: `trace_filter_to_processors` adapter, `LangfuseTracer.as_pipeline(...)` migration helper
- T2.12: `LangfuseTracer` rewritten as a `TracePipeline` subclass (educa upgrade path = zero code changes; constructor still works)
- T2.13: OTel + OperonEyes tracers deleted (no callers); legacy core (`TraceCollector`, `TraceNode`, `flush_worker`, abstract `Tracer`, `LocalTracer`) deleted; `engine.py` simplified
- T3: `DeprecationWarning` on `LangfuseTracer(...)` constructor and on `TraceFilter` — both still functional, both point at their migration target (`TracePipeline` + `LangfuseTreeExporter`, processor list + `GroupBy`/`Aggregate`)
- §3.8 media handling: producer wired in `op.run` (`_extract_trace_io`); refs ride on `OP_START` / `OP_END` / `OP_YIELD` payload; exporter uploads via `client.upload_media` and substitutes Langfuse tokens at each ref's `field_path`. End-to-end probe green against `langfuse:edupia` (`--path=media`). Educa migration unblocked.

**Open / deferred:**
- `max_buffered_events` default — placeholder `100_000`; tune against educa's CCU benchmark before public release.
- T4 — final delete of `LangfuseTracer(...)` constructor + `TraceFilter` after one minor release with the T3 deprecation warnings. Calendar-gated, not actionable now.

**Decisions locked:**
- Four-abstraction contract (Event, Emitter, Pipeline, Exporter)
- Per-pipeline `flush_strategy` (not per-exporter for v1)
- Folder split: zero-dep core vs optional-dep telemetry exporters
- Three rules from §3.6: per-yield threshold, streaming `Aggregate`, cancel emits `OP_END`
- Legacy `TraceFilter` deleted after T4 (currently kept for adapter back-compat)
- Media handling: no `MediaStore`; refs ride on event payload, exporter uploads at flush (§3.8)

**Edupia integration:**
- `langfuse:edupia` config in [resources.yaml](../resources.yaml) + [.env](../.env)
- Probe round-trips green for all four paths (legacy / new / shim / media) — see `scripts/probe_langfuse_edupia_roundtrip.py`
- Educa migration to the new pipeline: ready (`Media`-emitting ops round-trip end-to-end)

**Companion review:** [TRACING_REDESIGN_REVIEW.md](TRACING_REDESIGN_REVIEW.md)
