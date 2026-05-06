# Tracing Redesign — Review

**Companion to**: [TRACING_REDESIGN_PLAN.md](TRACING_REDESIGN_PLAN.md)
**Stance**: critical. This is a pre-implementation review; my job is to find what breaks.
**Author**: claude (2026-05-05)

---

## §1. Hidden complexity in the current code (things you may not have flagged)

These are real findings from reading the source, not generic concerns.

### 1.1 Per-yield timing is silently broken — confirmed in source

[`base.py:727-736`](../operonx/core/ops/base.py#L727):

```python
if self.is_gen and _tracing:
    _yield_end = perf_counter()
    _now = datetime.now(timezone.utc)
    self._store_metrics(
        state, ctx,
        start_time=_now,             # ← same value
        end_time=_now,               # ← same value
        duration_ms=(_yield_end - _yield_start) * 1000,
    )
```

`start_time == end_time` for every per-yield metric. Then [`base.py:775`](../operonx/core/ops/base.py#L775)'s `finally` overwrites the *root* ctx with cumulative `duration_ms` for the generator. Result: per-yield TraceNodes show as zero-duration spans, the parent generator span shows as 1.45s. The old draft's claim ("denoise shows 1.45s but actual work per yield is <1ms") is correct and reproduces from this code. Today's UI shows misleading timings; nobody has noticed because the UI's flame graph still looks plausible.

**Implication for new design**: per-yield timing has to be designed correctly from day one. The new event model gives every `OP_YIELD` its own timestamp, so this is a free win — but it must be tested against generator workloads explicitly.

### 1.2 `TraceFilter` rewriter errors are silently swallowed

[`trace_filter.py:137-140`](../operonx/core/tracing/trace_filter.py#L137):

```python
for rewriter in self.rewriters:
    try:
        result = rewriter(result)
    except Exception as exc:
        LOGGER.warning("TraceFilter rewriter %s failed: %s", rewriter, exc)
```

A buggy `turn_rewriter` produces malformed traces and only logs a `WARNING`. Educa has been deploying changes to this 200-line function with no test gate. If `turn_rewriter` raised today on a real call, you'd see flat ungrouped events in Langfuse and have no idea why.

**Implication**: the new processor chain MUST fail loudly by default. Add a `safe_mode` flag for legacy compatibility, but default to "raise."

### 1.3 Generator output aggregation is reading from possibly-empty cells

[`collector.py:346-354`](../operonx/core/tracing/collector.py#L346): if a generator op's first output cell is empty (op crashed before first yield, or all yields had a key=None), the generator span gets `outputs={}` with NO indication that yields happened. Tracing visibility hole — looks like the op produced nothing rather than "produced N items but couldn't aggregate."

### 1.4 `state.tracing` is a binary, no sampling possible

[`engine.py:429`](../operonx/core/engine.py#L429): `state.tracing = bool(tracers)`. There is no per-call probability sampling. Educa at 100 CCU traces every call → 30MB+ in memory per call accumulates, plus full Langfuse ingestion. Sampling has to be added as a wrapper today (skip tracer construction in some calls), which loses partial visibility.

### 1.5 Tracer flush fires on cancellation against possibly-inconsistent state

[`engine.py:450-459`](../operonx/core/engine.py#L450):

```python
async def _run() -> None:
    try:
        await self.graph._scheduler.run(...)
    except asyncio.CancelledError:
        queue.put_nowait(None)
        raise
    finally:
        if tracers:
            get_flush_worker().submit(tracers, collector, state)
```

If the engine task is cancelled mid-mutation (e.g., op A wrote to state, op B's write was interrupted), the collector walks the half-state. No protection against this. The old code's saving grace is that state cells are dict writes (atomic at the entry level), but order-of-operations bugs surface only under abnormal cancellation.

### 1.6 `state.tracing` bool gates more than just metrics

It also gates `start_time`/`end_time` recording (lines 700, 772). Not "skip metrics" — actually "skip timestamps." Means we can't get span timing without a full tracer, which is a coupling bug. The new design separates emission from rendering; it should NOT inherit this coupling.

### 1.7 The `node_type` field is doing two jobs

In [`models.py`](../operonx/core/tracing/models.py): `node_type ∈ {trace, span, generation}` is used for (a) Langfuse event routing and (b) protected-from-filtering exemption (`protected_types`). These are unrelated. The new design correctly drops `node_type` from event shape, but must still handle the exemption case — answer is "exporter handles its own protected ops"; default behavior of `LangfuseExporter` should be: never drop the root trace event or LLM_USAGE-bearing ops.

---

## §2. What the new design genuinely fixes

Concrete wins — I'm being specific so the next sections can be honest about what's still wrong.

| Pain | Fix | Worth the rewrite? |
|---|---|---|
| `TraceFilter` mixes 7 concerns | Each becomes one Processor | yes — the only field with no clean replacement was `protected_types`, and that was a bug pretending to be a feature |
| Tree shape hardcoded in collector | Exporter renders its own shape | yes — educa's 200-line `turn_rewriter` collapses to ~30 lines |
| Single batch flush at call end | `flush_strategy` per pipeline | yes — long calls finally get mid-call visibility |
| Per-yield timing broken | Each `OP_YIELD` carries its own timestamp | yes (essentially free) |
| Cancelled tasks leave open spans | Scheduler emits `OP_END(status=cancelled)` | yes — but this is a scheduler change, not just a tracing change |
| Sampling impossible | `Sample(rate=0.1)` processor | yes |
| Failure debugging hard | Fail-loud processors + bounded buffer overflow log | yes |

---

## §3. Critical issues with the proposed design

Numbered by severity. SEVERE = ship-blocker. HIGH = will bite us within a quarter. MEDIUM = bites us when scaling.

### 🔴 SEVERE-1. `OP_YIELD` flood blows up memory in long calls

A callbot's `audio` op yields every 20ms. A 5-min call = 15,000 `OP_YIELD` events for that op alone. Multiple generator ops (audio, vad, denoise) → ~50,000 events per call. Each `TraceEvent` after `TruncateIO(2000)` is ~2KB → **100MB buffered per call**. At 100 CCU = 10GB. The system OOMs before the call ends.

Today this doesn't happen because `_store_metrics` writes to bounded state cells (each ctx has one slot, generators overwrite as they go); the explosion is hidden by lossy aggregation.

**Fix**:
- Per-op default: `emit_yields=False`. Generator ops emit `OP_START` + `OP_END(yield_count=N, sample=[first 5 yields])` only.
- Opt-in to full per-yield emission via `@op(emit_yields=True)` or processor `EnableYields([op_names])`.
- `Sample(rate)` processor MUST be applied before buffering, not after — drop early to save memory.
- Bounded buffer: `max_buffered_events` (default 50k); on overflow, drop oldest with one WARNING log.

This is a default-behavior question, not a knob. Get it wrong and the new design is **worse than the old one** for long-call workloads.

### 🔴 SEVERE-2. `Aggregate` defeats streaming flush

The plan says `Aggregate(window="group", op_name="audio", summarize=fn)` collapses audio events into a summary. To produce that summary, Aggregate must hold every audio event for the window in memory until `GROUP_END` fires. With `FlushOnGroupEnd`, the flush happens at group_end:

```
[turn_start] [audio]×500 [vad]×10 [tts] [turn_end] ←─ flush fires here
                                                     Aggregate runs NOW
                                                     emits 1 summary event
                                                     pipeline drains buffer to exporter
```

For a 30-second turn at 50Hz audio: 1500 events × 2KB = 3MB per turn. OK. But for a 30s greeting + 4-min user monologue (no turn boundaries): boundless buffer growth.

**Fix**: Aggregate must support **streaming reduction** — it folds events into an accumulator as they arrive (size-bounded), and emits only the accumulator at group_end. Like a fold/reduce, not a buffer-and-summarize. The user supplies `(state, event) -> state` instead of `events -> summary`.

This is a 5-line spec change but a fundamental shift in mental model.

### 🔴 SEVERE-3. Cancelled-task `OP_END` is the scheduler's problem, not tracing's

Phase B's brutal cancel calls `task.cancel()` — if the task hasn't started, its body never runs, no `try/finally` fires. The new design says "scheduler emits synthetic OP_END(cancelled)." The plan §11 calls this an "open question." It's not — it's mandatory. Without it:

- LangfuseExporter sees `OP_START` with no matching `OP_END` → renders open span → ugly trace
- TimelineView shows event without `end_time` → downstream consumers crash on `None`
- `Aggregate(window="group")` waits forever for a `group_end` event that never comes

**Fix**: in [task_scheduler.py](../operonx/core/ops/graph/task_scheduler.py)'s `_sweep_ctx` brutal-cleanup loop, after `await asyncio.gather(*cancelled, return_exceptions=True)`:

```python
for op_name, ctx, _task in cancelled:
    if op_name still in tasks_by_ctx[ctx]:
        emitter.op_end(op_name, ctx, outputs={}, status="cancelled", duration_ms=0)
        # ... existing inflight bookkeeping ...
```

Lock this into the spec, not "we'll figure it out."

### 🟠 HIGH-4. `GroupBy` boundary may never fire — open groups leak

If `GroupBy(boundary=fn)` opens a group on event A and closes on event B, but the call disconnects before B, the group never closes. Events accumulate. With `FlushOnGroupEnd`, no flush fires for the trailing group either. Memory leak + lost trailing events.

**Fix**: pipeline shutdown auto-emits `GROUP_END` for any open groups. Add to spec:

> When `TracePipeline.shutdown()` runs (called from engine `finally`), for each open group still tracked by any processor, synthesize a `GROUP_END` event with `status=truncated` so processors close cleanly and flushes fire.

### 🟠 HIGH-5. `LLM_USAGE` event ordering is fragile

`LangfuseExporter` decides "this op is a generation" by checking if its op_name appears in the set of "ops that emitted at least one LLM_USAGE." If `LLM_USAGE` arrives **after** `OP_END` for the same op (e.g., the LLM client tallies tokens post-completion in a callback), the exporter has already classified the op as a span. Now it has to retroactively reclassify, which the Langfuse API doesn't support cleanly (you'd have to delete and re-create the observation).

**Fix**: enforce in code that `LLM_USAGE` MUST be emitted BEFORE `OP_END`. The `ask` op's run loop calls `emit.llm_usage(...)` synchronously before `emit.op_end(...)`. Add an assertion at OP_END that warns if a later LLM_USAGE arrives for an already-ended op.

Alternative: roll usage into the OP_END payload (one of the §11 open questions). Simpler, eliminates ordering, but loses streaming-LLM mid-call usage updates. **Recommend**: keep separate, enforce ordering.

### 🟠 HIGH-6. Processor ordering is implicit and silently breaks

`[GroupBy(...), Aggregate(window="group")]` only works because GroupBy runs first (emits markers Aggregate needs). Reverse the order and Aggregate sees no group_start events, never closes a window, never emits a summary. Failure mode: silently empty traces.

**Fix**:
- Each Processor declares `requires: List[Type]` (e.g., `Aggregate.requires = [GroupBy]`).
- `TracePipeline.__init__` topologically validates and raises if requirements not met before this processor in chain.
- Add unit tests for common mis-orderings.

### 🟠 HIGH-7. `MEDIA_REF` payload — bytes have to live somewhere

The plan says "handle only; exporter decides upload strategy." Missing: where do the bytes live? If the buffer doesn't carry them, the exporter can't upload them at flush time without a side channel.

**Fix**: introduce explicit `MediaStore` in core:

```python
class MediaStore:
    def put(self, handle: str, data: bytes, mime: str) -> None: ...
    def get(self, handle: str) -> Tuple[bytes, str]: ...
    def evict(self, handle: str) -> None: ...

class TracePipeline:
    def __init__(self, ..., media_store: MediaStore = InMemoryMediaStore(max_bytes=100_000_000)):
```

Default in-memory with size cap. Disk-backed implementation is a separate class. Exporters call `store.get(handle)` at flush time. Eviction policy on flush success.

### 🟠 HIGH-8. "Langfuse merges partial flushes" is unverified

The plan says `FlushOnGroupEnd` works because Langfuse merges add-on observations to an existing trace. **I have not verified this against Edupia's self-hosted version or even cloud.** If Langfuse creates one trace per partial flush, educa's UI fills with `turn-0`, `turn-1`, ... as separate traces — wrong shape, breaks aggregation, breaks search.

**Fix**: before locking `FlushOnGroupEnd` into Educa Phase 3, write `scripts/probe_langfuse_partial_flush.py` that:
1. Posts a `trace-create` for trace_id X
2. Waits 1s
3. Posts a `span-create` referencing trace_id X
4. Reads back via API; verifies the span appears under the trace, not as a new trace

If it fails on Edupia's self-hosted version, the streaming optimization is dead-on-arrival and we ship without it.

### 🟡 MEDIUM-9. ContextVar binding subtleties under user-spawned tasks

`emitter.annotate(key, value)` reads `_current_emitter_var`. Set inside `_pump`. If user code does `asyncio.create_task(something())` and `something()` calls `annotate`, the task captures the parent's context — annotation goes to the parent's `(op, ctx)`. Sometimes that's what you want; sometimes the task outlives the op and the annotation lands on a closed span.

**Fix**: document this. Provide `emitter.scoped(op, ctx)` context manager for explicit binding when user-spawned tasks need their own scope. Don't try to make it automatic.

### 🟡 MEDIUM-10. `state.tracing` removal breaks the no-op fast path

Currently when no tracer is set, `state.tracing = False` skips all timestamp/metric work. The new design proposes always emitting events. If `pipeline=None`, the emitter must be a no-op `NullEmitter` whose `op_start/op_end/op_yield` are pure pass-throughs. Otherwise we pay TraceEvent allocation cost per op even when not tracing.

**Fix**: explicit `NullEmitter` in core. `engine.start()` chooses `NullEmitter` when `tracer is None`. emit_* methods on NullEmitter are `def op_start(*args, **kwargs): pass` — JIT inlines to nothing.

### 🟡 MEDIUM-11. `render="grouped_timeline"` is stringly-typed

Mixing typed Processors (`DropOps([...])`, `Aggregate(...)`) with stringly-typed exporter render modes (`render="grouped_timeline"`) is inconsistent. New users will mis-spell, get silent fallback to default.

**Fix**: replace with subclasses or strategy objects.

```python
LangfuseTreeExporter(resource="...")           # tree shape
LangfuseGroupedTimelineExporter(resource="...") # grouped flat
LangfuseTimelineExporter(resource="...")        # pure flat
```

Three classes, each renders its own shape. Avoids enum-via-string entirely.

### 🟡 MEDIUM-12. Default `AtScheduledExit` keeps the long-call problem for users who don't migrate

If an educa-style consumer doesn't read the migration guide, they get the same memory growth as today. The default needs to protect them.

**Fix**: bound the default buffer (`max_buffered_events=50_000`). On overflow:
- Drop oldest events
- Log ONE WARNING per call: `"trace buffer overflow at request_id=X — switching to FlushOnSize fallback"`
- Auto-trigger a partial flush

This degrades gracefully instead of OOM-ing.

### 🟢 LOW-13. Event timestamp ties

Concurrent ops emit at the same `datetime.now()` resolution (microsecond on Linux). `TimelineView` sorted by timestamp produces unstable order. The current Langfuse code bumps siblings by `0.1ms` ([langfuse.py:213-222](../operonx/telemetry/tracers/langfuse.py#L213)). New design needs the same trick — or use `event_id` as tiebreaker, or use a monotonic sequence number stamped at emit-time.

**Fix**: add `seq: int` to `TraceEvent`. Emitter increments. Sort by `(timestamp, seq)`.

### 🟢 LOW-14. Per-exporter failure isolation

If `LangfuseExporter` raises (HTTP 503), does `JsonFileExporter` still run? Today's flush_worker catches per-tracer (line 87-89) and collects errors. New design must preserve this — make explicit in spec:

> `TracePipeline.flush()` calls each exporter's `export()` in a try/except, logs failures, raises a `MultiExporterError` after all exporters have been attempted.

---

## §4. Old vs new — head to head

| Concern | Today | New | Improvement | New risk |
|---|---|---|---|---|
| Tree shape rigidity | hardcoded in collector | exporter renders its own | major | none |
| Filter mess | 7-field TraceFilter | composable Processors | major | processor ordering (HIGH-6) |
| Per-yield timing | silently broken (1.1) | per-event timestamps | major | yield flood (SEVERE-1) |
| Long-call buffering | full call in memory | flush_strategy | major | aggregate memory (SEVERE-2) |
| Cancellation visibility | open spans, missing data | OP_END(cancelled) | major | requires scheduler work (SEVERE-3) |
| Sampling | impossible | `Sample()` processor | major | none |
| Custom shapes | rewriter callback (1.2) | first-class Processor | major | none |
| Memory at 100 CCU | bounded by state cells | unbounded by default | **regression** unless SEVERE-1 fixed | bounded buffer needed |
| Failure visibility | rewriter swallows errors (1.2) | fail loud (proposed) | major | none |
| Backend coupling | `node_type` in data | exporter decides | major | LLM_USAGE ordering (HIGH-5) |
| Test surface | hard (state cells) | easy (event list) | major | new test infrastructure needed |
| Migration cost | n/a | 4 phases of operonx + 3 of educa | one-time | breaking changes if T4 deletes legacy |

**Net assessment**: design is a clear win on every axis except memory-at-scale, which only wins after SEVERE-1 and SEVERE-2 are spec'd. Not a fatal flaw, but the plan currently understates how much defaults matter.

---

## §5. Over-engineering check

Things in the plan that smell like complexity-for-its-own-sake:

| Thing | Honest assessment | Verdict |
|---|---|---|
| Separate `EventEmitter` class with ContextVar | Useful for `annotate()` ergonomics; could be `pipeline.emit(...)` directly | **Keep** — ergonomics matter |
| `MEDIA_REF` as separate event kind | Justified — keeps `OP_END` payload bounded; bytes go via MediaStore | **Keep** but spec MediaStore (HIGH-7) |
| `LLM_USAGE` as separate event kind | Justified by streaming LLMs; alternative breaks at runtime | **Keep** but enforce ordering (HIGH-5) |
| `GROUP_START`/`GROUP_END` as event kinds | Cleanest way to express logical grouping in a flat stream | **Keep** |
| `TimelineDebugExporter` | 30 lines, useful for tests/dev | **Keep** |
| `FlushStrategy` pluggable | Justified — five concrete strategies make sense | **Keep** |
| `Sample(rate=0.1)` processor | One line of code that solves a real problem | **Keep** |
| `RedactKeys([...])` processor | Same | **Keep** |
| Folder split: `core/tracing/exporters/` vs `telemetry/exporters/` | Saves lean install dependency tree | **Keep** |
| Legacy `TraceFilter` shim in `legacy.py` | One release of bw-compat is reasonable | **Keep**, delete in T4 |

Nothing screams over-engineered. The bigger risk is **under-specification of defaults** (SEVERE-1, SEVERE-2, MEDIUM-12), not over-design.

---

## §6. Suggested adjustments — concrete spec amendments

Bundle of edits to the plan, ordered by impact:

### 6.1 Add to §3.1 `TraceEvent`
```python
@dataclass(frozen=True, slots=True)
class TraceEvent:
    event_id: str
    request_id: str
    kind: EventKind
    op_name: Optional[str]
    ctx: Tuple[str, ...]
    timestamp: datetime
    seq: int                # ← NEW: monotonic sequence for tiebreak
    payload: Dict[str, Any]
```

### 6.2 Add to §3.2 `EventEmitter`
- `NullEmitter` subclass with no-op methods, used when `tracer is None`
- `emitter.scoped(op_name, ctx)` context manager for user-spawned tasks
- Document that `annotate()` outside an op scope raises `TraceContextError`

### 6.3 Add to §3.3 `Processor`
- Each Processor declares `requires: ClassVar[List[Type]]` (e.g., `Aggregate.requires = [GroupBy]`)
- `TracePipeline.__init__` validates topologically; raises `ProcessorOrderError`
- `Aggregate` uses streaming reduction `(state, event) → state`, not buffer-and-summarize
- New `EnableYields(op_names)` opt-in; default is summary-only for generator ops

### 6.4 Add to §3.4 `Exporter`
- Replace `render="grouped_timeline"` string with subclasses (LangfuseTreeExporter, LangfuseGroupedTimelineExporter, LangfuseTimelineExporter)
- Spec: each exporter declares which `EventKind`s it consumes, can tell pipeline "I don't need MEDIA_REF" → pipeline drops media uploads entirely if no consumer

### 6.5 Add to §3.5 `TracePipeline`
- `max_buffered_events: int = 50_000` (sensible default)
- `media_store: MediaStore = InMemoryMediaStore(max_bytes=100_000_000)`
- Buffer overflow → drop oldest + log + auto-flush; **never raise**, **never OOM**
- Failure isolation across exporters: continue on per-exporter exception, raise `MultiExporterError` at end if any failed

### 6.6 Add to §3 (new subsection) Cancellation contract
> When the scheduler cancels an in-flight op (Phase B Interrupt sweep), it MUST emit `OP_END(status="cancelled", duration_ms=elapsed_so_far)` for that op via the bound emitter, before the brutal cleanup pops the task entry. This is required for trace consistency and matches the existing "every OP_START has a matching OP_END" invariant.

### 6.7 Add to §3 (new subsection) Group lifecycle contract
> When `TracePipeline.shutdown()` runs in the engine's `finally`, the pipeline emits `GROUP_END(status="truncated")` for every open group. Processors that track open groups (`GroupBy`, `Aggregate`) must respond to truncated group_end the same way they would respond to a normal one.

### 6.8 Add to §10 Educa migration — Phase 3 prerequisites
- Add probe script `scripts/probe_langfuse_partial_flush.py` (described in HIGH-8)
- Phase 3 goes prod only if probe returns green on Edupia's Langfuse version

### 6.9 Add to §11 Open questions — answered

Some §11 open questions get firm answers from this review:
- Q3 (LLM_USAGE separate or payload): **separate, enforce ordering** (HIGH-5)
- Q4 (MEDIA_REF bytes vs handle): **handle + explicit MediaStore** (HIGH-7)
- Q6 (Cancellation OP_END): **emit, mandatory** (SEVERE-3)
- Q1 (Sync vs async emit): **sync** — buffer is in-memory, no I/O on emit path

Remaining truly open: Q2 (separate ContextVar vs reuse SCRATCH's — separate, low-cost decision), Q5 (per-yield timing — solved by yield flood policy in 6.3).

---

## §7. Open questions to resolve before any code

These ARE blocking. Don't skip them.

1. **Default policy for generator op yields**: `emit_yields=False` by default (summary only) or `True` (full per-yield)? Educa's audio op at 50Hz makes the answer obvious — but the spec must say it explicitly.
2. **Aggregate streaming reduction signature**: `(state, event) → state` — what's the type of `state`? `Any`? A dataclass? Document with one full example.
3. **Langfuse partial-flush behavior**: Run the probe script. If it fails, drop FlushOnGroupEnd from educa Phase 3 (or spec a workaround like "one trace per turn, link via session_id").
4. **`max_buffered_events` default**: 50k? 100k? At ~2KB/event after truncate, 50k = 100MB per call. At 100 CCU = 10GB. Either too high or fine — depends on actual CCU. Need data.
5. **MediaStore eviction policy**: on flush success, evict referenced handles? On call end? On store-size limit? Each has different memory characteristics. Pick one explicitly.

---

## §8. Verdict

**The design is solid. Ship it after the §6 amendments.**

Specifically:
- The four-abstraction shape (`TraceEvent` → `Emitter` → `Pipeline` → `Exporter`) is the right contract. None of the issues I found are architectural — they're all spec gaps in defaults and edge-case behaviors.
- SEVERE-1 (yield flood) and SEVERE-2 (Aggregate buffering) are spec problems, not design problems. Fixed by 6.3.
- SEVERE-3 (cancellation) is a known scheduler change that was glossed over in §11; promote to a hard requirement (6.6).
- The remaining HIGH/MEDIUM issues are normal "didn't think of that yet" gaps that any large refactor surfaces.

**What I'd insist on before coding starts:**
- Spec amendments §6.1 through §6.7 land in the plan doc
- Probe script for Langfuse partial flush runs green on Edupia's actual Langfuse version
- Memory test harness: simulate a 5-min callbot at 50Hz for one of the new processor configurations, measure peak RSS, gate at <50MB per call

**What I'd defer:**
- Rust mirror — Python ships first, port after stable
- OTel exporter — not on educa's path
- Disk-backed MediaStore — InMemory is fine for now

If the user agrees to amendments §6, the actual implementation work breaks down naturally into the 4 operonx phases already in the plan, with the new defaults baked in from T1.

---

## §9. What I'd push back on if challenged

If someone says "this is over-designed," I'd defend:
- Processors over a single-method TraceFilter: composability is real value
- Separate Emitter class: ContextVar ergonomics for `annotate()` is worth one class
- Separate kinds for LLM_USAGE/MEDIA_REF: streaming + memory bounds make this not optional

If someone says "this isn't enough":
- It's not OTel-compatible by design — fight that fight when an actual OTel consumer shows up
- It's not distributed-tracing — single-process is the scope
- It's not metrics — that's a separate system

If someone says "what about Hush/legacy users":
- T1-T3 maintain bw-compat
- T4 deletes after deprecation warnings — one minor release
- educa stays on Educa-1 (zero changes) until they're ready
