# Streaming Refactor Plan

## Problem

Current Hush scheduler has 3 code paths for executing ops:
1. `op.run()` — normal ops
2. `_run_generator()` — generator ops (bypasses `run()` entirely)
3. `_run_streaming()` — nested streaming GraphOps

All 3 duplicate: get_inputs, store_result, metrics, error handling.

Additionally:
- Default streaming is **parallel** → causes ordering bugs with stateful ops
- Output collection is **implicit** — scheduler guesses what to collect
- No way to control execution mode (sequential vs parallel) per-variable
- `PENDING` sentinel is dead code in task scheduler
- `active_count` is redundant (have `active` set)

## Design

### Ref Modifiers — chained methods on Ref

```python
op["x"]                           # sequential, per-item (default, safe)
op["x"].parallel()                # parallel unlimited, per-item
op["x"].parallel(max=4)           # parallel with limit, per-item
op["x"].collect()                 # sequential, collect all → list
op["x"].parallel().collect()      # parallel, collect all → list
op["x"].parallel(max=4).collect() # parallel limited, collect all → list
```

Two orthogonal axes:
- **Execution**: sequential (default) vs `.parallel(max=N)`
- **Output**: per-item streaming (default) vs `.collect()` → list

### How each mode works

**Sequential (default)**:
```
source yields: 0, 1, 2
downstream:    process(0) → done → process(1) → done → process(2) → done
```
Scheduler: dispatch next item only when previous item's downstream chain completes.

**Parallel**:
```
source yields: 0, 1, 2
downstream:    process(0) ──┐
               process(1) ──┤ (concurrent)
               process(2) ──┘
```
Scheduler: dispatch all immediately (or up to `max` limit).

**Collect**:
```
source yields: 0, 1, 2
after all done: downstream receives [result_0, result_1, result_2] as single list
```
Scheduler: wait until source exhausted + all items processed → collect results → dispatch downstream once with list.

**Parallel + Collect**:
Same as collect but items process concurrently before collection.

### Implementation on Ref

```python
class Ref:
    def __init__(self, source, var):
        self.source = source
        self.var = var
        self._parallel = False
        self._parallel_max = None
        self._collect = False

    def parallel(self, max=None):
        """Mark this ref for parallel consumption."""
        new = Ref(self.source, self.var)
        new._parallel = True
        new._parallel_max = max
        new._collect = self._collect
        return new

    def collect(self):
        """Mark this ref to collect all items into list."""
        new = Ref(self.source, self.var)
        new._parallel = self._parallel
        new._parallel_max = self._parallel_max
        new._collect = True
        return new
```

### Scheduler changes

Current `_handle_yield()` dispatches downstream immediately (parallel). New logic:

```python
def _handle_yield(event):
    for downstream_op in get_ready_downstream(event):
        ref = get_input_ref(downstream_op, event.op_name)

        if ref._collect:
            # Don't dispatch yet — buffer result, wait for source exhausted
            collect_buffer[downstream_op].append(result)

        elif ref._parallel:
            # Dispatch immediately (current behavior)
            # Respect max limit via semaphore per-ref
            dispatch(downstream_op, stream_ctx)

        else:
            # Sequential (default): queue, dispatch when previous done
            sequential_queue[downstream_op].append((stream_ctx, result))
            if not sequential_active[downstream_op]:
                dispatch_next_sequential(downstream_op)

def on_op_done(op_name, ctx):
    # Check if sequential queue has next item
    if op_name in sequential_queue and sequential_queue[op_name]:
        dispatch_next_sequential(op_name)
```

On source exhausted:
```python
def on_source_exhausted(source_name):
    # Dispatch collected items
    for downstream_op, buffer in collect_buffer.items():
        if buffer.source == source_name:
            dispatch(downstream_op, ctx, input=list(buffer))
```

### Unify run() — void, no return value

`run()` does the job, stores result to state, returns nothing.
Scheduler doesn't inspect return values. Task completion = op done.

```python
class BaseOp:
    async def run(self, state, context_id, parent_context):
        """Single execution entry point. Returns nothing.

        Handles both normal and generator ops.
        Results stored directly to state. Scheduler notified via _on_yield callback.
        """
        inputs = self.get_inputs(state, context_id, parent_context)

        try:
            if self._is_generator:
                idx = 0
                async for result in self.core(**inputs):
                    stream_ctx = context_id + (f"[{idx}]",)
                    self.store_result(state, result, stream_ctx)
                    if self._on_yield:
                        await self._on_yield(stream_ctx, result)
                    idx += 1
            else:
                result = await self._exec_core(inputs)
                self.store_result(state, result, context_id)
        except Exception:
            ...  # error handling once
        finally:
            self._store_metrics(...)  # metrics once

        # NO return. Task completion = op done.
```

Scheduler simplified:
```python
async def run_op(task):
    op._on_yield = lambda ctx, result: yield_queue.put(YieldEvent(...))
    await op.run(state, ctx, parent)
    # Task completes → propagate downstream
    # No return value to check. No "exhausted" string. No PENDING.

# Main loop:
done, _ = await asyncio.wait(active, FIRST_COMPLETED)
for task in done:
    active.discard(task)
    propagate(task.op_name, task.ctx)  # always propagate on completion
```

No `_run_generator()`. No `_run_streaming()`. No return value parsing. One path.

### GraphOp streaming

GraphOp with streaming children: `run()` internally runs scheduler → collects outputs → yields per-item via `_on_yield`. Returns nothing.

```python
class GraphOp(BaseOp):
    async def run(self, state, context_id, parent_context):
        """Run inner graph. Void return. Results stored to state."""
        self.get_inputs(state, context_id, parent_context)
        outputs, stream_ctxs = await run_scheduler(self, state, ...)

        if stream_ctxs:
            # Streaming: yield per-item
            for i in range(list_len):
                item = {k: v[i] for k, v in outputs.items()}
                stream_ctx = context_id + (f"[{i}]",)
                self.store_result(state, item, stream_ctx)
                if self._on_yield:
                    await self._on_yield(stream_ctx, item)
        else:
            # Batch: store once
            self.store_result(state, outputs, context_id)

        # NO return.
```

No separate `_run_streaming()`. Same `run()` method. Same void pattern.

### Output collection removal

With explicit `.collect()`, no more implicit output collection at graph end.

Graph outputs are simply what the **terminal op** stores in state:
- If terminal receives per-item → graph output is per-item (list from stream contexts)
- If terminal receives from `.collect()` → graph output is whatever terminal returns

The complex `_has_output` / `terminal_ctxs` / `leaf_ctxs` / `shared_indices` filter logic is **removed**. Output is just: read terminal op's output from state.

Shared vars (`PARENT.shared()`) still read from DEFAULT_CONTEXT — that logic stays but is simpler without the output collection guessing.

### Flow control — no special frames

**No `EndStream` / `EndGraph` / `Interrupt` keywords needed.**

- **Generator exhaustion**: source task completes = source done. `.collect()` triggers when source task finishes.
- **Graceful stop**: ops call `get_current_scheduler().cancel()` directly
- **Force stop**: ops call `get_current_scheduler().abort()` directly

`.collect()` mechanism:
```
Source A yields: item_0, item_1, item_2
Source A task completes (generator exhausted)
  → scheduler checks: any .collect() consumers waiting on A?
  → yes: dispatch downstream with [item_0, item_1, item_2]
```

### Remove dead code

- `PENDING` sentinel — unused in task scheduler
- `_is_gen()` function — still needed internally but not for branching code paths
- Old `scheduler.py` — can delete after migration
- `_loop.py` — already integrated into scheduler
- `active_count` concept — replaced by `len(active)` set
- `"exhausted"` return string — task completion is the signal

## Migration

### Phase 1: Ref modifiers + sequential default
- Add `.parallel()`, `.collect()` methods to Ref
- Change scheduler default to sequential
- Fix ordering bug (audio processor)
- All existing tests: add `.parallel()` where needed to maintain behavior

### Phase 2: Unify run()
- Move generator handling into BaseOp.run()
- Add `_on_yield` callback mechanism
- Remove `_run_generator()` from scheduler
- Remove `_run_streaming()` from GraphOp
- Simplify scheduler to single dispatch path

### Phase 3: Explicit output collection
- Remove implicit output collection logic
- Graph outputs from terminal op state
- `.collect()` for explicit list gathering
- Remove `_has_output`, `terminal_ctxs`, `leaf_ctxs` complexity

### Phase 4: Cleanup
- Remove old `scheduler.py`
- Remove `_loop.py`
- Remove `PENDING` sentinel
- Update all examples and docs

## Files changed

```
Phase 1:
  hush-icore/hush/core/states/ref.py           — .parallel(), .collect() methods
  hush-icore/hush/core/ops/graph/task_scheduler.py — sequential default, dispatch modes

Phase 2:
  hush-icore/hush/core/ops/base.py             — unified run(), _on_yield callback
  hush-icore/hush/core/ops/graph/graph_op.py   — remove _run_streaming()
  hush-icore/hush/core/ops/graph/task_scheduler.py — remove _run_generator(), single dispatch

Phase 3:
  hush-icore/hush/core/ops/graph/task_scheduler.py — remove output collection, .collect() dispatch

Phase 4:
  hush-icore/hush/core/ops/graph/scheduler.py  — DELETE
  hush-icore/hush/core/ops/graph/_loop.py      — DELETE
  hush-icore/hush/core/ops/base.py             — remove PENDING
```

## Test Migration

### Tests that need `.parallel()` added (Phase 1)

With sequential as new default, tests relying on concurrent execution will slow down
or change behavior. Add `.parallel()` to maintain performance where needed:

```python
# BEFORE (implicit parallel):
d = double(x=source["x"])

# AFTER (explicit parallel for stateless ops):
d = double(x=source["x"].parallel())
```

Test categories:

**1. Streaming tests (`test_streaming.py`)**
- `TestSimpleStreamChain` — sequential is fine, results same
- `TestAsyncGenerator` — sequential is fine
- `TestBroadcast` — needs `.parallel()` (two consumers of same source)
- `TestFanOut` — needs `.parallel()` (fork pattern)
- `TestFanInJoin` — soft edges, needs review
- `TestTwoGeneratorsZip` — needs `.parallel()` (two sources)
- `TestBackpressure` — REWRITE with `.parallel(max=N)`
- `TestGeneratorError` — sequential is fine
- `TestBatchOnlyUnchanged` — no generators, unchanged
- `TestGeneratorMetrics` — sequential is fine
- `TestNestedStreamDepth` — sequential is fine

**2. N-to-M tests (`test_streaming_ntom.py`)**
- All sequential — no changes needed ✓

**3. Iteration tests (`test_for_op.py`, `test_aiter_op.py`, `test_map_op.py`)**
- Already sequential — no changes needed ✓

**4. Graph loop tests (`test_graph_loop.py`)**
- Loops are sequential by nature — no changes needed ✓

**5. Concurrent tests (`test_concurrent.py`)**
- CCU tests: each request is independent — no changes
- Internal parallelism: may need `.parallel()` where fork patterns exist

**6. Engine stream tests (`test_engine_stream.py`)**
- Streaming output: sequential fine for correctness, may need `.parallel()` for perf

**7. Shared vars tests (`test_shared_vars.py`)**
- Sequential default makes shared vars safer — no changes needed ✓

### Tests for new features (add in each phase)

**Phase 1:**
- `test_sequential_default` — verify items processed in order
- `test_parallel_ref` — verify `.parallel()` enables concurrent execution
- `test_parallel_max` — verify `.parallel(max=N)` limits concurrency
- `test_collect_basic` — verify `.collect()` gathers into list
- `test_parallel_collect` — verify `.parallel().collect()` concurrent + gather
- `test_collect_waits_for_exhaustion` — verify `.collect()` waits until source done

**Phase 2:**
- `test_void_run` — verify `run()` returns None
- `test_on_yield_callback` — verify generator yields trigger callback
- `test_unified_metrics` — verify metrics work for both generator and non-generator
- `test_unified_error_handling` — verify errors handled same way for both

**Phase 3:**
- `test_no_implicit_collection` — verify graph outputs without `.collect()` are per-item
- `test_explicit_collect_output` — verify `.collect()` at graph end produces list

## Verification

Each phase: run full test suite (692+ tests).
- Phase 1: some tests need `.parallel()` added. New streaming behavior tests added.
- Phase 2: internal refactor, existing test API unchanged.
- Phase 3: output collection changes may affect tests that check graph result format.
- Phase 4: cleanup, no behavior changes.

## Examples after refactor

### Callbot speech pipeline
```python
@graph
def callbot(wav_path, script_data):
    PARENT.shared(rs={"resampler": None}, buf={"data": deque()}, ...)

    source = wav_source(wav_path=wav_path)

    # Sequential: resampler needs chunk order (default)
    audio = decode_resample_buffer(
        raw_chunk=source["raw_chunk"],
        cmc_time=source["cmc_time"],
        rs=PARENT["rs"], buf=PARENT["buf"],
    )

    # Parallel: VAD infer + foreground detect independent
    infer = vad_infer(audio=audio["audio"].parallel(), onnx=PARENT["onnx"])
    fg = foreground_detect(audio=audio["audio"].parallel(), bg=PARENT["bg"])

    # Sequential: state machine needs order
    seg = speech_segmenter(prob=infer["prob"], fg=fg["is_fg"], ...)

    # Parallel: STT calls independent
    stt = triton_stt(audio=seg["speech_audio"].parallel())

    # Sequential: LLM workflow per-transcript
    workflow = educa_workflow(transcript=stt["transcript"], ...)

    # Collect all responses
    all_responses = report(responses=workflow["response"].collect())
```

### Simple streaming
```python
@graph
def pipeline(items):
    source = each_item(items=items)
    doubled = double(x=source["x"])           # sequential default
    result = summarize(all=doubled["y"].collect())  # collect into list
    START >> source >> doubled >> result >> END
```
