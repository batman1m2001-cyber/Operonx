# ExecutionHandle Plan

Replace the dual `engine.run()` / `engine.stream()` API with a single
`engine.start()` that returns an `ExecutionHandle` — an async-iterable result
handle that streams op frames as they arrive.

---

## Design Decisions

| # | Decision |
|---|----------|
| 1 | Frame shape: `(op_name, ctx, data_dict)` tuple — mirrors internal `Frame` dataclass |
| 2 | Generator ops emit one frame per `yield` — enables true token streaming |
| 3 | `handle["op", "var"]` → awaitable, returns the **last** value at root context |
| 4 | `engine.run()` → thin wrapper: `await self.start(...).collect()` |
| 5 | Only **root graph** frames visible — nested GraphOp frames stay internal |
| 6 | `_output_queue` ContextVar **deleted** — `output_queue` param replaces it |

---

## Architecture

### Queue flow

```
engine.start(inputs)
  │
  ├─ creates ONE asyncio.Queue (output_queue)
  ├─ creates ExecutionHandle(output_queue, scheduler_task)
  └─ spawns asyncio.Task:
       root._Scheduler.run(state, ctx, output_queue=output_queue)
           │
           ├─ every Frame in root graph → output_queue.put_nowait((op, ctx, data))
           │
           └─ when a nested GraphOp runs:
                GraphOp.run() calls self._scheduler.run(state, ctx)
                                                          ↑
                                            NO output_queue — sub-graph
                                            frames stay internal
                GraphOp yields (ctx, aggregated_result) upward
                root scheduler sees that as a normal Frame
                → puts it on output_queue naturally
           │
           └─ when root scheduler done → output_queue.put_nowait(None)  # sentinel
```

### Why root-only

Each `GraphOp` has its own `_Scheduler` instance (created at `build()`, stored
as `self._scheduler`). `GraphOp.run()` calls `self._scheduler.run(state, ctx)`
at line 458 of `graph_op.py` — a direct call, not going through the parent
scheduler. The parent only sees what `GraphOp.run()` yields: the aggregated
final result. Internal frames from nested ops never surface to the parent.

Passing `output_queue` only to the root scheduler exploits this natural isolation:
sub-graph details stay private, only top-level op outputs are visible to the caller.

---

## `ExecutionHandle` class

**Status: DONE** (engine.py lines 43–178)

Improvements over original plan (keep as-is):
- `_MISSING` sentinel instead of `None` — avoids ambiguity when actual output value is `None`
- `_waiters` is `dict[tuple, list[Future]]` instead of `dict[tuple, Future]` — supports multiple concurrent waiters for the same output
- `_resolve_all_waiters` and `_match_waiters` extracted as helpers — cleaner `_pump`
- `_pump` has outer `except Exception` wrapper — catches unexpected errors inside `_pump` itself

```python
_MISSING = object()

class ExecutionHandle:
    def __init__(self, queue: asyncio.Queue, task: asyncio.Task) -> None:
        self._queue = queue
        self._scheduler_task = task
        self._frames: list[tuple[str, Any, dict[str, Any]]] = []
        self._idx: int = 0
        self._done: bool = False
        self._error: BaseException | None = None
        self._cond = asyncio.Condition()
        self._waiters: dict[tuple[str, str], list[asyncio.Future[Any]]] = {}
        self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                async with self._cond:
                    if item is None:
                        self._done = True
                        self._resolve_all_waiters(None)
                        self._cond.notify_all()
                        return
                    if isinstance(item, BaseException):
                        self._error = item
                        self._done = True
                        self._resolve_all_waiters(item)
                        self._cond.notify_all()
                        return
                    op, ctx, data = item
                    self._frames.append(item)
                    self._cond.notify_all()
                    self._match_waiters(op, data)
        except Exception as exc:
            async with self._cond:
                self._error = exc
                self._done = True
                self._resolve_all_waiters(exc)
                self._cond.notify_all()

    def _resolve_all_waiters(self, exc: BaseException | None) -> None:
        for futs in self._waiters.values():
            for fut in futs:
                if not fut.done():
                    if exc is None: fut.set_result(_MISSING)
                    else: fut.set_exception(exc)
        self._waiters.clear()

    def _match_waiters(self, op: str, data: dict) -> None:
        for var, val in data.items():
            for fut in self._waiters.pop((op, var), []):
                if not fut.done():
                    fut.set_result(val)

    def __aiter__(self) -> "ExecutionHandle": return self

    async def __anext__(self) -> tuple[str, Any, dict[str, Any]]:
        async with self._cond:
            while self._idx >= len(self._frames):
                if self._done:
                    if self._error: raise self._error
                    raise StopAsyncIteration
                await self._cond.wait()
            frame = self._frames[self._idx]
            self._idx += 1
            return frame

    def __getitem__(self, key: tuple[str, str]):
        op, var = key
        return self._await_output(op, var)   # coroutine — caller awaits

    async def _await_output(self, op: str, var: str) -> Any:
        async with self._cond:
            last = _MISSING
            for f_op, _, data in self._frames:
                if f_op == op and var in data:
                    last = data[var]
            if last is not _MISSING:
                return last
            if self._done:
                if self._error: raise self._error
                return None
            fut = asyncio.get_running_loop().create_future()
            self._waiters.setdefault((op, var), []).append(fut)
        val = await fut
        return None if val is _MISSING else val

    async def collect(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        async for _, _, data in self:
            out.update(data)
        return out

    def cancel(self) -> None:
        self._scheduler_task.cancel()
        self._pump_task.cancel()
```

---

## Changes per file

### `task_scheduler.py`

Add `output_queue` param to `_Scheduler.run()`:

```python
async def run(
    self,
    state,
    context_id: tuple,
    output_queue: asyncio.Queue | None = None,
) -> tuple[dict, list]:
    ...
    # In _on_frame, after routing:
    if output_queue is not None:
        output_queue.put_nowait((event.op, event.ctx, event.result))

    # After the while loop (graph done):
    if output_queue is not None:
        output_queue.put_nowait(None)   # sentinel
```

`GraphOp.run()` calls `self._scheduler.run(state, context_id)` with no
`output_queue` — sub-graph frames stay internal. No change needed in `graph_op.py`.

### `engine.py`

**Status: DONE** — `engine.py` lines 43–178 (`ExecutionHandle`) and lines 378–481 (`start()`, `run()`).

- `start()` creates an `asyncio.Queue`, spawns the scheduler task, returns `ExecutionHandle`
- `run()` delegates to `start(...).collect(unwrap=True)`
- `stream()` deleted; `_output_queue` ContextVar deleted
- `output_queue` param added to `Scheduler.run()` — frames are pushed directly, no ContextVar needed
- `llm.py` yields token chunks as frames from `run()` directly

---

## Implementation Notes

All items in this document are **fully implemented** as of the scheduler rewrite
(Steps 1–15, 704/704 tests passing).

Key locations in the current codebase:

| Component | File | Lines |
|-----------|------|-------|
| `ExecutionHandle` class | `engine.py` | 43–178 |
| `Hush.start()` | `engine.py` | 378–449 |
| `Hush.run()` | `engine.py` | 451–481 |
| `Scheduler.run()` + `output_queue` param | `task_scheduler.py` | 79–350 |
| `Scheduler._pump()` | `task_scheduler.py` | ~199–215 |

---

## Step 6 — `collector.py` fix

### Root cause

`_get_stream_contexts()` reads `stream_contexts` as a dynamic attribute off `GraphOp`:

```python
# collector.py line 194-198
def _get_stream_contexts(self, parent_name: str) -> list:
    parent_op = self._op_map.get(parent_name)
    return getattr(parent_op, "stream_contexts", [])   # ← always [] now
```

The old scheduler set this attribute on GraphOp after each run. The new scheduler
returns `item_ctxs` from `_Scheduler.run()` but `GraphOp.run()` uses it locally
and never stores it back. `stream_contexts` is also not in `__slots__`.

Without this data, all generators appear to have 0 yields and no `[N]` sub-nodes
in the trace tree — incomplete but not a crash.

### What `stream_contexts` was

A list of item context tuples produced by generator ops during a run:
```
[("main","[0]"), ("main","[1]"), ("main","[2]"), ...]
```

This data is already in state — `state.iter_executed(gen_op_name)` yields every
`(ctx, start_time)` pair including all item contexts. No new storage needed.

### Fix

Replace `_get_stream_contexts(parent_name)` with a state-driven method. All three
call sites are inside `_scan_nodes(self, state, executed_pairs)` which already
receives `state`.

```python
# REMOVE:
def _get_stream_contexts(self, parent_name: str) -> list: ...

# ADD:
def _get_stream_contexts(self, state, gen_op_name: str, gen_ctx: tuple) -> list:
    """Derive item contexts from state — no GraphOp attribute needed."""
    result = []
    for ctx, _ in state.iter_executed(gen_op_name):
        if (len(ctx) == len(gen_ctx) + 1
                and ctx[: len(gen_ctx)] == gen_ctx
                and _is_stream_segment(ctx[-1])):
            result.append(ctx)
    return result
```

Update the three call sites in `_scan_nodes` to pass `state`, `op_info["op"].full_name`,
and the current `ctx`.

### Scope

- 1 method signature changed
- 3 call sites updated (all in `_scan_nodes`)
- No structural changes to the rest of collector.py
