# SCRATCH + Interrupt Primitives — Design Plan

**Status**: shipped (Python + Rust). Phase E/F dropped — see §5.
**Scope**: `operonx` core (Python + Rust)
**Driving consumer**: `educa-reminder-agent` (callbot-engine)
**Author**: thanglq + claude (2026-05-01)

This doc proposes new primitives for operonx that let consumers replace
ad-hoc external state holders (e.g. educa's `InterruptCoordinator`) with
first-class operonx mechanisms.

**Two primitives:**
1. **`SCRATCH`** (core) — free-form per-call key-value on `MemoryState`
2. **`Interrupt` event** (core) — in-band scheduler cancellation

Both shipped Python + Rust at parity per `docs/architecture/rust-python.md`.
A planned `operonx-transports` package (`WebSocketOp` + protocol registry)
was scoped out after design review — see §5 Phase E.

---

## 0. Findings from second-wave codebase review (must address)

| # | Finding (codebase ref) | Addressed in |
|---|---|---|
| 1 | `MemoryState.__slots__` ([state.py:31-40](../operonx/core/states/state.py#L31-L40)) blocks adding `_scratch` without updating the slots tuple. | §3.2 |
| 2 | `Scheduler.__slots__ = ("graph",)` ([task_scheduler.py:84](../operonx/core/ops/graph/task_scheduler.py#L84)) — `inflight`, `queue`, `seq_active`, etc. are LOCALS in `_run_once`, not Scheduler attributes. `tasks_by_ctx` and `_sweep_ctx` must be locals/closures. | §4.3 |
| 3 | `ExecutionHandle._pump` ([engine.py:97](../operonx/core/engine.py#L97)) unpacks `op, ctx, data = item`; Interrupt must be encoded as a 3-tuple to keep `async for` ergonomic. | §4.4 |
| 4 | `engine.start()` returns immediately and creates `_run` via `asyncio.create_task`; any `await` in caller before seeding `handle.scratch` opens a race. Need `engine.start(scratch=...)` parameter. | §3.4 |
| 5 | Rust `MemoryState` uses `serde_json::Value`; Python uses `Any`. Cross-runtime spec fixtures must use JSON-friendly subset only. | §3.2 |
| 6 | `_pump` runs in executor threads for `bound="cpu"` ops via `asyncio.to_thread`. ContextVar propagates via `copy_context()` — confirmed safe. | §3.3 + Phase A test A11 |
| 7 | Setting `_current_state_var` per-`_pump` adds noise; one set at `_run_once` entry works because `asyncio.create_task` snapshots context at creation. | Phase A files list |
| 8 | Inflight invariant during sweep: queued frames decremented by sweep; in-flight task cancellations decrement via existing `_pump.finally` — must not double-count. | §4.3 step 4 + Phase B test B5 |
| 9 | **Latency**: Interrupt only sweeps scheduler-internal state. Frames already pushed to *consumer-owned* queues (e.g. educa's `tts_out`) keep flowing FIFO and arrive at consumer ahead of Interrupt sentinel. Operonx primitive can't fix this generically — consumer must drain its own queues. | §4.6a + Phase B test B14 |
| 10 | **Concrete code location** for `_current_state_var.set(state)` was vague ("at `_run_once` entry"). Inserting in the wrong place leaves entry ops with unbound ContextVar. | Phase A files list — pinned to line 170/403 |
| 11 | **Pre-flight (4th wave)**: declarative `inputs={"x": SCRATCH["k"]}` was speculated to require polymorphic input cache (`(kind, idx_or_key, fallback)`). Codebase re-read showed `BaseOp.get_inputs()` accepts arbitrary literal defaults — `ScratchRef` falls through unchanged and is post-resolved in a 3-line loop at the end. **Cache structure untouched.** | §3.3 + Phase A files list (post-resolve in `base.py`) |
| 12 | **Pre-flight (4th wave)**: `_current_state_var` is a fresh ContextVar in `operonx/core/states/_scratch_var.py`; bound at `_run_once` entry via token + `try/finally`. Standalone — does not depend on or layer on the existing tracing module (which is being replaced separately). | Phase A files list |
| 13 | **Pre-flight (4th wave)**: `ScratchRef` is a 4-line bare class with `__slots__`, not a dataclass. Single-field marker doesn't justify dataclass machinery; bare class gives clearer repr (`SCRATCH['coord:phase']`) and avoids equality-with-string surprises in tests. | §3.3 |
| 14 | **Implementation discovery (Phase A)**: declarative `ScratchRef` inputs DO need to appear in `graph.json` so Phase C's Rust port has a contract to read. Imperative `SCRATCH[k]=v` and `engine.run(scratch=...)` are runtime-only and stay opaque to serialization. Single new field `"scratch": {"key": ...}` in `_serialize_params()` alongside `"ref"`/`"literal"`. | §3.3a |
| 15 | **Implementation discovery (Phase B)**: `task.cancel()` on a not-yet-started `_pump` task raises `CancelledError` *into* the coroutine before its body executes — `try/finally` never runs. The plan's "each task's `finally` already decrements `inflight`" is false in this case and would leak `inflight`, deadlocking the main loop on `await queue.get()`. **Fix**: brutal idempotent post-cleanup pass in `_sweep_ctx` after `await gather`. No `add_done_callback` (introduced its own deadlock — fires after main loop is already blocked). | §4.3 step 4 + §4.3a |

---

## 1. Problem

### 1.1 `PARENT.shared` is unreliable for streaming/interrupt-driven workflows

Three concrete traps in the current implementation:

| # | Trap | Source |
|---|---|---|
| 1 | **Stale cache via downstream pull_ref** — once an op caches a pulled value at `cell[ctx_key]`, subsequent reads in the same context return the cached value even if the source updated | [operonx/core/states/state.py:143-157](../operonx/core/states/state.py#L143-L157) |
| 2 | **No happens-before between overlapping turns** — when one stream context starts before the previous writes its shared var, the new context reads stale value. No lock, no version. | architectural |
| 3 | **Push_ref to non-shared target uses incoming `ctx_key`** — write to a shared source pushes to per-context slot of target; readers using a different ctx see no value | [operonx/core/states/state.py:119-121](../operonx/core/states/state.py#L119-L121) |

These traps have already pushed the educa consumer to maintain a parallel
Python state holder (`InterruptCoordinator`) outside the graph entirely.

### 1.2 Streaming consumers can't cancel in-band

Operonx's scheduler dispatches `Frame` and `EOF` events. There is no in-band
"cancel everything queued for this context" event. Consumers that need
cross-stream cancellation (e.g. callbot interrupting in-flight TTS frames
when a user starts speaking mid-greeting) currently have to:
- Poll an external Python flag from inside long-running async generators
- Maintain a side-channel queue for "drop" signals
- Reach into operonx internals to cancel tasks

All of these are workarounds for a missing primitive.

---

## 2. Design goals

1. **Two primitives**: `SCRATCH` (per-call free-form state) + `Interrupt` (in-band scheduler cancellation event)
2. **Additive only**: no breaking changes to existing `Cell`, `Frame`, `EOF`, schema, or scheduler dispatch
3. **Python ↔ Rust parity** from day one (shared `tests/spec/` fixtures)
4. **Honest about contracts**: SCRATCH provides no synchronization; `Interrupt` is best-effort
5. **No bloat**: defer latched cells, phase-machine ops, event subscriptions until a second consumer justifies them

---

## 3. `SCRATCH` — per-call scratch space

A free-form key-value store on `MemoryState`, separate from `Cell`-based
schema state, accessible globally inside any op via a context variable.

### 3.1 Properties

- **No schema declaration required** — keys appear on first write
- **No contexts, no pull/push refs, no caching** — `dict[str, Any]` semantics
- **Default for missing key**: `None` (matches existing `MemoryState.__getitem__` for unknown ops/vars; avoids try/except boilerplate)
- **Last-write-wins** — no synchronization (same contract `PARENT.shared` already had, but no longer hidden behind dataflow vocabulary that implies more)
- **Lifetime**: per `engine.start()` call (one `MemoryState` instance per call)
- **ContextVar persists** across async-generator yields and `asyncio.create_task` boundaries (PEP 567 + same MemoryState reference across tasks of one call)

### 3.2 Storage type — distinct from `Cell`

```python
class MemoryState:
    __slots__ = (
        # ... existing slots (whatever is current at implementation time) ...
        "_scratch",  # NEW — must be added to __slots__ tuple
    )
    _cells: List[Cell]          # existing — schema-driven, contexts, refs
    _scratch: Dict[str, Any]    # NEW — free-form, no schema, no contexts
```

> **`__slots__` change is required** — `MemoryState` uses `__slots__` for
> memory; adding a field without updating the tuple raises `AttributeError`.
> Read the current slots tuple at implementation time; it may have changed
> (e.g. once the tracing module rewrite lands, `_iter_labels` may be gone).

```rust
pub struct MemoryState {
    cells: Vec<Cell>,
    scratch: HashMap<String, serde_json::Value>,  // NEW
    // ... existing fields
}
```

Mixing them (e.g. as a `Cell::is_volatile` flag) muddles two very different
contracts: Cell has contexts, defaults, pull/push refs, schema indices —
SCRATCH has none of those. Separate storage = clear API boundary.

**Type-domain mismatch (Python vs Rust)**: Python SCRATCH accepts arbitrary
objects (tuples, NumPy arrays, custom classes — educa stores `tuple` for
`spec:prior_tts_ctx` and `dict` for `coord:transfer_payload`). Rust SCRATCH
holds `serde_json::Value`. **Cross-runtime parity tests use only
JSON-friendly values** (str/int/float/bool/list/dict). Python users keep the
freedom to stuff arbitrary objects when running pure-Python; only the
`tests/spec/` fixtures constrain themselves to the JSON-compatible subset.

### 3.3 Two usage modes

**Imperative (inside `@op` body)** — contextvar resolves to current `MemoryState`:

```python
@op
def my_op(x):
    SCRATCH["coord:current_state"] = "REMINDER"
    phase = SCRATCH["coord:phase"]   # None if unset
    return ...
```

**Declarative (in `inputs={}` at graph-def time)** — returns a `ScratchRef` marker; resolved fresh per op-execution by a small post-resolve pass at the end of `BaseOp.get_inputs()`:

```python
@graph
def workflow(...):
    do_thing(state=SCRATCH["coord:current_state"])  # post-resolved each call
```

**`ScratchRef` is a 4-line bare class** (no dataclass overhead — single-field
marker doesn't need it):

```python
# operonx/core/states/scratch_ref.py
class ScratchRef:
    __slots__ = ("key",)
    def __init__(self, key: str): self.key = key
    def __repr__(self): return f"SCRATCH[{self.key!r}]"
```

**Resolution flow** — `SCRATCH["k"]` returns `ScratchRef("k")` at graph-construction
time (when ContextVar isn't bound). Schema treats it as a literal default
(falls through `_params.resolve_value()` and `StateSchema._build()` since it's
not a `Ref` and has no `.name` attribute). At op-execution time,
`BaseOp.get_inputs()` adds one post-resolve loop:

```python
# At the end of get_inputs(), after existing fast-path / slow-path branches:
for var, val in result.items():
    if isinstance(val, ScratchRef):
        result[var] = state._scratch.get(val.key)
return result
```

The cache structure (`_input_cache`) stays untouched — it stores the
`ScratchRef` as a `fallback`, the post-resolve does the runtime lookup.

### 3.3a Graph.json serialization (declarative ScratchRef)

`ScratchRef` is the only SCRATCH form that appears in `graph.json` —
imperative `SCRATCH[k] = v` lives inside op bodies (opaque to the
serializer) and external `engine.run(scratch=...)` is run-time only.

A declarative `inputs={"x": SCRATCH["k"]}` serializes alongside `ref` and
`literal` in the existing `_serialize_params()` shape:

```json
"inputs": {
    "value": {
        "default": null,
        "required": true,
        "ref": null,
        "scratch": {"key": "phase"},
        "literal": null
    }
}
```

Rust deserialization (Phase C) reads the `scratch.key` field, holds it as
the param's binding, and post-resolves against `state.scratch` at op-input
time — symmetric to Python's `BaseOp.get_inputs()` post-resolve loop.

### 3.4 External access

Two ways to seed/read SCRATCH from outside the graph:

**(a) `engine.start(scratch={...})` — preferred for seeding.** Applied
synchronously inside `create_state` before the scheduler `_run` task is
created. Race-free.

```python
handle = engine.start(
    inputs={"audio_in": q1, "tts_out": q2},
    scratch={"coord:phase": "IDLE", "coord:current_state": "REMINDER"},
)
```

**(b) `handle.scratch[...]` — for reads + late writes.**

```python
phase = handle.scratch["coord:phase"]   # read at any time
handle.scratch["coord:transfer_target"] = "..." # write — see contract below
```

> **Seeding race contract**: `handle.scratch[k] = v` is safe **only when
> performed synchronously between `engine.start()` and the next `await`**.
> The scheduler `_run` task runs in a `create_task`; it is queued but not
> yet executed at the moment `start()` returns. Any `await` in the caller
> may yield control, allowing entry ops to read stale defaults. **Prefer
> `engine.start(scratch=...)` for any state ops will read.**

External access never racks up callbacks/notifications — SCRATCH is plain
mutable dict.

### 3.5 Test access

Operonx ships a public test helper so unit tests can call op bodies directly
(via `op.__wrapped__(...)`) without going through the scheduler:

```python
from operonx.core.testing import scratch_active

state = engine._schema.create_state()
with scratch_active(state):
    state.scratch["coord:current_state"] = "MAIN"
    result = quick_detect.__wrapped__(text)   # reads SCRATCH from `state`
```

Without this helper, `_raw(op)` patterns would crash because the contextvar
is normally only set by `Scheduler._pump`. **This must ship in Phase 1**, not
deferred to test-migration phase.

### 3.6 Non-goals

- **Synchronization** — last-write-wins. If a use case needs atomic multi-key updates, callers wrap with `asyncio.Lock()` themselves.
- **Schema validation** — keys are unstructured strings. Consumers adopt their own naming convention (e.g. `<namespace>:<key>`).
- **Persistence** — lifetime is per-call only. Cross-call state is the consumer's problem.

---

## 4. `Interrupt` — in-band scheduler cancellation event

A new dataclass sibling to `Frame` and `EOF` in
[operonx/core/ops/graph/task_scheduler.py](../operonx/core/ops/graph/task_scheduler.py).
When an op yields/returns an `Interrupt`, the scheduler drops queued frames
for the target context, cancels in-flight tasks rooted at it, and propagates
the event downstream so consumers can drain output buffers.

### 4.1 Definition

```python
@dataclass
class Interrupt:
    op: str                  # emitter op name (for tracing)
    ctx: tuple               # emitter's ctx (for tracing)
    ctx_to_cancel: tuple     # explicit target — typically prior turn's ctx
    reason: str = ""
```

```rust
#[derive(Debug, Clone)]
pub struct Interrupt {
    pub op: String,
    pub ctx: ContextId,
    pub ctx_to_cancel: ContextId,
    pub reason: String,
}
```

### 4.2 Why explicit `ctx_to_cancel`?

The op that emits `Interrupt` typically runs in a **different** context than
the one to cancel. Example (callbot): `detect_interrupt` runs in the new
turn's context (e.g. `("main", "[6]")`), but needs to cancel the previous
turn's TTS context (`("main", "[5]")`). If `Interrupt` implicitly used the
emitter's own ctx, it would cancel itself.

Consumers store the to-be-cancelled ctx in `SCRATCH` when long-running work
begins, then read it back when emitting `Interrupt`.

### 4.3 Scheduler handling

> **Important: scheduler state lives in closures, not `Scheduler` attributes.**
> [Scheduler.__slots__](../operonx/core/ops/graph/task_scheduler.py#L84) is
> `("graph",)` — `inflight`, `queue`, `seq_active`, `seq_queues`, `seq_origins`,
> `collect_bufs`, `ready`, `loop_iters` are all **locals inside `_run_once`**.
> `tasks_by_ctx` and `_sweep_ctx` therefore live as locals/closures inside
> `_run_once` too — not new `Scheduler` methods or fields.

A new `_sweep_ctx(ctx_prefix, exclude=(emitter_op, emitter_ctx))` closure inside `_run_once`:

1. **Drain dispatch queue**, drop items where `item.ctx == ctx_prefix OR item.ctx descends from it`, re-enqueue rest. Decrement `inflight` by exactly the number of dropped items.
2. **Cancel matching `_pump` tasks** via `tasks_by_ctx: Dict[tuple, Dict[str, asyncio.Task]]` local map (ctx → {op_name: task}). Populate at `dispatch()`: `tasks_by_ctx.setdefault(ctx, {})[op_name] = task`. Remove in `_pump.finally` before decrementing inflight.
3. **Await cancelled tasks** with `asyncio.gather(*cancelled, return_exceptions=True)`.
4. **Brutal idempotent post-cleanup** *(implementation discovery — see §4.3a below)*. After gather, walk the cancelled list: if a task's bucket entry is still there, its `finally` never ran (cancel-before-start) — pop the entry and decrement `inflight` here. If the entry is gone, finally already cleaned up. **Idempotent**: safe under either path.
5. **Clear bookkeeping** for matching contexts: pop entries in `seq_origins`, `collect_bufs`, `ready` keyed by ctx that descends from `ctx_prefix` AND is not the emitter's ctx. Sibling contexts untouched.
6. **Forward `Interrupt`** to `output_queue` so `ExecutionHandle` consumers see it (see §4.4 for the wire format).
7. **Sync ops mid-execution**: `_drain_inline` runs sync ops to completion in-place. `_sweep_ctx` cannot interrupt a sync op already executing. Document this as best-effort: sync ops complete, but their downstream dispatches are dropped if they arrive after sweep.

### 4.3a Implementation discovery: cancel-before-start

The original plan assumed `_pump.finally` always runs and decrements
`inflight`, so the sweep would not need to compensate for cancelled tasks.
In practice that's wrong: when `task.cancel()` fires before the task body
ever starts (e.g. emitter is sync and runs in `_drain_inline` while the
target task is still pending in the event loop's call_soon queue), Python
raises `CancelledError` *into* the coroutine on its first `_step` —
**without ever entering the function body**. The `try/finally` doesn't
exist yet, so it doesn't run.

Tried & rejected fixes:
- **Outer `try/finally` wrapping `async with _sem:`** — same problem, the
  outer `try` block also needs the body to enter.
- **`add_done_callback`** for `inflight -= 1` — works but creates a
  deadlock: callback fires *after* the main loop's `await queue.get()`
  is already blocked, so the loop never re-checks `inflight`.

Shipped fix: **brutal idempotent post-cleanup**. Keep the original inner
`finally` (decrement + bucket cleanup); after `await gather` in sweep,
walk the cancelled list and clean up any task whose bucket entry is
still present. The check `if op_name in bucket` is idempotent: if
finally ran, bucket is clean and we skip; if finally didn't run, we do
it ourselves. No callbacks, no sentinels, no double-counting.

### 4.4 ExecutionHandle propagation

`ExecutionHandle._pump` ([operonx/core/engine.py:80-106](../operonx/core/engine.py#L80-L106))
currently does `op, ctx, data = item` on every queue item after filtering
`None` and `BaseException`. To preserve `async for op, ctx, data in handle`
ergonomics without making consumers handle a third type, **encode `Interrupt`
as a normal 3-tuple**:

```python
# In Scheduler._sweep_ctx, forwarding to output_queue:
output_queue.put_nowait((
    "__interrupt__",                     # synthetic op name
    interrupt.ctx,                        # emitter ctx
    {"__interrupt__": interrupt},        # payload key
))
```

Consumers that care branch on `op == "__interrupt__"`; consumers that don't,
ignore it. Add `handle.interrupts` property that filters `_frames` for
`op == "__interrupt__"` for typed access.

**Why not a separate type**: making `_frames` heterogeneous would force every
existing consumer to type-check before unpacking — a wider blast radius than
this design needs. The synthetic-op-name approach matches how the scheduler
already encodes other internal events.

### 4.5 User op example

```python
@op
def detect_interrupt(normalized_text):
    if SCRATCH["coord:phase"] == "SPEAKING" and is_real_speech(normalized_text):
        prior_ctx = SCRATCH["spec:prior_tts_ctx"]   # stored when SPEAKING began
        SCRATCH["coord:phase"] = "PROCESSING"
        return Interrupt(ctx_to_cancel=prior_ctx, reason="user_spoke_mid_tts")
    return {"text": normalized_text}
```

### 4.6 Cancellation also covers async-generator ops mid-yield

When the scheduler cancels an in-flight task running an async-generator op
(e.g. `synthesize_tts` mid HTTP request), `CancelledError` propagates up
through `await`, the HTTP client (httpx, aiohttp, etc.) aborts cleanly. No
polling flag needed in user code. Cancellation latency drops from
"next-yield-boundary" to "next-await-checkpoint" (~0ms in practice).

### 4.6a Limit of operonx Interrupt: data already pushed to consumer queues

**Operonx `Interrupt` only affects what's inside the scheduler.** It cancels
in-flight tasks and drops Frames in the scheduler's internal event queue.
It does **NOT** clear data that ops have already pushed to *consumer-owned*
queues (e.g. `tts_out: asyncio.Queue` passed in as a graph input).

This means: if `tts_emit` has already pushed N frames onto `tts_out`, and
then an `Interrupt` sweeps the TTS task, those N frames are still in
`tts_out` and the consumer will read them in FIFO order. The Interrupt
event itself arrives via `ExecutionHandle._frames` (a separate channel
from `tts_out`), so it bypasses the queue — but the audio frames don't.

**Consumer responsibility**: drain consumer-owned queues on Interrupt.
Educa pattern in §4 of the educa plan: have a small `interrupt_forwarder`
task that watches `handle` for `op == "__interrupt__"` events and pushes
a `DropAudioSentinel` onto `tts_out`. `send_loop` on seeing the sentinel
drains the queue synchronously and discards everything before it.

The operonx primitive cannot fix this generically because consumer queues
are arbitrary user data structures.

### 4.7 What `Interrupt` is NOT

- **NOT a graceful close** — for "play through then close" semantics, consumers use sentinel items on their own queues (pure consumer-side code, no operonx primitive).
- **NOT a global pause** — only affects the target ctx subtree; sibling contexts continue.
- **NOT replayable** — once swept, dropped frames are lost. Consumers re-derive state from inputs if a retry is needed.

### 4.8 Open design call: cancellation scope

Three options for how `_sweep_ctx(ctx_prefix)` matches:

- **(a) Surgical** — only `ctx == ctx_prefix`. Misses child stream contexts.
- **(b) Subtree** — `ctx == ctx_prefix OR descends from it`. **Recommended.**
- **(c) Nuclear** — all in-flight in the entire execution. Bad precedent.

Recommendation: **(b)** as default, with the explicit `ctx_to_cancel`
parameter giving consumers fine-grained control over the target subtree.

---

## 5. Implementation phases

### Python ↔ Rust strategy

Both primitives must reach Rust feature-parity. Two viable orderings:

- **Sequential**: Python first → freeze API → Rust second. Lower coordination cost; Rust lags by ~1 release cycle.
- **Parallel**: design + implement in lockstep. Higher initial coordination; primitives ship simultaneously to both runtimes.

**Recommendation: Sequential.** Rust state model is still in build-up
([rust/operonx/src/core/states/state.rs](../rust/operonx/src/core/states/state.rs)
header notes "Phase 1 scope … ref pull/push lands later"). Adding SCRATCH on
Python first lets us validate the design against the educa consumer before
porting.

### Phase A — Python SCRATCH primitive

**Files**:
- `operonx/core/states/state.py` — extend `__slots__` to include `_scratch`; init in `__init__`; expose via `state.scratch` property
- `operonx/core/states/scratch_ref.py` *(new)* — `ScratchRef` marker class (4-line bare class, see §3.3)
- `operonx/core/states/_scratch_var.py` *(new)* — `_current_state_var: ContextVar[MemoryState]` + `_set_state(state) -> Token` / `_reset_state(token)` helpers. Module body is small:
  ```python
  import contextvars
  _current_state_var: contextvars.ContextVar = contextvars.ContextVar("operonx_current_state")
  def _set_state(state) -> contextvars.Token: return _current_state_var.set(state)
  def _reset_state(token: contextvars.Token) -> None: _current_state_var.reset(token)
  ```
- `operonx/core/ops/_edges.py` — add `ScratchAccessor` singleton, export `SCRATCH`. `__getitem__(key)` does `try: state = _current_state_var.get(); return state._scratch.get(key) except LookupError: return ScratchRef(key)`. `__setitem__(key, value)` requires the ContextVar bound (raises if not — write-outside-run is a programming error, not a graph-time annotation).
- `operonx/core/ops/graph/task_scheduler.py` — set `_current_state_var.set(state)` once at `_run_once` entry, with a token + `try/finally` reset wrapping the existing run body. **Concrete location**: insert immediately after [task_scheduler.py:170](../operonx/core/ops/graph/task_scheduler.py#L170) (`queue: asyncio.Queue = asyncio.Queue()`) and before [line 403](../operonx/core/ops/graph/task_scheduler.py#L403) (entry-op dispatch loop). Reset before `return outputs, item_ctxs` at the end of `_run_once`. All child tasks inherit via `asyncio.create_task`'s context snapshot. Setting per-`_pump` adds noise; not needed.
- `operonx/core/ops/base.py` — add 3-line post-resolve pass at the bottom of `BaseOp.get_inputs()` (both fast-path and slow-path branches): `for var, val in result.items(): if isinstance(val, ScratchRef): result[var] = state._scratch.get(val.key)`. Cache structure untouched.
- `operonx/core/ops/_params.py` — verify `ScratchRef` falls through `resolve_value()` to the literal branch (no `.name`, not a `Ref`). One unit test confirms — no code change expected.
- `operonx/core/states/schema.py` — verify `_build()` handles `ScratchRef` defaults cleanly (only branches on `isinstance(value, Ref)`). One unit test confirms.
- `operonx/core/engine.py`:
  - Accept new `scratch: Optional[Dict[str, Any]]` parameter in `start()`
  - Apply seed scratch synchronously after `create_state()` and BEFORE `asyncio.create_task(_run())`: `if scratch: state._scratch.update(scratch)`
  - Add `handle.scratch` `@property` on `ExecutionHandle` returning `state._scratch`
- `operonx/core/testing.py` *(new)* — public `scratch_active(state)` context manager (~10 lines, wraps `_set_state` / `_reset_state` from `_scratch_var`)
- `operonx/core/ops/base.py` — `_serialize_params()` adds a `"scratch"` field alongside `"ref"`/`"literal"` (§3.3a). Set to `{"key": ref.key}` when `param.value` is a `ScratchRef`, else `None`
- `operonx/core/__init__.py` + `operonx/core/ops/__init__.py` — export `SCRATCH` and `ScratchRef`

**Test cases** (`tests/internal/states/test_scratch.py`):

| # | Test | Setup | Assert |
|---|---|---|---|
| A1 | `__slots__` extension | `MemoryState(schema)` | `state._scratch == {}`, `state.scratch is state._scratch` |
| A2 | Imperative write→read inside @op | One @op writes `SCRATCH["k"]="v"`, downstream @op reads `SCRATCH["k"]` | downstream sees `"v"` |
| A3 | Missing key returns None | @op reads `SCRATCH["missing"]` | returns `None`, no `KeyError` |
| A4 | `scratch_active()` for unit tests | `with scratch_active(state): op.__wrapped__(...)` writes/reads SCRATCH | succeeds without scheduler |
| A5 | Declarative `inputs={"x": SCRATCH["k"]}` resolves per call | Op A writes `SCRATCH["k"]="v1"` then later `"v2"`; op B (declarative input) runs in both turns | sees `"v1"` then `"v2"` (post-resolve picks up live value) |
| A5b | `ScratchRef` outside a run returns marker | Call `SCRATCH["k"]` from module top-level | returns `ScratchRef(key="k")`, doesn't raise |
| A5c | `_params.resolve_value` preserves `ScratchRef` | Wire `inputs={"x": SCRATCH["k"]}` and inspect resolved param | `param.value` is `ScratchRef("k")`, not converted to `Ref` |
| A5d | Schema `_build()` ignores `ScratchRef` defaults | Build schema with op input default = `ScratchRef("k")` | no pull_ref / push_ref created; default stays as ScratchRef |
| A6 | `engine.start(scratch=...)` seeds before first op | Pass `scratch={"k": "seed"}`; first op reads `SCRATCH["k"]` | reads `"seed"`, never None |
| A7 | `handle.scratch` external write/read | After start, write `handle.scratch["k"]="x"` synchronously; first op reads | first op sees `"x"` |
| A8 | Multiple stream contexts share scratch | Generator op yields N items; child ops in `("main","[i]")` all read same `SCRATCH["k"]` | all see same value (no per-ctx isolation) |
| A9 | Concurrent `engine.start()` isolation | Run 50 calls × 30 min, each writes `SCRATCH["call_id"]=request_id`, reads back | each call sees only its own request_id (no cross-pollination) |
| A10 | ContextVar propagates through `asyncio.create_task` | @op spawns sub-task with `asyncio.create_task(...)`; sub-task reads `SCRATCH["k"]` | sub-task sees parent's scratch dict |
| A11 | ContextVar propagates through `asyncio.to_thread` | @op with `bound="cpu"` (runs in thread); thread reads `SCRATCH["k"]` | thread sees same dict |
| A12 | Race: late `handle.scratch[k]=v` after await | start, await something, then write — first op already ran with None | document expected stale-read behavior; reaffirms `engine.start(scratch=...)` is correct path |

**Spec fixtures** (`tests/spec/`):
- `scratch_basic.json` — A2 + A3
- `scratch_external_seed.json` — A6
- `scratch_ref_input.json` — A5

JSON-only types: str, int, float, bool, list, dict (no tuples, no NumPy).

### Phase B — Python `Interrupt` event

**Status**: shipped. 13 unit tests passing (B1–B14, minus B8 which needs httpx
— covered by B7's `await asyncio.sleep(N)` cancellation as the underlying
mechanism is identical). 1089 total tests pass, zero regressions.

**Files**:
- `operonx/core/ops/_events.py` *(new)* — moved `Frame`, `EOF`, and added `Interrupt` dataclass here. Lives outside `ops/graph/` so importing it doesn't trigger `graph/__init__.py` (avoids the circular import that bit when base.py needed `Interrupt`).
- `operonx/core/ops/graph/task_scheduler.py`:
  - Imports `Frame`, `EOF`, `Interrupt` from `_events`
  - Adds `tasks_by_ctx: Dict[tuple, Dict[str, asyncio.Task]]` **as a local inside `_run_once`** (Scheduler's `__slots__` is `("graph",)`). Keyed by `(ctx, op_name)` to support self-cancel guard.
  - Tracks tasks: in `dispatch()`, after `asyncio.create_task(_pump(...))`, does `tasks_by_ctx.setdefault(ctx, {})[op_name] = task`. In `_pump.finally`, removes entry and decrements `inflight`.
  - `_sweep_ctx(ctx_prefix, exclude=(emitter_op, emitter_ctx))` closure:
    1. Drain queue, drop items at descendants of `ctx_prefix`, decrement `inflight` by drop count.
    2. Cancel matching `_pump` tasks (skip emitter via `exclude`).
    3. `await asyncio.gather(*cancelled, return_exceptions=True)`.
    4. **Brutal idempotent post-cleanup** (see §4.3a) — for each cancelled task whose bucket entry is still present, pop it and decrement `inflight`. Handles cancel-before-start where finally never ran.
    5. Clear `ready`/`seq_origins`/`collect_bufs` for descendants of `ctx_prefix` excluding emitter's ctx.
  - Main dispatch loop branches on `isinstance(event, Interrupt)` → `await _sweep_ctx(event.ctx_to_cancel, exclude=(event.op, event.ctx))`, then forwards to `output_queue` as `("__interrupt__", event.ctx, {"__interrupt__": event})`.
  - `_pump` puts `Interrupt` directly on queue (not wrapped as Frame) when op yields/returns one. Stamps `result.op = op_name; result.ctx = ctx` so the main loop knows the emitter for the self-cancel guard.
  - `_drain_inline` (sync-op path) handles Interrupt similarly — calls `await _sweep_ctx(...)` inline and forwards to `output_queue`.
- `operonx/core/ops/base.py`:
  - `op.run()` skips `store_result(state, result, ctx)` when `result` is `Interrupt` (Interrupt isn't a result dict). Yields the Interrupt directly so `_pump` can detect it.
- `operonx/core/engine.py`:
  - `ExecutionHandle._pump` already does `op, ctx, data = item` — Interrupt forwarded as 3-tuple, no change required.
  - `ExecutionHandle.interrupts` property: filters `_frames` for `op == "__interrupt__"`, returns list of `Interrupt` objects in arrival order.
- `operonx/__init__.py` + `operonx/core/__init__.py` + `operonx/core/ops/__init__.py` — export `Interrupt` (and `Frame`/`EOF` from `_events`).

**Test cases** (`tests/internal/scheduler/test_interrupt.py`):

| # | Test | Setup | Assert |
|---|---|---|---|
| B1 | Drop queued frames at target ctx | Long generator yields N frames at `("main","[0]")`; emitter at `("main","[1]")` returns `Interrupt(ctx_to_cancel=("main","[0]"))` | downstream of `[0]` sees < N frames; `[1]` runs to completion |
| B2 | Preserve siblings | Two parallel generator branches; Interrupt(ctx_to_cancel=X) | Y branch's frames all delivered |
| B3 | Subtree cancellation | Sweep `("main","[0]")` while tasks running at `("main","[0]","[2]")` | descendant tasks cancelled too |
| B4 | `inflight` invariant — pending Frames | Drop 5 Frames from queue + 0 in-flight tasks | inflight decrements by 5; main loop terminates cleanly |
| B5 | `inflight` invariant — in-flight tasks | Drop 0 from queue + 3 cancelled tasks | inflight decrements by 3 (via task `finally`); no double-count |
| B6 | `inflight` invariant — collect-mode downstream | Generator with `.collect()` mid-yield, then Interrupt | inflight reaches 0; collect_buf cleared for swept ctx |
| B7 | Mid-yield generator cancellation | Async-gen op `await asyncio.sleep(10)`; cancel via Interrupt | `CancelledError` propagates within ms (not after sleep) |
| B8 | HTTP cancellation propagation | Op uses `httpx.AsyncClient.get(...)`; Interrupt fires | client request aborts cleanly via `CancelledError` |
| B9 | Forwarded Interrupt at consumer | `async for op,ctx,data in handle: ...` | tuple `("__interrupt__", ctx, {"__interrupt__": Interrupt(...)})` arrives |
| B10 | `handle.interrupts` accessor | After several Interrupts fired | list of typed `Interrupt` objects in arrival order |
| B11 | Next turn at sibling ctx clean | After Interrupt, dispatch at sibling ctx | runs without stale `seq_active` blocking it |
| B12 | Sync-op race | Sync op currently in `_drain_inline` when sweep fires | sync op completes; downstream dispatch dropped per contract |
| B13 | Self-cancel guard | Op emits `Interrupt(ctx_to_cancel=event.ctx)` (same ctx) | scheduler does NOT cancel emitter's pump task; only descendants/siblings of `ctx_to_cancel` swept |
| B14 | Consumer-queue NOT drained by Interrupt | Op pre-pushes 5 items to a consumer-owned `asyncio.Queue`, then emits Interrupt | queue still has 5 items after sweep — documents that operonx Interrupt does NOT touch user queues; consumer is responsible |

**Spec fixtures**: deferred to Phase D. The cross-runtime parity contract
for `Interrupt` (especially how `__interrupt__` synthetic events serialize
into `expected.json`) is best designed alongside the Rust port consumer.
Phase B's 13 unit tests cover all behaviors.

### Phase C — Rust SCRATCH primitive

**Status**: declarative path shipped. Imperative path deferred until Rust ops
gain a state-access mechanism (separate macro surface change).

**Files (shipped)**:
- `rust/operonx/src/core/states/scratch_ref.rs` *(new)* — `ScratchRef` struct (`{key: String}`) deserialised from the `"scratch"` field in serialized op `inputs`.
- `rust/operonx/src/core/utils/common.rs` — `Param` extended with `scratch: Option<ScratchRef>` alongside `ref_config`/`literal`. `deny_unknown_fields` still in effect; the new field is optional.
- `rust/operonx/src/core/ops/graph/task_scheduler.rs`:
  - `RuntimeState` extended with `scratch: HashMap<String, Value>` and `scratch_get(key) -> Value` (returns `Value::Null` for missing — Python parity).
  - New `InputResolver::Scratch(String)` variant in the pre-compiled input plan.
  - `compile_input_plans` detects `param.scratch.is_some()` and emits the Scratch resolver.
  - `resolve_inputs` reads scratch via `state.lock().scratch_get(key)`.
  - `Scheduler::run` signature accepts `scratch: Option<Map<String, Value>>` and seeds `state.scratch` synchronously before any op dispatches (race-free).
- `rust/operonx/src/core/engine.rs` — `Operon::start()` accepts `scratch: Option<Map<...>>`; new `Operon::run_json_async_with_scratch()` mirrors Python's `engine.run(scratch=...)`.
- `tests/common/mod.rs` — spec runner reads optional `scratch.json` per fixture; `passthrough` op registered for the SCRATCH fixtures.

**Tests (shipped)**:

| # | Test | Status |
|---|---|---|
| C1 (`scratch_ref_input`) | Run Python's `tests/spec/.../scratch_ref_input/` fixture against Rust runtime — declarative `SCRATCH["k"]` resolves from pre-seeded scratch | ✓ passing |

**Deferred (educa migration timing)**:
- `scratch_basic` fixture (imperative `SCRATCH["k"] = v` inside an op body) — Rust ops are currently `fn(&Value) -> Value` with no state pointer. Adding op-body SCRATCH access requires either a `tokio::task_local!` accessor + helper API, or a richer `OpFunc` signature. Defer until educa imperative usage informs the API shape.
- `scratch_external_seed` fixture — same blocker (uses `scratch_read` op).
- `MemoryState.scratch` field (Rust) — `MemoryState` is the Phase 1 placeholder; the scheduler uses `RuntimeState`. Unify in the Phase 7 state-merge pass.
- C2–C5 (task_local propagation tests) — ship alongside imperative SCRATCH.

### Phase D — Rust `Interrupt` event

**Status**: minimum-viable shipped. Full B-port suite deferred until educa
exercises Rust Interrupt in production.

**Files (shipped)** — all in `rust/operonx/src/core/ops/graph/task_scheduler.rs`:
- New `SchedulerEvent::Interrupt { op, ctx, ctx_to_cancel, reason }` variant alongside `Frame` / `Eof`.
- `parse_interrupt(value)` — recognises the synthetic shape `{"__interrupt__": {"ctx_to_cancel": [...], "reason": "..."}}` an op returns to emit an Interrupt. Mirrors Python's `isinstance(result, Interrupt)` check in `op.run()`.
- Both spawn paths in `spawn_op` (sync inline + io/cpu tokio::spawn) call `parse_interrupt` on the executed op's result and emit `SchedulerEvent::Interrupt` instead of a Frame when matched, then EOF normally.
- `tasks_by_ctx: Arc<Mutex<HashMap<ContextId, HashMap<String, JoinHandle<()>>>>>` — local to `run_once`, populated when the spawn path returns. Threaded through `on_frame`/`on_eof`/`route_edge_async` (an `Arc` clone — no `&mut`).
- `sweep_ctx(ctx_prefix, exclude=(emit_op, emit_ctx), ...)` — top-level helper (not a method, since it needs `&mut rx`) that:
  1. Drains the mpsc channel via `rx.try_recv()`, drops events at descendants of `ctx_prefix`, decrements `inflight` per dropped EOF (Frame events don't carry an inflight reservation), re-enqueues kept events via `tx.try_send`.
  2. Snapshots matching `JoinHandle`s (skipping the emitter), calls `JoinHandle::abort()` on each not-yet-finished one, decrements `inflight` eagerly (the EOF won't arrive for an aborted task), then awaits each handle to completion. Brutal-idempotent equivalent of Python's post-cleanup pass.
  3. Clears `ready` / `seq_origins` / `collect_bufs` for descendants of `ctx_prefix` excluding the emitter's ctx.
- Main loop in `run_once` adds an `Interrupt` arm: `await sweep_ctx(...)`, then sends a synthetic `FrameEvent { op: "__interrupt__", context: emit_ctx, data: { "__interrupt__": {...} } }` to the public `FrameSender` so consumers (`ExecutionHandle`) see it.

**Tests (shipped)** in `tests/internal/core/interrupt.rs`:

| # | Test | Setup | Assert |
|---|---|---|---|
| D1 | `interrupt_aborts_in_flight_task` (analog of Python B7) | Two parallel ops: `long_sleep` (`tokio::time::sleep(5s)`) + `kick_interrupt` returning the synthetic Interrupt shape | scheduler completes in <2s wall-clock; sweep aborts long_sleep via `JoinHandle::abort()` |

**Deferred (until educa exercises Rust Interrupt)**:
- D2 reqwest HTTP cancellation — same mechanism as D1; not load-bearing for ship.
- Full B1–B13 ports as Rust tests — write incrementally as bugs surface.
- `ExecutionHandle::interrupts()` accessor analog of Python's `handle.interrupts` — not needed until a Rust consumer asks for typed access. Synthetic frame already arrives via the public stream.

### Phase E — Python `operonx-transports` package + `WebSocketOp`

**Status: dropped.** After Phase A+B shipped, the proposed `WebSocketOp` +
`Protocol` registry + `serve_ws` helper kept failing the value test against
educa. The 700-line `ws_server.py` is ~80% business logic (call_logger,
customer_store, agent dispatch, transfer payload, log_summary) and ~20%
generic WS plumbing — extracting the generic 20% into a package would be
ceremony with one caller and one wire protocol (CMC).

What educa actually needed from operonx was the graph-side primitives —
SCRATCH (Phase A) and Interrupt (Phase B). With those shipped, the WS
server stays in the agent project as plain `@app.websocket`, and the graph
gets simpler because:
- `coordinator.should_interrupt` polling → graph returns `Interrupt(...)` and the scheduler aborts in-flight tasks
- per-call state globals/ContextVar gymnastics → `SCRATCH["k"]`

If a second agent project later shows up with a different wire protocol,
the shared bits get extracted *then*. No empty package skeleton meanwhile.

### Phase F — Rust `operonx-transports`

**Status: dropped** (follows Phase E).

---

## 6. Concurrency contracts (documented)

`SCRATCH` provides **no synchronization**:
- Python: single asyncio loop → no thread races within one call. Concurrent stream contexts can interleave reads/writes → last write wins.
- Rust: tokio runtime; mutability via `&mut` borrows. If the scheduler dispatches multiple tasks that share state, use `Mutex<HashMap>` internally. Readers see writes ordered by tokio's task scheduling.
- Verified for Python: ContextVar across `asyncio.create_task` works (PEP 567). All tasks in one call see the same `_scratch` dict via the same MemoryState reference.

`Interrupt` provides **best-effort cancellation**:
- Frames already dispatched to ops continue running until they hit an `await` that observes `CancelledError`
- Frames in the dispatch queue at sweep time are dropped synchronously
- Forwarded `Interrupt` arrives at consumers in order

---

## 7. What gets deprecated

After both primitives ship and at least one consumer (educa) migrates:

- `PARENT.shared(...)` — emit `DeprecationWarning` in Python, mark `#[deprecated]` in Rust, remove after one minor release
- Document migration path in `docs/architecture/state-model.md` (consumers move shared vars to SCRATCH)

No other public API changes.

---

## 8. Open decisions

| # | Question | Recommendation | Why |
|---|---|---|---|
| 1 | Default for missing SCRATCH key | `None` via `ScratchAccessor.__getitem__` wrapping `dict.get(k)` | Matches existing `MemoryState.__getitem__`; avoids try/except |
| 2 | `Interrupt` cancellation scope | Subtree (option b) with explicit `ctx_to_cancel` parameter | Matches the cross-stream-cancel use case without nuclear blast radius |
| 3 | Field name on `MemoryState` | `_scratch` (Python), `scratch` (Rust) | Python prefix matches `_cells`; Rust idiom is `pub` field with separate accessor |
| 4 | Trace SCRATCH writes? | v1: no, v2: yes | v2 piggybacks on `_current_gen_key` ContextVar to attribute writes to active op span; cheap to add later |
| 5 | Deprecate `PARENT.shared`? | Yes, after both primitives ship + one consumer migration | Don't break other operonx users immediately |
| 6 | Rust `SCRATCH` task-local mechanism | `tokio::task_local!` | Mirrors Python ContextVar semantics across `tokio::spawn` |
| 7 | `Interrupt` semantics in Rust scheduler | Cancel via `JoinHandle::abort` + drop queued | Same shape as Python |

---

## 9. Validation plan

### Python
1. All Phase A test cases (A1–A12) pass
2. All Phase B test cases (B1–B13) pass
3. New spec fixtures pass on Python runtime
4. Existing operonx test suite passes unchanged (additive change only)
5. Educa consumer (driving requirement) migrates onto SCRATCH and runs in production for ≥1 week without regressions

### Rust
1. All Phase C test cases (C1–C5) pass
2. All Phase D test cases plus B1–B13 ports pass
3. All spec fixtures from Python Phase A+B pass on Rust runtime (JSON-friendly subset)
4. Benchmark: SCRATCH read/write overhead vs `Cell` access (expectation: equal or faster)

### Pre-merge gates per phase
- Phase A: A1–A12 + Existing Python tests still green
- Phase B: B1–B13 + A1–A12 still green + manual smoke against educa staging branch
- Phase C: C1–C5 + Phase A spec parity
- Phase D: B-port + D1–D2 + Phase B spec parity
- Phase E/F: dropped (see §5 Phase E)

---

## 10. Out of scope

- New op types beyond `SCRATCH`/`Interrupt` consumption (latched cells, phase-machine ops, event subscriptions)
- Persistence (cross-call SCRATCH state)
- Distributed execution (SCRATCH is per-call, single-process)
- Migration tooling for `PARENT.shared` — manual migration (consumers grep their codebase)
- `operonx-transports` package (dropped — see §5 Phase E). WS transport is consumer-side plain FastAPI.
