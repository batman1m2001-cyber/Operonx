# Phantom Latency in GraphOp Scheduler — Research Notes

## The Problem

When running parallel workflows, sync ops (branch, exit_fn, format, etc.) reported
**150-210ms of wall-clock time** even though their actual work completed in **< 1ms**.

```
qc_flow (7.43s)
├── case1       2ms   ✓ fast (only 1 sync branch → exit)
├── case2       7.42s ✓ correct (LLM calls)
├── case3       6.41s ✓ correct (LLM calls)
├── case4       210ms ✗ should be ~2ms (3 sync ops: check → branch → exit)
├── case5       210ms ✗ should be ~2ms (3 sync ops: check → branch → exit)
├── s_agent     144ms ✗ should be ~5ms
└── s_customer  144ms ✗ should be ~6ms
```

After fix:

```
case4: 210ms → 2ms
case5: 210ms → 2ms
s_agent: 144ms → 5ms
s_customer: 144ms → 6ms
```

---

## Background: How asyncio Event Loop Works

Python's asyncio runs on a **single thread**. It maintains a queue of callbacks and
coroutines. Only ONE coroutine runs at a time. When a coroutine hits an `await`, it
**yields control** back to the event loop, which picks the next ready callback.

```
┌──────────────────────────────────────────────────┐
│                  Event Loop                       │
│                                                   │
│  Ready queue: [callback_A, callback_B, ...]       │
│                                                   │
│  1. Pick next ready callback/coroutine            │
│  2. Run it until it hits `await` or returns       │
│  3. If `await` completes immediately → continue   │
│     If `await` suspends → back to step 1          │
│  4. Process I/O events (sockets, timers, etc.)    │
│  5. Back to step 1                                │
└──────────────────────────────────────────────────┘
```

Key insight: **`await` does NOT always suspend**. If the awaited coroutine has no
internal suspension point (no I/O, no `await asyncio.sleep()`, no `await future`),
then `await coroutine()` runs to completion **synchronously** — the event loop never
gets a chance to interleave other work.

---

## Background: `asyncio.wait()` Always Yields

`asyncio.wait()` is designed for waiting on tasks that may take a long time. Even if
all tasks are **already done** when you call it, `asyncio.wait()` still yields to the
event loop at least once. This is by design — it gives the event loop a chance to
process other pending callbacks.

```python
# This ALWAYS yields, even if task is already done
done, _ = await asyncio.wait({task}, return_when=FIRST_COMPLETED)
# ↑ event loop runs other callbacks here before returning
```

This is fine for I/O-bound tasks where you want fair scheduling. But for tasks that
complete in microseconds, each yield becomes a ~50ms penalty because the event loop
uses that yield to process **other** pending work (HTTP socket events, DNS callbacks,
SSL handshakes from parallel LLM calls, etc.).

---

## Background: `BaseOp.run()` — Sync vs Async Path

Look at how `BaseOp.run()` executes the core function:

```python
async def run(self, state, context_id=None, parent_context=None):
    # ... setup, timing ...

    if asyncio.iscoroutinefunction(self.core):
        _outputs = await self.core(**_inputs)       # Path A: async → yields
    elif self.executor == "thread":
        _outputs = await asyncio.to_thread(...)     # Path B: thread → yields
    else:
        _outputs = self.core(**_inputs)              # Path C: sync → NO yield

    # ... store results, timing ...
```

**Path C is critical**: when `self.core` is a regular sync function and `executor` is
None, there is **no `await`** in the entire `run()` method body. The method is declared
`async def` but never actually suspends. This means:

```python
await sync_op.run(state, ctx)
# ↑ This completes IMMEDIATELY. No yield. No event loop interleaving.
# The next line of YOUR code runs right after, without any other task
# getting a chance to execute.
```

---

## Root Cause: The Old Scheduler

The old `GraphOp.run()` treated every child op the same way — wrap in `create_task`,
collect via `asyncio.wait`:

```python
# OLD CODE — every op goes through create_task + asyncio.wait
for entry in self.entries:
    task = asyncio.create_task(entry_op.run(state, ctx))    # (1)
    active_tasks[entry] = task

while active_tasks:
    done, _ = await asyncio.wait(                            # (2)
        active_tasks.values(),
        return_when=FIRST_COMPLETED
    )
    for task in done:
        # activate successors → create_task again             # (3)
        task = asyncio.create_task(next_op.run(state, ctx))
```

For **every** sync op, this introduces two penalties:

1. **`create_task()`** — wraps the coroutine in a Task object, schedules it on the
   event loop. The task may complete instantly, but it's now in the event loop's queue.

2. **`asyncio.wait()`** — yields to the event loop. Even though the task is done,
   the event loop processes other pending callbacks before returning to us.

---

## Step-by-Step: What Happens with case4

case4's graph: `is_suspicious (sync) → r0 branch (sync) → exit_fn (sync)`

All three ops are sync leaf ops that complete in < 0.1ms each.

Meanwhile, case2 and case3 are running parallel LLM calls (HTTP requests to OpenAI).
Their HTTP connections generate many event loop callbacks: DNS resolution, TCP connect,
SSL handshake, HTTP/2 stream management, response chunk processing, etc.

### Timeline with OLD scheduler

```
Time 0ms    case4 starts
            create_task(is_suspicious.run())
            is_suspicious completes instantly (sync, ~0.05ms)

Time 0ms    await asyncio.wait()
            ↓ YIELD to event loop
            ↓ Event loop processes case2's SSL handshake callback
            ↓ Event loop processes case3's DNS resolution callback
            ↓ Event loop processes case2's HTTP/2 stream setup
            ↓ ... more callbacks from case2/case3 ...
Time ~50ms  Event loop returns to case4's asyncio.wait()

Time 50ms   is_suspicious done → create_task(r0_branch.run())
            r0_branch completes instantly (sync, ~0.05ms)

Time 50ms   await asyncio.wait()
            ↓ YIELD to event loop
            ↓ Event loop processes case3's SSL handshake callback
            ↓ Event loop processes case2's response chunk callback
            ↓ ... more callbacks ...
Time ~100ms Event loop returns to case4's asyncio.wait()

Time 100ms  r0_branch done → create_task(exit_fn.run())
            exit_fn completes instantly (sync, ~0.05ms)

Time 100ms  await asyncio.wait()
            ↓ YIELD to event loop
            ↓ Event loop processes more case2/case3 callbacks
            ↓ ...
Time ~150ms Event loop returns to case4's asyncio.wait()

Time 150ms  exit_fn done → no more successors → case4 finishes

Total: ~150ms wall-clock, ~0.15ms actual work
Phantom latency: ~150ms (3 yields × ~50ms each)
```

The ~50ms per yield is not fixed — it depends on how many callbacks are queued from
other parallel tasks. With more parallel LLM calls, the phantom latency increases.

### Timeline with NEW scheduler (inline)

```
Time 0ms    case4 starts
            _can_inline(is_suspicious) → True (sync, no executor, not GraphOp)
            await is_suspicious.run()     ← direct await, NO yield
            is_suspicious completes (~0.05ms)

Time 0ms    _activate_successors("is_suspicious") → ["r0"]
            _can_inline(r0_branch) → True
            await r0_branch.run()         ← direct await, NO yield
            r0_branch completes (~0.05ms)

Time 0ms    _activate_successors("r0") → ["exit_fn"]
            _can_inline(exit_fn) → True
            await exit_fn.run()           ← direct await, NO yield
            exit_fn completes (~0.05ms)

Time 0ms    _activate_successors("exit_fn") → [] (no more)
            queue empty → done
            active_tasks empty → case4 finishes

Total: ~0.15ms wall-clock, ~0.15ms actual work
Phantom latency: 0ms
```

The event loop **never gets a chance to interleave** because there is no yield point
in the entire chain. All three sync ops run as one continuous burst.

---

## Why `await sync_op.run()` Doesn't Yield

This is the most unintuitive part. The method is declared `async def`:

```python
async def run(self, state, context_id=None, parent_context=None):
```

But for sync ops (Path C), the method body **never uses `await`**:

```python
    _inputs = self.get_inputs(state, context_id, parent_context)  # sync
    _outputs = self.core(**_inputs)                                # sync (Path C)
    self.store_result(state, _outputs, context_id)                 # sync
    state[self.full_name, "duration_ms", context_id] = ...         # sync
    return _outputs                                                # sync
```

In Python's asyncio, `async def` just means "this function returns a coroutine object."
It does NOT mean "this function will suspend." A coroutine only suspends when it hits
an actual `await` on something that suspends (a Future, another suspending coroutine,
etc.).

### Demonstration

```python
import asyncio

async def sync_style():
    """Declared async but never suspends."""
    x = 1 + 1
    return x

async def async_style():
    """Actually suspends."""
    await asyncio.sleep(0)  # ← suspension point
    return 42

async def main():
    print("before sync_style")
    result = await sync_style()   # Runs to completion, no suspension
    print(f"after sync_style: {result}")  # Prints immediately

    print("before async_style")
    result = await async_style()  # Suspends at sleep(0), event loop runs
    print(f"after async_style: {result}")  # Prints after event loop cycle

asyncio.run(main())
```

Output:
```
before sync_style
after sync_style: 2        ← no gap, no event loop cycle
before async_style
after async_style: 42      ← event loop ran between these
```

---

## Why `create_task()` + `asyncio.wait()` Forces a Yield

Even though the coroutine has no suspension point, wrapping it in `create_task()`
changes the execution model:

```python
# Direct await — runs inline, no yield
await sync_op.run()  # completes immediately

# Via create_task — schedules on event loop, requires wait
task = asyncio.create_task(sync_op.run())  # schedules, may run immediately
done, _ = await asyncio.wait({task})       # ALWAYS yields, even if task is done
```

`asyncio.create_task()` puts the coroutine on the event loop's ready queue. The
coroutine may execute immediately (before `asyncio.wait()` is called), but
`asyncio.wait()` still yields to give the event loop a scheduling cycle. This is where
the phantom latency enters.

---

## Why `executor="thread"` Would Be Even Worse

With `executor="thread"`, `BaseOp.run()` uses:

```python
_outputs = await asyncio.to_thread(self.core, **_inputs)
```

`asyncio.to_thread()` dispatches the function to a ThreadPoolExecutor and returns a
Future. This is an `await` point — it **yields to the event loop**. Combined with
`asyncio.wait()`, you get **two yields per op**:

```
1. create_task(op.run())
2. op.run() hits: await asyncio.to_thread(core)
   → YIELD #1: event loop services other tasks (~50ms)
3. Thread completes core() in ~0.05ms
   → Callback queued on event loop
4. asyncio.wait() picks up done task
   → YIELD #2: event loop services other tasks (~50ms)

Total: ~100ms phantom latency per op (vs ~50ms with old scheduler)
```

`executor="thread"` is designed for **blocking** sync operations (file I/O, heavy CPU,
ONNX inference) where you **want** to yield so the event loop stays responsive. For
fast sync ops (< 1ms), the thread dispatch overhead exceeds the actual work.

---

## The Fix: Inline Sync Leaf Ops

### Detection: `_can_inline()`

Identify ops that are safe to run without `create_task`:

```python
def _can_inline(op_obj):
    return (
        not isinstance(op_obj, GraphOp)                                    # (1)
        and not asyncio.iscoroutinefunction(getattr(op_obj, "core", None)) # (2)
        and getattr(op_obj, "executor", None) is None                      # (3)
    )
```

1. **Not GraphOp**: GraphOps have their own scheduler with `asyncio.wait()`, so they
   inherently yield.
2. **Not async core**: If `core()` is `async def`, it may have internal `await` points.
   Must use `create_task` for proper concurrency.
3. **No executor**: If `executor="thread"`, `run()` uses `await asyncio.to_thread()`
   which yields.

When all three are met → `await op.run()` completes synchronously, no yield.

### Scheduling: `_schedule_ops()`

One unified BFS function handles both entry ops and successor activation:

```python
async def _schedule_ops(names: list):
    """Run ready ops: inline sync leaves, create tasks for async/graph."""
    queue = list(names)
    while queue:
        name = queue.pop(0)
        op_obj = nodes[name]
        if _can_inline(op_obj):
            await op_obj.run(state, context_id, parent_context)  # no yield
            queue.extend(_activate_successors(name))
        else:
            active_tasks[name] = asyncio.create_task(
                name=name, coro=op_obj.run(state, context_id, parent_context)
            )
```

The BFS queue processes all transitively-ready sync ops in one burst without yielding.
The chain only breaks when it hits an async op, a GraphOp, or an op with an executor —
those go to `create_task` and get collected by `asyncio.wait()`.

### The Complete Scheduler

```python
# 1. Schedule entry ops (inline sync chains, task async ops)
await _schedule_ops(self.entries)

# 2. Wait for async tasks, schedule their successors
while active_tasks:
    done, _ = await asyncio.wait(
        active_tasks.values(), return_when=asyncio.FIRST_COMPLETED
    )
    for task in done:
        op_name = task.get_name()
        active_tasks.pop(op_name)
        await _schedule_ops(_activate_successors(op_name))
```

This is the entire scheduling loop. Three key properties:

1. **Sync chains run as one burst** — no `create_task`, no `asyncio.wait()`, no yields.
   A chain of 10 sync ops runs in one continuous execution with zero phantom latency.

2. **Async ops get proper concurrency** — `create_task` puts them on the event loop,
   `asyncio.wait(FIRST_COMPLETED)` collects them as they finish. Multiple async ops
   (e.g., parallel LLM calls) run truly in parallel.

3. **BFS order preserved** — the queue processes ops in breadth-first order. When an
   async task completes and activates multiple successors, they are processed left-to-right
   in the queue, which ensures merge nodes (multiple predecessors) get their ready_count
   decremented in the correct order.

---

## How `_activate_successors()` Drives the Scheduler

The ready-count mechanism is what determines **when** an op can run:

```python
def _activate_successors(op_name: str) -> list:
    """Decrement ready counts and return newly-ready op names."""
    newly_ready = []
    for next_op, is_soft in _get_successors(op_name):
        if is_soft:
            if next_op in soft_satisfied:
                continue              # already counted this soft group
            soft_satisfied.add(next_op)
        ready_count[next_op] -= 1
        if ready_count[next_op] == 0:
            newly_ready.append(next_op)
    return newly_ready
```

- **Hard edges** (`>>`) count individually. If op D has `A >> D, B >> D`, then
  `ready_count[D] = 2`. Both A and B must complete before D runs.

- **Soft edges** (`>`, from branch outputs) count as a group of 1. If op D has
  `A >> D, B > D, C > D`, then `ready_count[D] = 2` (1 hard + 1 soft group).
  Only ONE of B or C needs to complete (since branches are exclusive).

The returned `newly_ready` list feeds directly into `_schedule_ops()`, which
immediately runs sync ops inline or creates tasks for async ops.

---

## Execution Flow Comparison

### Old Scheduler (all ops go through create_task)

```
                   create_task        asyncio.wait       create_task       asyncio.wait
entry_op ──────→ [event loop queue] ──→ YIELD ──→ next_op ──→ [queue] ──→ YIELD ──→ ...
                                        ~50ms                               ~50ms
```

Every op, regardless of type, pays the `create_task` + `asyncio.wait()` tax.

### New Scheduler (inline sync, task async)

```
Sync chain (no yields):
entry_sync_op → await run() → _activate → next_sync_op → await run() → _activate → ...
                  0ms                        0ms                          0ms

Async op (yields naturally):
async_op → create_task → asyncio.wait(FIRST_COMPLETED) → _activate → ...
                              ↕ (event loop runs other work while waiting)
```

Sync chains complete instantly. Async ops yield **naturally** (because they actually
need to wait for I/O), not artificially (because the scheduler forced a yield).

---

## Measuring: `perf_counter()` Captures Phantom Latency

GraphOp measures duration with `perf_counter()`:

```python
perf_start = perf_counter()
# ... entire scheduling loop runs here ...
duration_ms = (perf_counter() - perf_start) * 1000
```

`perf_counter()` measures **wall-clock time**, including time the event loop spent
servicing other tasks. This is why case4 showed 210ms — the actual ops ran for 0.15ms
but `perf_counter()` captured all the event loop detour time.

This measurement is **correct** — it reflects the real elapsed time. The fix is not to
change the measurement, but to eliminate the unnecessary yields so wall-clock time
matches actual work time.

---

## Summary

| Factor | Impact |
|---|---|
| `asyncio.wait()` always yields | ~50ms per yield cycle |
| 3 sequential sync ops in case4 | 3 × ~50ms = ~150ms phantom |
| More parallel LLM calls | More callbacks = longer yield detours |
| Fix: inline sync ops | 0 yields → 0 phantom latency |

| Op type | Right strategy |
|---|---|
| Sync, fast (< 1ms) | **Inline** — direct `await`, no `create_task` |
| Sync, blocking (I/O, CPU) | `executor="thread"` — unblocks event loop |
| Async (LLM, HTTP) | `create_task` + `asyncio.wait` — natural concurrency |
| GraphOp (nested graph) | `create_task` + `asyncio.wait` — has own scheduler |
