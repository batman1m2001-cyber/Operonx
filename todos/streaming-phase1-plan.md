# Streaming Phase 1 — Implementation Plan

## Context

Extend Hush from batch DAG execution to **streaming**: ops that yield multiple outputs over time with per-yield forwarding. Core principle: **streaming lives in the scheduler, not in ops**. An op just yields dicts; the scheduler handles context isolation, ready_count, broadcast, backpressure, and result collection.

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scheduler | Unified event-queue (replaces `asyncio.wait`) | Handles batch + streaming uniformly, edge-triggered, extensible |
| Context IDs | **Tuples** — `("main",)`, `("main", "s0")` | O(1) slice for broadcast, hierarchical, type-safe |
| Broadcast | Precomputed stream depth + tuple slice | `ctx[:depth+1]` — O(1), no string manipulation |
| Backpressure | Semaphore inside task runner | Limits concurrent downstream ops (not just scheduling) |
| END collection | Aggregate into list (like ForOp) | Simple, consistent, individual results still in state |
| Error handling | Fail entire graph | Matches current behavior, simplest for v1 |
| Generator detection | No `_is_generator` flag — use `inspect` at point of use | `inspect.isgeneratorfunction()` is O(1) bitwise check, no need to store |

## Context ID Design — Tuple-Based with Stream Depth

### Why Tuples Over Strings

Strings require `split(".")` + `".".join()` for broadcast lookup — O(depth) string ops. Tuples use `ctx[:depth+1]` — O(1) slice, immutable, hashable, type-safe. Benchmarked at **2.6x faster** on the critical broadcast lookup path.

### Context ID Format

```python
("main",)                        # top level (depth 0)
("main", "s0")                   # first yield of a generator (depth 1)
("main", "s0", "s0")             # nested generator yield (depth 2)
("main", "[0]")                  # ForOp iteration 0
("main", "[0]", "s0")            # streaming inside ForOp
("main", "s0", "[0]")            # ForOp inside streaming
```

### Broadcast via Stream Depth + Tuple Slice

At **build time**, compute each op's stream depth via topological traversal:

```python
stream_depth = {
    "config": 0,       # no generators upstream
    "outer_gen": 0,    # is a generator at depth 0
    "inner_gen": 1,    # downstream of 1 generator
    "process": 2,      # downstream of 2 generators
}
```

At **read time**, tuple slice:

```python
# process runs in ("main", "s0", "s1") at depth 2
# Reading from config (depth 0):     ctx[:1] = ("main",)            ← O(1) ✓
# Reading from outer_gen (depth 1):  ctx[:2] = ("main", "s0")       ← O(1) ✓
```

ForOp iteration elements (`"[0]"`) are NOT streaming depth boundaries. Stream depths are computed per-GraphOp — the inner GraphOp (ForOp's body) has its own `_stream_depths` dict. So `("main", "[0]")` is depth 0 within the sub-graph, and streaming yields append: `("main", "[0]", "s0")`.

### Impact on get_inputs()

```python
def get_inputs(self, state, context_id, parent_context=None):
    for var_name, param in self.inputs.items():
        # PARENT ref → use parent_context (unchanged)
        if (parent_context is not None
                and isinstance(param.value, Ref)
                and param.value.raw_source is self.parent):
            lookup_ctx = parent_context

        # Sibling ref with stream depth → compute correct context via tuple slice
        elif (self._stream_depths
                and isinstance(param.value, Ref)
                and param.value.raw_source is not self.parent):
            source_name = param.value.raw_source.full_name
            source_depth = self._stream_depths.get(source_name, 0)
            lookup_ctx = context_id[:source_depth + 1]  # O(1) tuple slice!

        # Default (no streaming) → use context_id as-is (unchanged)
        else:
            lookup_ctx = context_id

        value = state[self.full_name, var_name, lookup_ctx]
        ...
```

---

## Changes

### Change 0: Tuple Context Migration (Foundation)

Converts context IDs from strings to tuples. Small, mechanical changes across multiple files.

**File**: `hush-core/hush/core/states/cell.py`

```python
# Before
DEFAULT_CONTEXT = "main"
self.contexts: Dict[str, Any] = {}

# After
DEFAULT_CONTEXT = ("main",)
self.contexts: Dict[tuple, Any] = {}
```

**File**: `hush-core/hush/core/states/state.py`

```python
# _unpack_key — normalize context to tuple
@staticmethod
def _unpack_key(key):
    if len(key) == 2:
        return key[0], key[1], DEFAULT_CONTEXT
    op, var, ctx = key
    if ctx is None:
        return op, var, DEFAULT_CONTEXT
    # Accept string context for backwards compat
    if isinstance(ctx, str):
        return op, var, (ctx,)
    return op, var, ctx
```

**File**: `hush-core/hush/core/ops/iteration/base.py`

```python
# Before
def get_iter_context(prefix: str, i: int) -> str:
    if i < 1000:
        return prefix + _CTX_SUFFIXES[i]
    return prefix + "[" + str(i) + "]"

# After
def get_iter_context(prefix: tuple, i: int) -> tuple:
    if i < 1000:
        return prefix + (_CTX_SUFFIXES[i],)
    return prefix + ("[" + str(i) + "]",)
```

**Files**: `for_op.py`, `while_op.py`, `map_op.py`, `aiter_op.py` (~3 lines each)

```python
# Before
ctx_prefix = (context_id + ".") if context_id else ""
iter_context = get_iter_context(ctx_prefix, i)

# After
iter_context = get_iter_context(context_id, i)
```

**File**: `hush-core/hush/core/ops/base.py` — `identity()` method

```python
def identity(self, context_id) -> str:
    if context_id:
        ctx_str = ".".join(context_id) if isinstance(context_id, tuple) else str(context_id)
    else:
        ctx_str = "main"
    return f"{self.full_name}[{ctx_str}]"
```

**File**: `hush-core/hush/core/background/db.py` (lines 349-376)

The iteration group parsing currently uses `rfind("[")`, `rstrip(".")`, `strip("[]")` on string context IDs. With tuples:

```python
# Before (string parsing)
last_bracket_start = context_id.rfind("[")
parent_context = context_id[:last_bracket_start].rstrip(".")
last_bracket = context_id[last_bracket_start:]
iteration_index = int(last_bracket.strip("[]"))

# After (tuple operations)
# context_id is now a tuple like ("main", "[0]")
# Last element is the iteration suffix
last_element = context_id[-1]  # e.g. "[0]"
if not (isinstance(last_element, str) and last_element.startswith("[")):
    continue
parent_context = context_id[:-1] if len(context_id) > 1 else None
iteration_suffix = "".join(context_id)  # for display name
iteration_index = int(last_element.strip("[]"))
```

**File**: `hush-core/hush/core/background/flush.py` (line 41)

```python
# Before
trace_key = f"{op_name}:{context_id}" if context_id else op_name

# After
ctx_str = ".".join(context_id) if isinstance(context_id, tuple) else str(context_id)
trace_key = f"{op_name}:{ctx_str}" if context_id else op_name
```

**File**: `hush-core/hush/core/tracing/collector.py` — No code change needed. `ctx != DEFAULT_CONTEXT` works with tuples.

**Existing tests**: Update context_id assertions from strings to tuples. Cell tests that use arbitrary strings like `cell["loop1"]` still work — Cell accepts any hashable key. Only tests asserting specific context_id values (like `"main"`, `"main[0]"`) need updating.

### Change 1: `ast.Yield` in `extract_return_schema`

**File**: `hush-core/hush/core/ops/transform/func_op.py`

In `extract_return_schema()` (line ~266), add `ast.Yield` handling alongside existing `ast.Return`:

```python
for node in ast.walk(tree):
    if isinstance(node, ast.Return) and node.value:
        if isinstance(node.value, ast.Dict):
            schema.update(_extract_dict_keys(node.value, source_lines))
    elif isinstance(node, ast.Lambda):
        if isinstance(node.body, ast.Dict):
            schema.update(_extract_dict_keys(node.body, source_lines))
    # NEW: handle generator yield
    elif isinstance(node, ast.Yield) and node.value:
        if isinstance(node.value, ast.Dict):
            schema.update(_extract_dict_keys(node.value, source_lines))
```

No `_is_generator` flag needed. No changes to `FuncOp.__init__()`. No changes to `BaseOp.__slots__`.

### Change 2: Stream Depth Computation in `GraphOp.build()`

**File**: `hush-core/hush/core/ops/graph/graph_op.py`

**`__slots__` additions** — must add to GraphOp's `__slots__` list (line 163):
```python
__slots__ = [
    ...existing slots...
    "_stream_depths",
    "_has_streaming_ops",
    "_stream_predecrements",
    "_max_stream_concurrent",
]
```

**`_stream_depths` on BaseOp** — add to `BaseOp.__slots__` (line 207):
```python
__slots__ = [
    ...existing slots...
    "_stream_depths",
]
```

Initialize in `BaseOp.__init__()`: `self._stream_depths = None`

**Helper function** — define at module level in `graph_op.py`:

```python
def _is_gen(op_obj):
    """Check if op is a generator (sync or async). O(1) bitwise check."""
    fn = getattr(op_obj, "core", None)
    return inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn)
```

**In `build()`**, after computing `initial_ready_count` (line ~296):

```python
# Compute topological order via Kahn's algorithm
in_degree = {name: len(self.prevs[name]) for name in self._ops}
topo_queue = [name for name, deg in in_degree.items() if deg == 0]
topological_order = []
while topo_queue:
    name = topo_queue.pop(0)
    topological_order.append(name)
    for succ in self.nexts[name]:
        in_degree[succ] -= 1
        if in_degree[succ] == 0:
            topo_queue.append(succ)

# Compute stream depth for each op
self._stream_depths = {}
for name in topological_order:
    max_pred_depth = 0
    for pred in self.prevs[name]:
        pred_depth = self._stream_depths.get(pred, 0)
        if _is_gen(self._ops[pred]):
            pred_depth += 1
        max_pred_depth = max(max_pred_depth, pred_depth)
    self._stream_depths[name] = max_pred_depth

# Store on each child op for get_inputs() access
for name, op_obj in self._ops.items():
    op_obj._stream_depths = self._stream_depths

self._has_streaming_ops = any(_is_gen(op) for op in self._ops.values())

# Pre-compute stream predecrements per generator
self._stream_predecrements = {}
if self._has_streaming_ops:
    for name in self._ops:
        if _is_gen(self._ops[name]):
            predecrements = {}
            for succ_name in self.initial_ready_count:
                decrement = 0
                for pred in self.prevs.get(succ_name, []):
                    if self._stream_depths.get(pred, 0) < self._stream_depths.get(succ_name, 0):
                        edge = self._edges.get((pred, succ_name))
                        if edge:
                            decrement += 1  # both hard and soft edges count
                if decrement > 0:
                    predecrements[succ_name] = decrement
            self._stream_predecrements[name] = predecrements

self._max_stream_concurrent = 64  # TODO: make configurable via GraphOp constructor
```

### Change 3: Unified Event-Queue Scheduler

**File**: `hush-core/hush/core/ops/graph/graph_op.py`

Replace the current scheduler (lines 761-834) with a unified event-queue. Extract into `_run_scheduler()` method for reuse by Phase 3's `GraphOp.loop()`.

#### Helper: `_is_gen` check (replaces `_is_generator` flag)

Used in 3 places: `_can_inline()`, `_schedule_op()`, `build()`. All use the same `_is_gen()` helper defined above.

#### `_can_inline()` — add generator check

```python
def _can_inline(op_obj):
    return (
        not isinstance(op_obj, GraphOp)
        and not _is_gen(op_obj)                    # NEW: generators can't inline
        and not inspect.iscoroutinefunction(getattr(op_obj, "core", None))
        and getattr(op_obj, "executor", None) is None
    )
```

#### `BaseOp.run()` — guard against direct generator calls

If someone calls a generator op directly via `op(x=1)` (the quick-test `__call__`), `BaseOp.run()` line 774 would call `self.core(**_inputs)` and get a generator object instead of a dict. Add a guard:

```python
# In BaseOp.run(), before the core dispatch (line 769):
if inspect.isgeneratorfunction(self.core) or inspect.isasyncgenfunction(self.core):
    raise TypeError(
        f"Generator op '{self.name}' cannot be called via run() directly. "
        "Use it inside a GraphOp where the scheduler drives the generator."
    )
```

#### Task runners

```python
async def _run_op(name, op_obj, ctx, p_ctx):
    """Run op, apply semaphore for streaming contexts."""
    is_stream = ctx != context_id
    if is_stream:
        await semaphore.acquire()
    try:
        await op_obj.run(state, ctx, p_ctx)
    finally:
        if is_stream:
            semaphore.release()
    await event_queue.put(("done", name, ctx))

async def _drive_generator(name, op_obj, ctx, p_ctx):
    """Drive generator op, emit yield/exhausted events.
    Handles metrics/logging since BaseOp.run() is bypassed."""
    start_time = datetime.now()
    perf_start = perf_counter()
    error_msg = None
    _inputs = {}

    try:
        _inputs = op_obj.get_inputs(state, ctx, p_ctx)
        gen_fn = op_obj.core
        yield_idx = 0

        if inspect.isasyncgenfunction(gen_fn):
            async for result in gen_fn(**_inputs):
                stream_ctx = ctx + (f"s{yield_idx}",)
                op_obj.store_result(state, result, stream_ctx)
                await event_queue.put(("yield", name, stream_ctx))
                yield_idx += 1
        elif inspect.isgeneratorfunction(gen_fn):
            for result in gen_fn(**_inputs):
                stream_ctx = ctx + (f"s{yield_idx}",)
                op_obj.store_result(state, result, stream_ctx)
                await event_queue.put(("yield", name, stream_ctx))
                yield_idx += 1

    except Exception:
        error_msg = traceback.format_exc()
        LOGGER.error("[%s] Error in generator op %s:\n%s",
                     request_id, name, error_msg.rstrip())
    finally:
        duration_ms = (perf_counter() - perf_start) * 1000
        op_obj._log(request_id, ctx, _inputs, {}, duration_ms)
        op_obj._store_metrics(state, ctx, error=error_msg,
                              start_time=start_time, end_time=datetime.now(),
                              duration_ms=duration_ms)

    await event_queue.put(("exhausted", name))
```

#### Scheduling, activation, context creation

```python
def _create_stream_context(stream_ctx, gen_name):
    rc = initial_ready_count.copy()
    predecrements = self._stream_predecrements.get(gen_name, {})
    for op_name, decrement in predecrements.items():
        rc[op_name] -= decrement
    ready_counts[stream_ctx] = rc
    stream_contexts.append(stream_ctx)

def _activate_successors(op_name, ctx):
    rc = ready_counts[ctx]
    ss = soft_satisfied.setdefault(ctx, set())
    newly_ready = []
    for next_op, is_soft in _get_successors(op_name):
        if is_soft:
            if next_op in ss:
                continue
            ss.add(next_op)
        rc[next_op] -= 1
        if rc[next_op] == 0:
            newly_ready.append(next_op)
    return newly_ready

async def _schedule_op(name, ctx, p_ctx):
    nonlocal active_count
    op_obj = nodes[name]
    if _is_gen(op_obj):
        active_count += 1
        asyncio.create_task(_drive_generator(name, op_obj, ctx, p_ctx))
    elif _can_inline(op_obj):
        await op_obj.run(state, ctx, p_ctx)
        completed_in_ctx.setdefault(ctx, set()).add(name)
        return _activate_successors(name, ctx)
    else:
        active_count += 1
        asyncio.create_task(_run_op(name, op_obj, ctx, p_ctx))
    return []
```

#### Main loop

```python
async def _run_scheduler(self, state, context_id, parent_context, request_id):
    event_queue = asyncio.Queue()
    active_count = 0
    ready_counts = {context_id: self.initial_ready_count.copy()}
    completed_in_ctx = {}
    stream_contexts = []
    semaphore = asyncio.Semaphore(self._max_stream_concurrent)
    soft_satisfied = {}
    initial_ready_count = self.initial_ready_count
    nodes = self._ops
    compiled_adj = self._compiled_adj

    # ... define _can_inline, _get_successors, _activate_successors,
    #     _create_stream_context, _run_op, _drive_generator, _schedule_op
    #     as closures above ...

    # Start entries
    queue = list(self.entries)
    while queue:
        name = queue.pop(0)
        newly_ready = await _schedule_op(name, context_id, parent_context)
        queue.extend(newly_ready)

    # Event loop
    while active_count > 0:
        event = await event_queue.get()

        if event[0] == "done":
            _, op_name, ctx = event
            active_count -= 1
            completed_in_ctx.setdefault(ctx, set()).add(op_name)
            queue = list(_activate_successors(op_name, ctx))
            while queue:
                name = queue.pop(0)
                newly_ready = await _schedule_op(name, ctx, parent_context)
                queue.extend(newly_ready)

        elif event[0] == "yield":
            _, gen_name, stream_ctx = event
            _create_stream_context(stream_ctx, gen_name)
            newly_ready = _activate_successors(gen_name, stream_ctx)
            for name in newly_ready:
                await _schedule_op(name, stream_ctx, parent_context)

        elif event[0] == "exhausted":
            active_count -= 1

    return stream_contexts
```

#### Result collection (in `run()`)

```python
stream_contexts = await self._run_scheduler(state, context_id, parent_context, request_id)

if stream_contexts:
    max_depth = max(len(ctx) for ctx in stream_contexts)
    leaf_contexts = [ctx for ctx in stream_contexts if len(ctx) == max_depth]
    _outputs = {}
    for var_name in self.outputs:
        _outputs[var_name] = [
            state[self.full_name, var_name, ctx]
            for ctx in leaf_contexts
        ]
else:
    _outputs = self.get_outputs(state, context_id=context_id, parent_context=parent_context)

self.store_result(state, _outputs, context_id)
```

---

## Two Generators at Same Depth — Zip Semantics

When two generators at depth 0 both yield, they create the same context tuple:
- gen1 yield 0 → `("main", "s0")`, gen2 yield 0 → `("main", "s0")` (same context!)
- gen1 stores `state["gen1", "item", ("main", "s0")]`, gen2 stores `state["gen2", "data", ("main", "s0")]`
- Different op names → no overwrite
- `process` ready_count in `("main", "s0")` starts at 2 (needs both), decremented by each → fires when both yield

Unmatched yields are silently dropped. This is documented behavior, not a bug.

---

## Review Issues Addressed

| # | Issue | Resolution |
|---|-------|-----------|
| 1 | `__slots__` blocks new attributes | Add `_stream_depths` to `BaseOp.__slots__`, add `_stream_depths`/`_has_streaming_ops`/`_stream_predecrements`/`_max_stream_concurrent` to `GraphOp.__slots__` |
| 2 | `BaseOp.run()` breaks on generators | Add guard that raises `TypeError` — generators must run inside GraphOp |
| 3 | No topological order in `build()` | Add Kahn's algorithm using `prevs`/`nexts` (graph already validated acyclic) |
| 4 | `parent_context` passing | Confirmed correct: child ops get `(stream_ctx, parent_context)` — parent_context is for PARENT ref resolution, unchanged |
| 5 | `db.py` context parsing | Detailed tuple adaptation: `context_id[-1]` replaces `rfind("[")`, `context_id[:-1]` replaces string slicing |
| 6 | Cell tests use arbitrary strings | Cell accepts any hashable key — only `DEFAULT_CONTEXT` comparisons and `None→default` mapping need updating |
| 7 | `flush.py` trace key | Convert tuple to string for display: `".".join(context_id)` |
| 8 | No `_is_generator` flag | Removed — use `_is_gen(op_obj)` helper that calls `inspect.isgeneratorfunction()` at point of use. O(1) bitwise check, no storage needed |

---

## Files Modified Summary

### Context Migration (Change 0)

| File | Change | Lines |
|------|--------|-------|
| `states/cell.py` | `DEFAULT_CONTEXT = ("main",)`, type hint | ~3 |
| `states/state.py` | `_unpack_key` string→tuple compat | ~5 |
| `ops/iteration/base.py` | `get_iter_context` returns tuple | ~5 |
| `ops/iteration/for_op.py` | Remove `ctx_prefix` concat | ~3 |
| `ops/iteration/while_op.py` | Remove `ctx_prefix` concat | ~3 |
| `ops/iteration/map_op.py` | Remove `ctx_prefix` concat | ~3 |
| `ops/iteration/aiter_op.py` | Remove `ctx_prefix` concat | ~3 |
| `ops/base.py` | `identity()` tuple formatting | ~3 |
| `background/db.py` | Tuple context parsing | ~10 |
| `background/flush.py` | Tuple-to-string for trace key | ~2 |
| Existing tests | Update context_id assertions | varies |

### Streaming (Changes 1-3)

| File | Change |
|------|--------|
| `ops/base.py` | `_stream_depths` in `__slots__` + init, streaming-aware `get_inputs()`, generator guard in `run()` |
| `ops/transform/func_op.py` | `ast.Yield` in `extract_return_schema` |
| `ops/graph/graph_op.py` | `__slots__` additions, `_is_gen()` helper, topological order, stream depth computation, predecrements, `_run_scheduler()`, unified event-queue, result collection |
| `tests/ops/test_streaming.py` | New test file |

---

## Tests

**File**: `hush-core/tests/ops/test_streaming.py` (new)

| Test | Description |
|------|-------------|
| `test_yield_schema_extraction` | `extract_return_schema` handles `ast.Yield` |
| `test_stream_depth_computation` | `build()` computes correct depths for various topologies |
| `test_simple_stream_chain` | `source (yields 3) >> process >> END` → list of 3 results |
| `test_async_generator` | Async generator op works the same way |
| `test_broadcast` | `config >> process, source (yields) >> process` — config via tuple slice |
| `test_fan_out` | `source >> a, source >> b` → both run per yield |
| `test_fan_in_join` | `source >> a >> merge, source >> b >> merge` → merge fires per context |
| `test_two_generators_zip` | Two generators → zip by index, unmatched dropped |
| `test_backpressure_semaphore` | `max_concurrent=2` limits concurrent streaming ops |
| `test_generator_error_fails_graph` | Error in one context fails entire graph |
| `test_batch_only_unchanged` | Existing batch graph works identically with new scheduler |
| `test_nested_stream_depth` | Nested generators: correct context resolution |
| `test_generator_metrics` | Generator op has proper timing/logging/metrics |
| `test_streaming_inside_for_op` | Generator inside ForOp: context `("main", "[0]", "s0")` |
| `test_generator_direct_call_raises` | `op(x=1)` on generator raises TypeError |
| `test_tuple_context_backwards_compat` | String context passed to state auto-converts |

## Implementation Order

1. **Change 0**: Tuple context migration — get all existing tests passing with tuples
2. **Change 1**: `ast.Yield` in `extract_return_schema`
3. **Change 2**: Stream depth + predecrements in `GraphOp.build()`
4. **Change 3**: Unified event-queue scheduler + streaming-aware `get_inputs()` + generator guard
5. **Tests**: New streaming test file
6. **Verify**: All existing + new tests pass

## Verification

```bash
# Existing tests (must all pass — validates context migration + scheduler replacement)
cd hush-core && uv run -m pytest

# New streaming tests
cd hush-core && uv run -m pytest tests/ops/test_streaming.py -v

# Lint
cd hush-core && uv run ruff check . && uv run ruff format --check .
```
