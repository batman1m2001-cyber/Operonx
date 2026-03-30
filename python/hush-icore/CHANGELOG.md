# Changelog

## [Unreleased] — Streaming Refactor (Phases 2–4)

### Summary

Complete overhaul of the streaming execution layer in `hush-icore`. The old
event-based `scheduler.py` + `_loop.py` are replaced by a single unified
`task_scheduler.py`. Generator-to-downstream routing now supports three modes:
**sequential** (default), **`.parallel()`** (per-item concurrent), and
**`.collect()`** (buffer-all, dispatch once). 700 tests pass.

---

### New Features

- **`.collect()` streaming mode** — `gen["key"].collect()` buffers every yield
  from a generator op and dispatches the downstream op exactly **once** with the
  full list as input. The graph output is scalar (one run, one result), not a
  list. Contrast with `.parallel()` where the downstream runs N times and the
  graph output is a list of N results.

- **`LoopConfig` + `run_loop` merged into `task_scheduler.py`** — loop
  execution logic previously split across `_loop.py` is now co-located with the
  scheduler, eliminating a circular import surface.

- **8 new `.collect()` tests** in `tests/ops/test_streaming_collect.py`
  covering downstream call count, scalar output, ordering, timing, and
  comparison against `.parallel()`.

---

### Bug Fixes

#### BUG-1 — `_on_yield` attribute not in `__slots__` → `AttributeError` on `BranchOp`

**File:** `hush/core/ops/base.py`

**Root cause:** The scheduler sets `op._on_yield = callback` on generator ops
before calling `op.run()`. `_on_yield` was defined as a class-level attribute
(`_on_yield = None`) but was missing from `__slots__`. Subclasses that define
their own `__slots__` (e.g. `BranchOp`) inherit the parent slots only — the
class-level default is invisible to `__slots__`-enforced attribute lookup,
causing `AttributeError: _on_yield` when the scheduler tried to assign the
callback.

**Fix:** Added `"_on_yield"` to `BaseOp.__slots__` and initialised it to
`None` in `__init__`. Removed the class-level `_on_yield = None`.

---

#### BUG-2 — Cache hit path skipped `store_result()` → downstream ops read stale/empty values

**File:** `hush/core/ops/base.py`

**Root cause:** When a cache hit occurred, `_outputs` was populated from the
cache store but `self.store_result(state, _outputs, context_id)` was never
called. Downstream ops call `get_inputs()` which reads from `state`, so they
received `None` for every key that should have come from the cached op.

**Fix:** Added `self.store_result(state, _outputs, context_id)` in the cache
hit branch, immediately after loading `_outputs` from the cache store.

---

#### BUG-3 — `yield_waiter` + `gen_task` race: first yield item lost from `collect_buffers`

**File:** `hush/core/ops/graph/task_scheduler.py`

**Root cause:** `asyncio.wait(FIRST_COMPLETED)` can return both `yield_waiter`
and `gen_task` in the same `done` set. `yield_waiter` already holds item `[0]`
(removed from the queue via `await queue.get()`). If `gen_task` was processed
**before** `yield_waiter` in the loop over `done`, the drain of
`yield_queue.get_nowait()` would only see items `[1], [2], …` — item `[0]` was
held by `yield_waiter` but hadn't been processed yet. When `collect_buffers`
was checked immediately after, item `[0]` was missing, so the collect dispatch
sent an incomplete list to the downstream op.

**Fix:** Always process `yield_waiter` **first** in the `done` iteration:
```python
if yield_waiter and yield_waiter in done:
    event = yield_waiter.result()
    self._handle_yield(event, ...)   # item [0] processed here

for t in done:
    if t is yield_waiter:
        pass  # already handled
    elif t in active:
        ...   # gen_task processed here — now drain is safe
```

---

#### BUG-4 — Remaining yield events not drained before `collect_buffers` check

**File:** `hush/core/ops/graph/task_scheduler.py`

**Root cause:** Even with BUG-3 fixed, the `yield_queue` may still contain
buffered events that arrived while the `gen_task` was in flight. When
`gen_task` completes, the code immediately checked `collect_buffers` — but
those queued events hadn't been processed via `_handle_yield` yet, so they
were never added to `collect_buffers`.

**Fix:** When `gen_task` completes, drain `yield_queue` synchronously before
checking `collect_buffers`:
```python
if is_gen_op:
    while not yield_queue.empty():
        self._handle_yield(yield_queue.get_nowait(), ...)
    # Now collect_buffers contains ALL items
    for (src, dst), buffer in list(collect_buffers.items()):
        ...
```

---

#### BUG-5 — `.collect()` stored result in downstream op's namespace instead of source op's namespace

**File:** `hush/core/ops/graph/task_scheduler.py`

**Root cause:** When dispatching the collect result, the code called
`dst_op.store_result(state, collected_values, collect_ctx)`. But downstream
ops read their inputs via `get_inputs()` which resolves `Ref` objects pointing
to the **source** op's namespace (e.g. `gen["item"]` → reads from
`state["gen", "item", ctx]`). Storing under `dst_op`'s namespace meant the
input was never found — the downstream op received `None` for every collect
field.

**Fix:** Changed to `src_op.store_result(state, collected_values, collect_ctx)`.

---

#### BUG-6 — `.collect()` graph output wrapped in list instead of scalar

**File:** `hush/core/ops/graph/task_scheduler.py`

**Root cause:** The output collector iterated `stream_contexts` and appended
any non-`None` value to `per_item_vals`. For `.collect()`, `stream_contexts`
contains `N` per-item contexts (`[0]`, `[1]`, …) plus one `__collect__`
context. The downstream op only ran in `__collect__`, so for output var `"count"`:

```
ctx=("[0]")          → state["agg","count","[0]"] = None
...
ctx=("__collect__")  → state["agg","count","__collect__"] = 5
per_item_vals = [5]  → outputs["count"] = [5]   ← WRONG
```

The single result `5` was incorrectly wrapped in a list because the collector
didn't distinguish "ran once in a collect context" from "ran N times in
per-item contexts".

**Fix:** Detect `ctx[-1] == "__collect__"` → assign as scalar directly:
```python
if ctx[-1] == "__collect__":
    collect_val = val        # scalar — downstream ran once
else:
    per_item_vals.append(val)  # list  — downstream ran per-item

outputs[var] = collect_val if collect_val is not None else per_item_vals
```

---

### Refactoring / Dead Code Removal

- **Deleted `hush/core/ops/graph/scheduler.py`** — old event-based scheduler,
  fully superseded by `task_scheduler.py`.

- **Deleted `hush/core/ops/graph/_loop.py`** — loop logic merged into
  `task_scheduler.py` (`LoopConfig`, `_evaluate_until`, `run_loop`).

- **Removed `_run_streaming()` from `graph_op.py`** — dead method that was
  never called after the task-scheduler migration.

- **Removed `PENDING = object()` sentinel** from `task_scheduler.py` —
  leftover from the old scheduler, never referenced.

- Updated `hush/core/tracing/collector.py` import: `scheduler` → `task_scheduler`.

---

### Test Coverage

| File | Tests | What it covers |
|------|-------|----------------|
| `tests/ops/test_streaming_collect.py` | 8 | `.collect()` correctness |
| `tests/ops/test_streaming_regression.py` | 6 | Regression guards for BUG-1…6 |

Total: **700 tests pass** (was 692 before this work).
