# Scheduler Rewrite Plan (v2)

## Design Decisions

### Core model
- Everything is a stream. Every op emits `Frame`s followed by `EOF`.
- Normal op: 1 Frame + EOF. Generator op: N Frames + EOF.
- Scheduler has one code path — no `is_gen` check anywhere.
- Single asyncio.Queue, two event types, `inflight` counter for termination.

### Events
```python
@dataclass
class Frame:
    op:     str
    ctx:    tuple
    result: dict

@dataclass
class EOF:
    op:  str
    ctx: tuple
```

### pump_op — drives every op
```python
async def pump_op(op_name, ctx):
    try:
        async for sctx, result in graph._ops[op_name].run(state, ctx):
            queue.put_nowait(Frame(op_name, sctx, result))
            inflight += 1
        queue.put_nowait(EOF(op_name, ctx))
        inflight += 1
    finally:
        inflight -= 1  # cancel dispatch()'s reservation
```
`dispatch()` adds +1 to `inflight` as a task reservation. `_pump` adds +1 per Frame
and +1 for EOF. The `finally` -1 cancels the dispatch reservation so each dispatched
op contributes exactly (N_frames + 1_EOF) to inflight, consumed by the main loop.

### Scheduler loop
```python
while inflight > 0:
    event = await queue.get()
    inflight -= 1
    match event:
        case Frame: _on_frame(event)   # route to downstream
        case EOF:   _on_eof(event)     # flush collect, advance seq, loop check
```

### Loop in EOF handler — no run_loop function
```python
case EOF(graph_op, ctx):
    if graph_op._loop_config:
        outputs = graph_op.get_outputs(state, ctx)
        if not _eval_until(graph_op._loop_config, outputs):
            next_ctx = ctx + (f"loop_{_next_iter(graph_op, ctx)}",)
            for var, val in outputs.items():
                state[graph_op.full_name, var, next_ctx] = val
            dispatch(graph_op, next_ctx)
            return
    # no loop or condition met — propagate normally
```

### Stream-context ready counts — pre-computed at build time

When generator G emits Frame[0] in `stream_ctx`, downstream ops need accurate
ready counts. These are pre-computed in `_build()` as `_stream_initial_ready`:

```python
# _stream_initial_ready[gen_name][op_name] = initial ready count for op
# when gen starts streaming — accounts for batch preds already completed.
#
# If A→gen (hard edge), A must complete before gen starts → A is guaranteed
# done when gen emits Frame[0]. Subtract A's contribution from op's count.
```

This replaces the old `_stream_predecrements` (same idea, cleaner name) and
the runtime `batch_completed` scan (which was timing-dependent and broken
when batch ops complete concurrently with generator frames).

### Build: one _build() pass
`_build_adj()` + `_build_ready_counts()` + `_build_predecrements()` → single `_build()`.
`_get_stream_mode()` at runtime → `StateSchema._stream_policies` index at build time.

```python
@dataclass
class Edge:
    target:  str
    is_soft: bool
    # no policy — stream policy is var-level (on Ref), not edge-level
```

### Stream policy lives in StateSchema, not Edge
`collect` and `parallel` are **var-level** attributes on `Ref`, not edge-level.
One edge `gen → process` can have multiple vars with different policies:
```python
step = process(
    value=gen["value"].parallel(max=5),   # parallel
    scores=gen["score"].collect(),         # collect
)
```
Merging onto `Edge` would collapse that distinction — wrong.

Instead, `StateSchema` builds a `_stream_policies` index during `_build()`:
```python
# StateSchema.__slots__
"_stream_policies"  # Dict[Tuple[str, str], StreamPolicy]
                    # key: (dst_op_full_name, var_name)

# Built in StateSchema._build(), same pass as pull ref resolution:
if value._stream_collect or value._stream_parallel:
    op_name, var_name = key
    self._stream_policies[(op_name, var_name)] = StreamPolicy(
        collect=value._stream_collect,
        parallel=value._stream_parallel,
        parallel_max=value._stream_parallel_max,
    )
```

Scheduler queries O(1):
```python
policy = state.schema._stream_policies.get((dst_op, var_name))
# None → default seq behavior
```

### Remove `parent_context` — Cell walk handles it

`parent_context` was passed everywhere so `get_inputs()` could force PARENT refs
to read from a specific context. Cell's hierarchy walk makes this unnecessary:

```
state[op, var, ("main","loop_2","[0]")]
  → not found → ("main","loop_2") → found ✅
```

Cell walk is fast: max 2-4 dict lookups, cached after first read by `MemoryState.__getitem__`.

**Current code has a loop bug**: `parent_context` is fixed at `("main",)` for all
iterations, so loop iter 2+ reads stale initial value instead of the current loop state.
Cell walk fixes this because `run_loop` explicitly copies values to the new context
before dispatching each iteration.

Remove `parent_context` from:
- `BaseOp.run(state, context_id)` — 2 params only
- `BaseOp.get_inputs(state, context_id)` — remove PARENT ref special case, always use `context_id`
- `GraphOp.run(state, context_id)`
- `run_task_scheduler(graph, state, context_id, request_id)`
- `_Scheduler.__init__` and `_pump()`

`get_inputs()` simplifies to:
```python
def get_inputs(self, state, context_id):
    result = {}
    for var_name, param in self.inputs.items():
        value = state[self.full_name, var_name, context_id]  # Cell walk handles PARENT fallback
        if value is not None:
            result[var_name] = value
        elif param.value is not None and not isinstance(param.value, Ref):
            result[var_name] = param.value
        elif param.default is not None:
            result[var_name] = param.default
    return result
```

### LoopConfig simplified
```python
@dataclass
class LoopConfig:
    until:          str | Callable
    max_iterations: int = 1000
    # initial_state removed — handled by PARENT.shared()
```

---

## Status

| Step | File | Status |
|------|------|--------|
| 1 | `cell.py` | ✅ done — `is_shared` on Cell |
| 2a | `ref.py` | ✅ done — `StreamPolicy` dataclass + `_with_transform()` fix |
| 2b | `schema.py` | ✅ done — `_stream_policies` index populated in `_build()` |
| 3 | `state.py` | ✅ done — no `_shared_indices` checks |
| 4 | `base.py` | ✅ done — async-gen `run()`, `_on_yield` removed, `parent_context` removed |
| 5 | `graph_op.py` | ✅ done — `Link` NamedTuple, `_build()`, async-gen `run()`, slots/serialize cleanup, `_out_vars` added |
| 6 | `task_scheduler.py` | ✅ done — full rewrite: Frame/EOF model, `_Scheduler`, dispatch/pump/on_frame/route/on_eof, `output_queue` param |
| 7 | `func_op.py` | ✅ done — async-gen `run()`, `parent_context` removed |
| 8 | `branch_op.py` | ✅ done — `__branch_target__` injected in `_create_core_function()`, no `run()` override needed |
| 9 | `llm.py` | ✅ done — stream branch drives `_stream_core()` directly, yields per-chunk frames, `_output_queue` removed |
| 10 | `engine.py` | ✅ done — `ExecutionHandle`, `start()`, `run()` delegates to `start().collect()`, `stream()` deleted |
| 11 | `collector.py` | ✅ done — `_get_stream_contexts` state-driven, no stale GraphOp attribute |
| 12 | `_output_queue` wiring | ✅ done — replaced by `output_queue` param on `_Scheduler.run()` + `_out_vars` filter |
| 13 | output contract | ✅ done — `ExecutionHandle`: `async for` per frame, `collect()` merges, `await handle["op","var"]` for point queries |
| 14 | Tests | ⬜ `test_engine.py`, `test_execution_handle.py` — see below |

---

## Remaining Work

### Step 14 — Tests ⬜

**`test_engine.py`** — update existing tests:
- Replace `engine.stream()` calls with `engine.start()`
- Replace `async for _, r in graph.run(state)` assertions with `handle.collect()`

**`test_execution_handle.py`** — new file, cover:
- `async for op, ctx, data in handle` — frames arrive in order
- `await handle["op", "var"]` — waits correctly, last value wins for generators
- `handle.collect()` — merges all frames, returns final dict
- `handle.cancel()` — cancels both tasks
- Crash propagation — scheduler error surfaces through `__anext__` and `_await_output`
- `_out_vars` filtering — only PARENT-bound vars forwarded to queue

**`test_collector.py`** — verify `_get_stream_contexts` returns correct item contexts from state for generator ops.
  Replace with the new `item_ctxs` returned by the scheduler, or remove if tracing no longer needs it.

### Step 14 — Tests (238 failing after child_name fix)

Failures split into **real bugs** (fix production code) and **stale tests** (update test assertions).

---

#### 14-A: Real bugs — fix production code first

**14-A1 — `task_scheduler.py` `edge.is_soft` → `edge.soft` (fixes ~44 tests)**
- `Link` NamedTuple has field `soft: bool`, but scheduler `_on_frame()` references `edge.is_soft`
- File: `task_scheduler.py` — search and replace `edge.is_soft` → `edge.soft`
- Affected: `test_streaming_collect.py`, `test_streaming_regression.py`, `test_collector.py`, `test_concurrent.py`, `test_workflow.py`, `test_end_output_mapping.py`

**14-A2 — `BranchOp` `__branch_target__` not in state schema (fixes ~31 tests)**
- `BranchOp._create_core_function()` injects `__branch_target__` in its result dict
- But `__branch_target__` is not registered in `BranchOp.outputs` → `store_result()` raises `KeyError`
- Fix: add `"__branch_target__": Param(type=str, required=False)` to BranchOp outputs schema
- Affected: all of `test_branch_op.py` (31 tests)

**14-A3 — Tests calling `graph.run()` directly with `await` (fixes ~131 tests)**
- `GraphOp.run()` is now an async generator — cannot be `await`ed
- Many tests do `result = await graph.run(state)` or call via engine internals expecting coroutine
- Fix: update all direct `graph.run()` call sites in tests to `async for _, r in graph.run(state): result = r`
  OR switch to `await engine.run(inputs)` which internally uses `start().collect()`
- Affected: `test_graph_op.py`, `test_graph_loop.py`, `test_branch_op.py`, `test_streaming.py`,
  `test_shared_vars.py`, `test_parser_op.py`, all iteration tests

---

#### 14-B: Stale test assertions — update to match new API

| File | Lines | Old | New |
|------|-------|-----|-----|
| `test_graph_op.py` | 872–1174 | `graph.initial_ready_count` | `graph._initial_ready` |
| `test_graph_op.py` | 875, 942, 1030, 1116–1117 | `graph.has_soft_preds` | removed — delete assertions |
| `test_streaming.py` | 76, 86, 110–111, 136–137 | `graph._stream_predecrements` | `graph._stream_initial_ready` |
| `test_serialize.py` | 150 | `"initial_ready_count" in s` | keep (key still exists in serialize()) |
| `test_serialize.py` | 192–199 | `"has_soft_preds" in s` | delete test — key removed |
| `test_streaming_regression.py` | 24–47 | `_on_yield in BaseOp.__slots__` | delete both tests — slot removed intentionally |
| `test_engine_stream.py` | 28, 59, 91, 121, 154 | `engine.stream(inputs)` | `engine.start(inputs)` + adapt iteration |
| `test_middleware.py` | 191 | `engine.stream(inputs)` | `engine.start(inputs)` + adapt iteration |
| `test_graph_loop.py` | 33, 62–64, 96, 161, 188, 259 | `result["_loop_metrics"]` | removed — delete or rewrite assertions |
| `test_concurrent.py` | 210 | `result["_loop_metrics"]` | removed — delete assertion |

---

#### 14-C: Tracer failures — investigate after 14-A/14-B

`test_local_tracer.py` (3 failures) — likely downstream of 14-A bugs. Re-evaluate after fixing 14-A.

---

#### Rust (deferred)

| File | Lines | Change |
|------|-------|--------|
| `rust/hush-icore/src/config.rs` | 182–251 | parse `stream_initial_ready`; handle missing `has_soft_preds` |

---

## Step 4 — `base.py`

### Remove
- `"_on_yield"` from `__slots__`
- `self._on_yield = None` from `__init__`

### Keep `contain_generation`
Do NOT remove `contain_generation` from `__slots__`. It is **static tracing metadata**
(set once in `__init__`, e.g. `LLMOp` sets it `True`) used by `collector.py` to classify
trace nodes as "generation" vs "span". It has nothing to do with the scheduler refactor.
Removing it would silently break LLM trace classification in `collector.py` lines 289–300.

### `is_gen` — local only, not stored
`is_gen` is a **local variable** inside `run_stream()`, not a stored attribute or scheduler concept:
```python
is_gen = inspect.isasyncgenfunction(core_fn) or inspect.isgeneratorfunction(core_fn)
```
The scheduler sees only `Frame` and `EOF` — it never knows or cares whether an op is a generator.

### Change `run()` to async generator

`run()` becomes the async generator. Normal op yields once. Generator op yields N times.
No `parent_context` parameter — Cell walk handles PARENT ref fallback.

```python
async def run(self, state, context_id):
    # ... delay, get_inputs(state, context_id), cache check ...

    # Cache hit: single Frame
    if _cache_hit:
        yield context_id, _outputs
        return

    is_gen = inspect.isasyncgenfunction(core_fn) or inspect.isgeneratorfunction(core_fn)

    if is_gen:
        base_ctx = context_id if context_id is not None else ("main",)
        idx = 0
        if inspect.isasyncgenfunction(core_fn):
            async for result in core_fn(**_inputs):
                sctx = base_ctx + (f"[{idx}]",)
                self.store_result(state, result, sctx)
                yield sctx, result
                idx += 1
        else:
            for result in core_fn(**_inputs):
                sctx = base_ctx + (f"[{idx}]",)
                self.store_result(state, result, sctx)
                yield sctx, result
                idx += 1
    else:
        result = await self._exec_core(_inputs)
        self.store_result(state, result, context_id)
        yield context_id, result

    # finally block: _store_metrics, _log, error — unchanged, fires on exhaustion
```

---

## Step 5 — `graph_op.py`

### Edge NamedTuple — add near top of file (before class)

```python
from typing import NamedTuple

class Edge(NamedTuple):
    dst: str
    soft: bool
```

Used by `_build()` and referenced by the scheduler in Step 6 as `edge.dst`, `edge.soft`.

### Imports — remove stale task_scheduler imports

```python
# OLD
from hush.core.ops.graph.task_scheduler import (
    LoopConfig,
    WorkflowScheduler,
    _is_gen,
    get_current_scheduler,
    run_loop,
    run_task_scheduler,
)

# NEW — only keep what graph_op.py actually uses
from hush.core.ops.graph.task_scheduler import (
    LoopConfig,
    run_task_scheduler,
)
```

Removed: `WorkflowScheduler`, `_is_gen`, `get_current_scheduler`, `run_loop`.
Loop execution moves into the scheduler's EOF handler (Step 6). `run_loop` has only one
caller (graph_op.py:501) — safe to remove once loop logic is in scheduler.

### __slots__ — remove 6, add 3

```python
# Remove these 6
"initial_ready_count"
"has_soft_preds"
"_compiled_adj"
"_stream_predecrements"
"stream_contexts"
"_has_streaming"

# Add these 3
"_adj"              # replaces _compiled_adj
"_initial_ready"    # replaces initial_ready_count
"_stream_initial_ready"
```

### __init__ — remove 5 old inits, add 3 new

```python
# Remove these 5 lines
self.has_soft_preds = set()
self._stream_predecrements = {}
self._compiled_adj = {}
self.stream_contexts = []
self._has_streaming = False

# Add these 3 lines (after self._loop_config = None)
self._adj = {}
self._initial_ready = {}
self._stream_initial_ready = {}
```

### build() — replace three methods with one, remove _has_streaming line

```python
def build(self):
    for child in self._ops.values():
        if hasattr(child, "build"):
            child.build()

    self._setup_schema()
    self._setup_endpoints()

    result = self.validate()
    result.raise_if_errors()

    self._build()   # ← replaces _build_ready_counts() + _build_adj() + _build_predecrements()

    if self._loop_config and isinstance(self._loop_config.until, str):
        self._loop_config._compiled_until = compile(self._loop_config.until, "<until>", "eval")

    self._is_building = False
    self._cache_full_names()
    # NOTE: _has_streaming line removed — scheduler uses op.is_gen directly (set in Step 4)
```

Note: `_build_ready_counts()` was called BEFORE `validate()` in the old code. In the
new flow, `_build()` runs AFTER `validate()`. This is correct — validation only needs
`prevs`/`nexts`/`entries`/`exits`, not ready counts.

### _build() — delete 3 old methods, add new one

Delete `_build_ready_counts()`, `_build_adj()`, `_build_predecrements()` entirely.

Add `_build()` in their place:

```python
def _build(self):
    """Single pass: adjacency list + initial ready counts + stream-context ready counts."""
    adj      = {name: [] for name in self._ops}
    ready    = {name: 0  for name in self._ops}
    has_soft: Dict[str, bool] = {}

    for (src, dst), edge in self._edges.items():
        adj[src].append(Edge(dst, edge.soft))
        if edge.soft:
            if not has_soft.get(dst):
                ready[dst] += 1
                has_soft[dst] = True
        else:
            ready[dst] += 1

    self._adj           = adj
    self._initial_ready = ready

    # Pre-compute stream-context initial ready counts per generator op.
    # When generator G emits Frame[0] in stream_ctx, its upstream batch ops
    # (direct hard-edge predecessors of G) are guaranteed already completed
    # — G cannot start until they finish. Subtract their contributions from
    # downstream ops' ready counts so stream_ctx starts with correct counts.
    #
    # Conservative: only subtract DIRECT predecessors of G (via hard edge).
    stream_initial = {}
    for gen_name in self._ops:
        gen_preds = {
            src for (src, dst) in self._edges
            if dst == gen_name and not self._edges[(src, dst)].soft
        }
        ri = {}
        for op_name, base_count in ready.items():
            r = base_count
            for pred in self.prevs.get(op_name, []):
                if pred == gen_name:
                    continue  # gen contributes via per-frame decrements
                edge = self._edges.get((pred, op_name))
                if edge and not edge.soft and pred in gen_preds:
                    r = max(0, r - 1)
            ri[op_name] = r
        stream_initial[gen_name] = ri
    self._stream_initial_ready = stream_initial  # Dict[str, Dict[str, int]]
```

No `_get_policy()` — stream policy resolved from `state.schema._stream_policies` at runtime (O(1), prebuilt by StateSchema).

### loop() classmethod — remove initial_state from LoopConfig

`LoopConfig.initial_state` field is removed in Step 6. Remove it from the constructor call:

```python
# OLD
g._loop_config = LoopConfig(
    until=until,
    max_iterations=max_iterations,
    initial_state=initial_state,
)

# NEW — initial_state already forwarded to GraphOp as inputs= (line above this)
g._loop_config = LoopConfig(
    until=until,
    max_iterations=max_iterations,
)
```

`initial_state` values are already passed to `cls(name=name, inputs=initial_state or None)` on
the line above — they become graph PARENT inputs, not LoopConfig state. The LoopConfig only
needs the stop condition.

### run() — async generator, no parent_context, no run_loop

```python
async def run(self, state: "MemoryState", context_id=None):
    """Execute graph: get inputs → schedule → yield (ctx, outputs) per frame."""

    if context_id is None:
        context_id = DEFAULT_CONTEXT

    request_id = state.request_id
    start_time = datetime.now(timezone.utc)
    perf_start = perf_counter()
    _inputs = {}
    _outputs = {}
    error_msg = None

    try:
        _inputs = self.get_inputs(state, context_id=context_id)

        if self._is_building:
            self.build()

        _outputs, stream_ctxs = await run_task_scheduler(self, state, context_id, request_id)
        # Loop iteration is handled inside run_task_scheduler via scheduler EOF handler (Step 6).
        # No run_loop() call here.

        if not stream_ctxs:
            # Batch mode: single result at context_id
            self.store_result(state, _outputs, context_id)
            yield context_id, _outputs
        else:
            # Streaming mode: one yield per stream context
            for sctx in stream_ctxs:
                item = self.get_outputs(state, context_id=sctx)
                yield sctx, item

    except Exception:
        import sys
        error_msg = (
            traceback.format_exc()
            if LOGGER.isEnabledFor(40)
            else f"{type(sys.exc_info()[1]).__name__}: {sys.exc_info()[1]}"
        )
        LOGGER.error(
            "[title]\\[%s][/title] Error in op [highlight]%s[/highlight]:\n%s",
            request_id, self.name, error_msg.rstrip(),
        )

    finally:
        end_time = datetime.now(timezone.utc)
        duration_ms = (perf_counter() - perf_start) * 1000
        self._log(request_id, context_id, _inputs, _outputs, duration_ms)
        self._store_metrics(
            state, context_id,
            start_time=start_time, end_time=end_time, duration_ms=duration_ms,
        )
        if error_msg is not None:
            state[self.full_name, "error", context_id] = error_msg
        # NOTE: no `return _outputs` — illegal in async generator
```

Key changes vs old `run()`:
- `parent_context` parameter removed
- `get_inputs()` call: remove `parent_context=parent_context`
- `run_task_scheduler()`: 4 args `(self, state, context_id, request_id)` — `parent_context` removed
- `self.stream_contexts = stream_ctxs` line removed (slot deleted)
- `run_loop()` call removed — loop handled in scheduler EOF handler (Step 6)
- `_loop_metrics` pop/re-add block removed — scheduler sets it directly on state (Step 6)
- `return _outputs` in `finally` removed — illegal in async generator

### serialize() — update Python attr refs, keep JSON keys for Rust compat

**IMPORTANT:** The Rust backend (`rust/hush-icore/src/config.rs`) parses these JSON keys by
exact name. Keep JSON output keys unchanged; only update the Python attribute references.
`has_soft_preds` is deleted entirely (Rust config.rs needs updating when Rust is rewritten).
`stream_predecrements` replaced by `stream_initial_ready` (different format — Rust Step TBD).

```python
def serialize(self) -> dict:
    base = super().serialize()
    base.update(
        {
            "ops": {name: op.serialize() for name, op in self._ops.items()},
            "edges": [
                {"from": src, "to": dst, "soft": edge.soft}
                for (src, dst), edge in self._edges.items()
            ],
            "entries": list(self.entries),
            "exits": list(self.exits),
            "initial_ready_count": dict(self._initial_ready),   # ← attr renamed, JSON key kept
            # "has_soft_preds" removed — field deleted, no longer computed
            "compiled_adj": {                                    # ← JSON key kept for Rust compat
                op: [[e.dst, e.soft] for e in edges]            # ← Edge namedtuple fields
                for op, edges in self._adj.items()
            },
            "stream_initial_ready": self._stream_initial_ready, # ← replaces stream_predecrements (new format)
            "loop_config": {
                "until": self._loop_config.until
                if isinstance(self._loop_config.until, str)
                else None,
                "max_iterations": self._loop_config.max_iterations,
                # "loop_vars" removed — Rust parses but never uses at runtime
            }
            if self._loop_config
            else None,
            "max_stream_concurrent": self.concurrency,
        }
    )
    return base
```

### show() — rename field ref

```python
LOGGER.debug("%sReady count: %s", prefix, dict(self._initial_ready))
#                                                  ↑ was initial_ready_count
```

### Cascading changes to other files (NOT part of Step 5 — tracked in their own steps)

These are discovered by agents and must be fixed in the steps listed:

| File | Line | Change needed | Step |
|------|------|---------------|------|
| `task_scheduler.py` | 189, 332, 465 | `graph.initial_ready_count` → `graph._initial_ready` | 6 |
| `task_scheduler.py` | 211, 476 | `graph._compiled_adj` → `graph._adj` | 6 |
| `task_scheduler.py` | 333, 466 | `graph._stream_predecrements` → `graph._stream_initial_ready` (new format) | 6 |
| `task_scheduler.py` | 239 | remove `getattr(op_obj, '_has_streaming', False)` check | 6 |
| `task_scheduler.py` | 247 | `await op_obj.run(state, task.context_id, parent_context)` → remove parent_context | 6 |
| `func_op.py` | 428, 433 | remove parent_context from run() and get_inputs() calls | 7 |
| `engine.py` | 258, 345 | update for async-gen run() | 10 |
| `collector.py` | 199 | `getattr(parent_op, "stream_contexts", [])` — stream_contexts no longer on GraphOp | 10 |
| `test_graph_op.py` | 872–1174 | `graph.initial_ready_count` → `graph._initial_ready`; delete `has_soft_preds` asserts | tests |
| `test_streaming.py` | 76, 86, 110–111, 136–137 | `_stream_predecrements` → `_stream_initial_ready` (verify expected values) | tests |
| `test_serialize.py` | 150, 151, 199 | update key assertions | tests |
| `debug_predecrements.py` | 31–32 | update attr names | tests |
| `rust/hush-icore/src/config.rs` | 182–251 | parse `stream_initial_ready` key; handle missing `has_soft_preds` | Rust step |

### LoopConfig — remove initial_state (in task_scheduler.py, Step 6)
```python
@dataclass
class LoopConfig:
    until:           Any   # str expression or callable
    max_iterations:  int   = 1000
    _compiled_until: Any   = field(default=None, repr=False)
    # initial_state removed — initial values live as graph inputs, not here
```

---

## Step 6 — `task_scheduler.py` (full rewrite)

### Remove entirely
- `WorkflowScheduler` class (current form)
- `run_loop()` function — loop logic moves to EOF handler
- `_is_gen()` — no longer needed, pump_op drives everything uniformly
- `_get_stream_mode()` — moved to build time
- `YieldEvent`, `Task` dataclasses — replaced by `Frame`, `EOF`
- `_current_scheduler` ContextVar — no longer needed (scheduler is local)
- `get_current_scheduler()` — no longer needed

### Keep
- `LoopConfig` dataclass (simplified)
- `_evaluate_until()` helper
- `run_task_scheduler()` as the public entry point (backward compat signature)

### New structure

```python
@dataclass
class Frame:
    op: str; ctx: tuple; result: dict

@dataclass
class EOF:
    op: str; ctx: tuple

@dataclass
class StreamPolicy:
    collect:      bool = False
    parallel:     bool = False
    parallel_max: int  = 0   # 0 = unlimited

@dataclass
class Edge:
    target:  str
    is_soft: bool
    # no policy here — stream policy is var-level, lives in StateSchema._stream_policies

@dataclass
class LoopConfig:
    until: str | Callable
    max_iterations: int = 1000
    _compiled_until: Any = field(default=None, repr=False)


async def run_task_scheduler(graph, state, context_id, request_id):
    """Public entry point. Returns (outputs_dict, stream_ctxs)."""
    return await _Scheduler(graph, state, context_id).run()


class _Scheduler:
    def __init__(self, graph, state, context_id):
        self.graph          = graph
        self.state          = state
        self.ctx            = context_id
        self.queue          = asyncio.Queue()
        self.inflight       = 0
        self.initial_ready  = dict(graph._initial_ready)
        self.ready          = {context_id: dict(graph._initial_ready)}
        # batch_completed removed — replaced by _stream_initial_ready pre-computed table
        self.seq_queues     = {}              # (src,dst) → deque[ctx]
        self.seq_active     = {}             # (src,dst) → bool
        self.seq_origins    = {}             # (op,ctx)  → (src,dst) — for EOF advance
        self.collect_bufs   = {}             # (src,dst) → [(ctx, result)]
        self.stream_ctxs    = []
        self.loop_iters     = {}             # graph_full_name → current iteration number

    def dispatch(self, op_name, ctx):
        self.inflight += 1
        asyncio.create_task(self._pump(op_name, ctx))

    async def _pump(self, op_name, ctx):
        op = self.graph._ops[op_name]
        try:
            async for sctx, result in op.run(self.state, ctx):
                self.queue.put_nowait(Frame(op_name, sctx, result))
                self.inflight += 1
            self.queue.put_nowait(EOF(op_name, ctx))
            self.inflight += 1
        finally:
            self.inflight -= 1  # cancel dispatch()'s reservation (task complete)

    async def run(self):
        for entry in self.graph.entries:
            self.dispatch(entry, self.ctx)

        while self.inflight > 0:
            event = await self.queue.get()
            self.inflight -= 1
            match event:
                case Frame(): self._on_frame(event)
                case EOF():   self._on_eof(event)

        # Return both outputs (batch ctx) and stream_ctxs so GraphOp.run()
        # can yield per stream_ctx in streaming mode.
        # NOTE: in streaming mode, outputs at self.ctx may be empty because
        # ops completed at stream_ctxs. GraphOp.run() uses stream_ctxs to
        # call get_outputs(state, sctx) for each and yield them separately.
        outputs = self.graph.get_outputs(self.state, context_id=self.ctx)
        return outputs, self.stream_ctxs

    def _on_frame(self, event: Frame):
        # Init stream context ready counts using pre-computed table
        if event.ctx not in self.ready:
            self.stream_ctxs.append(event.ctx)
            # Use pre-computed counts for this generator (batch preds already subtracted)
            ri = self.graph._stream_initial_ready.get(event.op, self.initial_ready)
            self.ready[event.ctx] = dict(ri)

        # Propagate Frame through adj
        for edge in self.graph._adj.get(event.op, []):
            rc = self.ready[event.ctx]
            if edge.target not in rc:
                continue
            if edge.is_soft and rc[edge.target] <= 0:
                continue
            rc[edge.target] -= 1
            if rc[edge.target] == 0:
                self._route(event.op, edge.target, event.ctx, event.result)

    def _on_eof(self, event: EOF):
        # Flush collect buffers sourced from this op
        for (src, dst), buf in list(self.collect_bufs.items()):
            if src == event.op:
                collected = {}
                for _, r in buf:
                    for k, v in r.items():
                        collected.setdefault(k, []).append(v)
                collect_ctx = event.ctx + ("__collect__",)
                self.stream_ctxs.append(collect_ctx)
                self.graph._ops[src].store_result(self.state, collected, collect_ctx)
                self.dispatch(dst, collect_ctx)
                del self.collect_bufs[(src, dst)]

        # Advance sequential queue
        origin = self.seq_origins.pop((event.op, event.ctx), None)
        if origin:
            key = origin
            if self.seq_queues.get(key):
                next_ctx = self.seq_queues[key].popleft()
                self.seq_origins[(key[1], next_ctx)] = key
                self.dispatch(key[1], next_ctx)
            else:
                self.seq_active[key] = False

        # Loop check
        op_obj = self.graph._ops.get(event.op)
        if op_obj and isinstance(op_obj, GraphOp) and op_obj._loop_config:
            outputs = op_obj.get_outputs(self.state, event.ctx)
            cfg = op_obj._loop_config
            n = self.loop_iters.get(op_obj.full_name, 0)
            if not _evaluate_until(cfg, outputs) and n < cfg.max_iterations:
                self.loop_iters[op_obj.full_name] = n + 1
                # iter 0: ctx=("main",) → next=("main","loop_1")  [append]
                # iter N: ctx=("main","loop_N") → next=("main","loop_N+1") [replace last]
                if n == 0:
                    next_ctx = event.ctx + (f"loop_1",)
                else:
                    next_ctx = event.ctx[:-1] + (f"loop_{n+1}",)
                for var, val in outputs.items():
                    self.state[op_obj.full_name, var, next_ctx] = val
                self.dispatch(event.op, next_ctx)
                return
            # condition met — store loop metrics and fall through
            # Note: old run_loop() stored _loop_metrics dict. If tracer consumes it,
            # store here: self.state[op_obj.full_name, "_loop_metrics", event.ctx] = {"iterations": n}
            # Otherwise can be dropped.

    def _route(self, src, dst, ctx, result: dict):
        # Resolve per-var stream policy from schema (O(1))
        # Find which input var of dst references src, then look up its policy
        dst_op = self.graph._ops[dst]
        policy = None
        for var_name, param in dst_op.inputs.items():
            ref = getattr(param, "value", None)
            if isinstance(ref, Ref) and getattr(ref.raw_source, "name", None) == src:
                policy = self.state.schema._stream_policies.get((dst_op.full_name, var_name))
                break

        if policy and policy.collect:
            self.collect_bufs.setdefault((src, dst), []).append((ctx, result))
        elif policy and policy.parallel:
            # TODO: respect policy.parallel_max via semaphore
            self.dispatch(dst, ctx)
        else:  # seq (default)
            key = (src, dst)
            if not self.seq_active.get(key):
                self.seq_active[key] = True
                self.seq_origins[(dst, ctx)] = key
                self.dispatch(dst, ctx)
            else:
                self.seq_queues.setdefault(key, deque()).append(ctx)
```

---

## Open questions / challenges

### 1. ~~`run()` vs `run_stream()`~~ — resolved
- `run()` → async generator, yields `(ctx, result)` — used by scheduler `_pump`
- No separate `run_stream()` needed — one method, one concept
- `engine.py` updated to drive the generator: `async for _, r in graph.run(state): result = r`
- Applies to both `BaseOp` and `GraphOp`

### 2. Ready count init for stream context
The approach above (start from `initial_ready`, subtract `batch_completed` preds)
assumes soft-edge grouping is preserved. Needs careful testing for graphs with
mixed hard+soft edges and generators.

### 3. Multi-source collect — intentionally per-source

When two generators feed the same collector (`genA → col`, `genB → col`), each
flushes independently on its own EOF and dispatches `col` twice (different
`collect_ctx` per source). This is **intentional**: each source produces an
independent collection. If you need to merge across sources, use a downstream
merge op. The plan does not support "wait for all sources" collect — that requires
a barrier, which is out of scope.

### 4. Exception handling in `_pump`

If `op.run()` raises inside `_pump`, `finally: inflight -= 1` fires but EOF is
never queued. Downstream ops waiting on that EOF (seq advance, collect flush)
will never trigger. The plan defers full error propagation to a later phase —
for now, `_pump` should catch exceptions, log the error to state, and then
explicitly enqueue EOF so the scheduler can drain cleanly:
```python
except Exception as e:
    self.state[op_name, "error", ctx] = str(e)
    self.queue.put_nowait(EOF(op_name, ctx))
    self.inflight += 1
```

### 6. Parallel max
`StreamPolicy("par", max_parallel=4)` — need a semaphore per `(src, dst)` pair
to cap concurrent dispatches. Straightforward but not yet in the sketch above.

### 7. engine.stream() and _output_queue — resolved
`engine.stream()` sets `_output_queue` ContextVar then wraps `graph.run()` in a coroutine
(since `asyncio.create_task` needs a coroutine, not a gen — see Step 5).
`_on_frame` still enqueues to `_output_queue` when set:
```python
if _output_queue.get():
    _output_queue.get().put_nowait({"type": "token", "op": event.op, "data": event.result})
```

### 8. Branch ops — resolved
BranchOp does NOT override `run()` currently. Add an override in `branch_op.py`
that wraps `super().run()` and injects `"__branch_target__"` into the yielded result:
```python
# branch_op.py — add run() override
async def run(self, state, context_id):
    async for sctx, result in super().run(state, context_id):
        target = result.get("target")          # BranchOp stores selected branch in "target"
        yield sctx, {"__branch_target__": target, **result}
```
`get_target()` is superseded by this — remove it or keep as internal helper.

`_on_frame` checks for this key — if present, only routes to that one target, skips all others:
```python
branch_target = event.result.get("__branch_target__")
for edge in self.graph._adj.get(event.op, []):
    if branch_target and edge.target != branch_target:
        continue   # skip non-selected branch edges
    ...  # normal ready-count decrement + route
```
BranchOp logic stays inside the op. Scheduler stays generic.

---

---

## Step 2a — `ref.py`

### Add `StreamPolicy` dataclass

Add near top of `ref.py` (requires only `from dataclasses import dataclass` — stdlib, no hush deps):

```python
@dataclass
class StreamPolicy:
    collect:      bool = False
    parallel:     bool = False
    parallel_max: int  = 0   # 0 = unlimited
```

Both `schema.py` and `task_scheduler.py` import `StreamPolicy` from `ref.py`.
This avoids circular import: `states/` cannot import from `ops/graph/`.

### Fix `_with_transform()` — stream policy drop bug (pre-existing)

`_with_transform()` creates a new `Ref` without copying stream policy attributes.
Any chained transform after `.parallel()` / `.collect()` silently loses the policy:

```python
gen["x"].parallel(max=5)["key"]  # ← ["key"] calls _with_transform(), policy lost
```

`_clone()` already does this correctly. Apply the same pattern to `_with_transform()`:

```python
def _with_transform(self, op: str, *args: Any) -> "Ref":
    new_transforms = self._transforms + [(op, args)]
    new_fn = self._wrap(self._fn, op, args)
    new_ref = Ref(self._source, self.var, new_transforms, new_fn)
    # Preserve stream policy (same as _clone())
    object.__setattr__(new_ref, "_stream_parallel",     self._stream_parallel)
    object.__setattr__(new_ref, "_stream_parallel_max", self._stream_parallel_max)
    object.__setattr__(new_ref, "_stream_collect",      self._stream_collect)
    return new_ref
```

---

## Step 2b — `schema.py` (addendum)

### Add `_stream_policies` to `StateSchema`

Add to `__slots__`:
```python
"_stream_policies"   # Dict[Tuple[str, str], StreamPolicy]
```

Initialize in `__init__`:
```python
self._stream_policies: Dict[Tuple[str, str], StreamPolicy] = {}
```

Extend `_build()` — same pass as pull ref resolution, after setting `self._pull_refs[idx]`:
```python
# Capture stream policy while resolving pull ref
if value._stream_collect or value._stream_parallel:
    op_name, var_name = key
    self._stream_policies[(op_name, var_name)] = StreamPolicy(
        collect=value._stream_collect,
        parallel=value._stream_parallel,
        parallel_max=value._stream_parallel_max,
    )
```

`StreamPolicy` defined in `ref.py` (no hush deps — avoids circular import since `schema.py` is in `states/` and cannot import from `ops/graph/`). Both `schema.py` and `task_scheduler.py` import `StreamPolicy` from `ref.py`.

### Multi-policy conflict — build-time error

If downstream has two inputs from the same src with **different** stream policies:
```python
step = process(
    value=gen["value"].parallel(max=5),  # parallel
    scores=gen["score"].collect(),        # collect  ← conflict!
)
```
Raise `ValueError` in `StateSchema._build()` when two vars from the same `(src_op, dst_op)` pair have conflicting policies. Clean and explicit — user must pick one.

---

## Files to update (additions found by audit)

In addition to `base.py`, `graph_op.py`, `task_scheduler.py`, `schema.py`:

- **`ref.py`** — Add `StreamPolicy` dataclass + fix `_with_transform()` (see Step 2a)
- **`engine.py`** — Two call sites for async-gen `run()` (see Step 5)
- **`func_op.py`** — `FuncOp.run()` overrides `BaseOp.run()` with `parent_context` param;
  convert to async generator and remove `parent_context`
- **`hush-providers/ops/llm.py`** — `LLMOp.run()` overrides `BaseOp.run()` with
  `parent_context` param; convert to async generator and remove `parent_context`
- **`flow/branch_op.py`** — Add `run()` override to inject `__branch_target__` (see Open Q #5)

## Files truly untouched
`cell.py` (done), `state.py` (done), `validation.py`, `_decorators.py`, `parser_op.py`

## Tests to update after refactor

Hard failures (AttributeError / AssertionError) — update these after Step 4–6:
- `test_streaming_regression.py` — 2 tests check `"_on_yield" in BaseOp.__slots__` → **delete**
- `test_streaming.py::TestStreamPredecrements` — 4 tests access `g._stream_predecrements`
  → update to check `g._stream_initial_ready`
- `test_serialize.py::TestGraphOpSerialize` — 2 tests assert old field names
  → update to `_initial_ready`, `_adj`
- `test_graph_op.py` — 7 assertions on `graph.initial_ready_count[...]`
  → update to `graph._initial_ready[...]`

New tests to add for coverage gaps:
- Loop state cell walk: iter 2+ reads updated state, not stale initial value
- `_stream_initial_ready` index built correctly per generator op
- Loop context naming: `("main",) → ("main","loop_1") → ("main","loop_2")`