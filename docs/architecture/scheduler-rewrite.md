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
| 2a | `ref.py` | ⬜ `StreamPolicy` dataclass + `_with_transform()` fix |
| 2b | `schema.py` | ✅ done (is_shared) + ⬜ add `_stream_policies` index |
| 3 | `state.py` | ✅ done — no `_shared_indices` checks |
| 4 | `base.py` | ⬜ next — keep `contain_generation`, remove `_on_yield` |
| 5 | `graph_op.py` | ⬜ — add `_stream_initial_ready`, update `serialize()` |
| 6 | `task_scheduler.py` | ⬜ full rewrite |
| 7 | `func_op.py` | ⬜ async-gen `run()`, remove `parent_context` |
| 8 | `hush-providers/llm.py` | ⬜ async-gen `run()`, remove `parent_context` |
| 9 | `branch_op.py` | ⬜ add `run()` override for `__branch_target__` |
| 10 | `engine.py` | ⬜ two call sites for async-gen `run()` |

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

### __slots__ — remove, rename
- Remove: `initial_ready_count`, `has_soft_preds`, `_compiled_adj`,
  `_stream_predecrements`, `stream_contexts`, `_has_streaming`
- Add: `_adj` (replaces `_compiled_adj`), `_stream_initial_ready`

### build() — replace three methods with one

Remove: `_build_ready_counts()`, `_build_adj()`, `_build_predecrements()`
Add: `_build()`

```python
def _build(self):
    adj      = {name: [] for name in self._ops}
    ready    = {name: 0  for name in self._ops}
    has_soft = {}

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
    # A more precise approach would use transitive ancestors, but direct preds
    # covers the common case: linear chains like A → G → B.
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
                    # pred is a hard-edge predecessor of gen → guaranteed
                    # completed before gen starts streaming
                    r = max(0, r - 1)
            ri[op_name] = r
        stream_initial[gen_name] = ri
    self._stream_initial_ready = stream_initial  # Dict[str, Dict[str, int]]
```

No `_get_policy()` — stream policy is resolved from `state.schema._stream_policies` by the scheduler at runtime (O(1) index, prebuilt by StateSchema).

### serialize() — update field names

`serialize()` references old slot names. Update after renaming:
- `initial_ready_count` → `_initial_ready`
- `compiled_adj` → `_adj`
- `_loop_config.initial_state` → remove (use `_shared_indices` for loop var names)

### run() — async generator

`GraphOp.run()` also becomes an async generator so `pump_op` can drive it uniformly.
`run_task_scheduler` now returns `(outputs, stream_ctxs)`.

```python
async def run(self, state, context_id):
    outputs, stream_ctxs = await run_task_scheduler(self, state, context_id, ...)

    if not stream_ctxs:
        # Batch mode: all ops completed at context_id, outputs are there
        self.store_result(state, outputs, context_id)
        yield context_id, outputs
    else:
        # Streaming mode: ops completed at stream_ctxs, yield one Frame per ctx
        for sctx in stream_ctxs:
            item = self.get_outputs(state, context_id=sctx)
            yield sctx, item
```

This ensures the outer `_pump` receives the correct number of Frames regardless
of whether the inner graph ran in batch or streaming mode.

### engine.py — update call sites

`run()` is now an async generator. Two call sites in `engine.py` need updating:

```python
# engine.run() — line 258
result = {}
async for _, r in self.graph.run(state):
    result = r

# engine.stream() — line 345, asyncio.create_task() needs a coroutine, not a gen
async def _run_and_collect():
    result = {}
    async for _, r in self.graph.run(state):
        result = r
    return result
task = asyncio.create_task(_run_and_collect())
# _output_queue ContextVar is set before this, so _on_frame can still enqueue frames ✓
```

### BaseOp.__call__ — update for async generator

```python
# __call__ quick-test path
async def _collect():
    result = {}
    async for _, r in self.run(state):
        result = r
    return result
# replace asyncio.run(self.run(state)) with:
try:
    asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = pool.submit(asyncio.run, _collect()).result()
except RuntimeError:
    result = asyncio.run(_collect())
```

### LoopConfig — remove initial_state
```python
@dataclass
class LoopConfig:
    until:          str | Callable
    max_iterations: int = 1000
    _compiled_until: Any = field(default=None, repr=False)
```

The `initial_state` kwarg in `GraphOp.loop()` is forwarded to `PARENT.shared()` instead
of stored in LoopConfig.

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