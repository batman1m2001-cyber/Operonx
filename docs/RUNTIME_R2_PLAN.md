# R2 — run identities, events, child executions, live traces

Status: **built, 2026-10-04** (branch `feat/r2-identity`, stacked on `feat/w1`); §5 records what was measured. Source: ROADMAP §2 R2;
`roadmap/track2_langgraph_gap.md` rows 12, 14, 16, 22, 23 and Phase 2; `roadmap/track3_agents.md` §4.7 K1/K2;
`AGENTS_V2_PLAN.md` §3 (K0, K1) and the A0 spike (`spike/child-exec`, `0e923b2`, `ae4b140`), used as a reference.

Every choice below is resolved. Nothing here gives an op body control flow: `RunContext` is information, `child()`
records, and both are no-ops for the scheduler. Nothing reopens `design/STATE_LOOP_REFACTOR_PLAN.md` §Rejected.

## 1. What exists today

| Concern | Today | Where |
|---|---|---|
| Run information in an op body | none: `SCRATCH`, the `ResourceHub`, ids on state | — |
| Invocation identity | `op_id = full_name#ctx` in the trace only; no key an op can use | `workflow_trace.make_op_id` |
| Interrupt id | `uuid4().hex` | `ops/flow/interrupt_op.py:106,109` |
| Stream modes | one per call: `updates`, `values`, `frames`, `custom`, `interrupts` | `engine.py::stream` |
| Op start/end events | none outside the trace, which is read at the end | — |
| Steps inside an op | invisible: an agent loop's model and tool calls have no record | `workflow_trace.py` has no parent/child API |
| Semantic attributes | none; GenAI fields are guessed from `outputs` | `consumers/langfuse.py` |
| When traces are written | once, when the run ends; a killed run has none | `engine.py` `_run` `finally` |
| Streamed LLM trace size | every yield record repeats the inputs: 1.17 MB for a 12 KB prompt | `consumers/local.py`, `runs/model.py`, `runs/clickhouse.py` |
| Tool dispatch | builds a `FuncOp` per call to read `.core`: ~2 ms, 97% `auto_name` + `getsource` | `agents/graphs/dispatch.py:318`, `func_op.py`, `utils/auto_name.py` |

## 2. The API

All new public names live in a new module, `operonx/core/runtime.py`, exported from `operonx` and `operonx.core`.

### 2.1 `RunContext`

```python
from operonx import run_context

@op
async def charge(order_id: str) -> dict:
    rc = run_context()                        # None outside a run's op
    await psp.charge(order_id, idempotency_key=rc.idempotency_key)
    return {"ok": True}

engine.start(inputs, session_id="cust-7", context=Tenant(id="acme"))
```

A frozen dataclass built on demand from a per-invocation frame, so an op that never asks pays nothing:

| Field | Value |
|---|---|
| `run_id` | the run's trace id (`trace_id=`, else `request_id`), the id every consumer files the run under |
| `thread_id` | the `session_id` the caller passed to `start()`, else `None` (R4 threads key on it) |
| `op_path` | the execution's full name (`graph.sub.op`; inside a child, `graph.sub.op.model`) |
| `ctx` | the execution's ctx tuple |
| `attempt` | the R1 attempt (1-based) |
| `deadline` | the attempt's `Timeout(run=)` deadline on the event-loop clock (`loop.time()`), else `None`; `remaining` is `deadline - loop.time()` |
| `context` | the object passed as `start(..., context=)` (also `run()` and `stream()`), else `None` |
| `idempotency_key` | `blake2b(json([run_id, op_path, ctx]), digest_size=16).hexdigest()`; `None` without a `run_id` |

- The key excludes the attempt, so a retried attempt reuses its key, which is what deduplication needs.
- Inside `child()`, `op_path`/`ctx`/`idempotency_key` are the child's, so each tool call has its own key.
- The frame replaces `_current_op_ctx`. Its readers (`InterruptOp`, `EmitOp`, `LLMOp`) read `frame.ctx`. It is restored
  by value, for the reason the R1 fix recorded (a generator closed by the finalizer runs in another context).
- `context` is stored as given. It is information: nothing in operonx reads it.

### 2.2 Deterministic interrupt ids

`interrupt_id = invocation_key(run_id, op_full_name, ctx)`, the same function as `idempotency_key`. Two interrupts in
one run differ by ctx (loops and fan-out give every invocation its own ctx). Consumers checked: `checkpoint/bridge.py`
and `AgentSession` pass the id through and resume by it within one state; serve and studio never read it. The id stays
32 hex characters.

### 2.3 Multi-mode stream and the `tasks` mode

```python
async for mode, chunk in engine.stream(inputs, mode=["updates", "tasks"]):
    ...
```

- `mode` a string: unchanged, yields chunks. A list: yields `(mode, chunk)` in arrival order. Unknown or repeated modes
  raise `ValueError` naming the valid ones: `updates`, `values`, `frames`, `custom`, `interrupts`, `tasks`.
- One implementation for every mode: one run, one pacing loop. Updates are released by completed step, as today; an
  `InterruptEvent` goes to `interrupts` when it is requested, else to `updates` (today's behaviour).
- `tasks` yields `TaskStarted(op, ctx, attempt)`, `TaskFinished(op, ctx, attempt, duration_ms)` and
  `TaskFailed(op, ctx, attempt, duration_ms, error, cancelled, retrying)`, per invocation (not per yield) and per
  child execution. A retried attempt gives `TaskFailed(retrying=True)` then `TaskStarted(attempt=n+1)`.
  - They are named after the mode, not `Op*` as the roadmap wrote them. R1 already ships `OpFailed` as the exception
    `errors="raise"` raises, and two public classes with one name would be read as one.
- Built where records are built: `BaseOp.run`, `_exec_with_policy`, `child()`. Events are constructed only while a
  `tasks` stream listens (`trace._task_listeners`), so the default path pays one list test.

### 2.4 Child executions (K1) and `attrs` (K2)

```python
from operonx import child

@op
async def agent(messages: list) -> dict:
    async with child("turn", inputs={"n": 0}, op_type="turn") as turn:
        async with child("model", inputs={"messages": messages}, op_type="llm") as call:
            reply = await model(messages)
            call.outputs = reply
            call.attrs["gen_ai.operation.name"] = "chat"
        turn.outputs = {"tool_calls": len(reply["tool_calls"])}
    return {"reply": reply}
```

- An async context manager only. A plain `def` op cannot use it, and a `bound="cpu"` body runs in a thread, where a
  record's position in `trace.nodes` could race.
- A child's ctx is its parent's plus `"<name>[<n>]"` (the n-th child of that name under that parent), its
  `op_full_name` is the parent's plus `".<name>"`, and its `op_id` follows `make_op_id`. The parent's id is derived
  from those, so no field is stored to link them.
- **Parent.** For a batch op, the op's record. For a generator, the yield record being produced when the child opens:
  ctx `base + ("[i]",)` (the spike hung it under `base`, where a non-transient generator has no record). For a
  transient generator, its one summary record at `base`. Inside a child, that child. In a `.parallel()` fan-out each
  item's invocation has its own frame, so its children land under its own ctx.
- **Retries.** Numbering restarts with each attempt, so keys stay stable. A child of attempt `n > 1` gets
  `op_id + "@n"` so ids stay unique. `build_tree` looks for the parent at `canonical@attempt` first, then
  `canonical`.
- **Filters.** The op's `@op(exclude=/include=)` trace filter applies to every child's inputs and outputs, so a key
  the op hides stays hidden in its steps.
- **Status.** An exception in the block is recorded as `error` (traceback) and re-raised; `CancelledError` as
  `cancelled`. The record carries the op's `attempt`.
- Outside a run (no frame or no trace), `child()` records nothing and costs one ContextVar read. Names with `.`, `[`,
  `]` or `#` are refused with a message naming the rule.
- **`OpExecution.attrs: dict`**, set through `ChildExecution.attrs`. It is for semantic attributes (`gen_ai.*`, tool
  names, usage).

### 2.5 Trace format: rows carry `attrs`, `attempt`, `inputs_from`

One row builder, `runs.model.row_of(node, trace)`, replaces the three copies (local consumer, `rows_of_trace`,
ClickHouse `build`). New keys, written only when they differ from the default, so old rows and new rows read alike:

- `attrs` (default `{}`) and `attempt` (default `1`; R1 recorded it in memory only).
- **`inputs_from`**: a generator invocation's records share one inputs dict in memory. The first record keeps
  `inputs`. Every later record of that invocation (later yields, a failure record, a transient summary) sets
  `OpExecution.inputs_from` to the first record's `op_id`, and the row omits `inputs`. Measured: a streamed 12 KB-prompt
  LLM call is 1.17 MB today.
- **Reading.** `RunRecord.__post_init__` resolves `inputs_from` by reference, so every store's `get_run`, and the studio
  reading through it, sees the inputs with no copy. Old traces carry no `inputs_from` and read unchanged.
- **Langfuse.** A referencing observation has no `input` and `metadata.inputs_from` (the scoped id), plus
  `metadata.attrs`/`attempt`. `records_of_langfuse_trace` maps both back.
- **ClickHouse.** Migration 3 adds `nodes.attrs String`, `nodes.inputs_from String`, `nodes.attempt UInt16 DEFAULT 1`
  (`ADD COLUMN IF NOT EXISTS`, idempotent).
- **`view.txt`.** A referencing record prints `in  (as <op_id>)`.
- **In memory.** `node.inputs` still holds the shared dict, so `handle.trace` readers are unchanged.

### 2.6 Live traces

- `Consumer.on_start(trace)` and `Consumer.on_execution(trace, execution)`, both optional no-ops, next to `consume`.
  They are called on the event loop, right after the run starts and right after each record is appended (its index is
  `len(trace.nodes) - 1`), so they must queue and return. A consumer that overrides either is *live*. The engine wires
  live consumers to the trace, and one that raises is logged and never affects the run. `WorkflowTrace.record(node)`
  is the single append path.
- **ClickHouse** queues live items on a second bounded `BackgroundWriter` (`live_writer`, 100k items). A burst of
  executions then can never crowd a finished run out of the main queue, whose capacity semantics stay as they were.
  - Each batch writes a running `runs` row per run (with the count of executions landed so far) at `written_at` =
    1970, version 0. The final row replaces it whichever lands first.
  - Each record becomes a `nodes` row at its final `seq`. The final insert rewrites the same keys with the same
    values, and `ReplacingMergeTree` collapses them.
  - The two writers take turns (a store lock), so a blob both reference is put once.
- **SQL (SQLite, Postgres)**: a shared base `SqlRunStore` (the two stores are the same code with a different driver)
  gets a `BackgroundWriter` for live items and a table `live (trace_id, seq, row)`.
  - `on_start` inserts the summary row with `status="running"` unless the run is already stored.
  - Nodes are inserted in batches, skipping a run already finished. The running summary's `executions` is set from
    its live rows.
  - `consume` flushes that writer, then `put_trace` writes the record and deletes the run's `live` rows.
  - `get_run` on a running run builds its record from `live` with `status="running"`.
- **`live=False`** on the ClickHouse and SQL stores (and `live: false` in `resources.yaml`) writes finished runs
  only: a service whose writer thread must not do the extra serialisation can turn it off.
- **Not live in R2:** the files store/`LocalConsumer` (atomic directory rename), Mongo, Langfuse. Each keeps writing
  at the end.
- **"abandoned" is not derived.** Telling a dead writer from a slow op needs a lease or heartbeat, which R3's journal
  (`claim`, `lease_s`) brings. A killed run stays listed as `running` with the nodes it completed.

### 2.7 K0: tool dispatch cost, at its root

Measured (`prof_dispatch.py`, 300 calls): 1.98 ms per `execute`. Of that, `FuncOp.__init__` takes 1.5 ms re-parsing
the tool's source (`inspect.getsource` three times, `ast.parse` twice) and 1.1 ms in `auto_name` disassembling the
whole caller's bytecode, on every construction. The profiler adds overhead, which is why the parts sum to more than
the total.
- `FuncOp`: the signature/AST analysis is a pure function of the function object, so it is memoised per function
  (`WeakKeyDictionary`). Each op gets fresh `Param`s copied from the cached schema.
- `auto_name`: the answer for `(code object, f_lasti)` never changes, so it is memoised (`lru_cache`, bounded).
- `dispatch.execute` calls the tool's function (`factory.__wrapped__`). It built an op only to call `.core`, which is
  the raw function, so the op bought no tracing, and its shorthand parsing could misread a tool argument named
  `name`, `bound` or `delay`.

## 3. Tests (each fails before its change)

| Test | Asserts |
|---|---|
| `test_run_context_fields` | run_id, thread_id (given/None), op_path, ctx, attempt, context; None outside a run |
| `test_idempotency_key_stable_across_retries_and_runs_differ` | same key on attempts 1–2; another run_id → another key; a child gets its own |
| `test_run_context_deadline` | `Timeout(run=)` → deadline ≈ loop.time()+run; none → None |
| `test_interrupt_id_deterministic` | id equals `invocation_key(run_id, full_name, ctx)`; resume still works |
| `test_stream_multi_mode`, `test_stream_tasks_mode`, `test_stream_rejects_unknown_mode` | `(mode, chunk)` pairs; `Task*` events with attempt; a retry gives `retrying` |
| `test_child_nesting`, `test_child_error_and_cancel`, `test_child_untraced_noop`, `test_child_name_rejected` | K1 records |
| `test_child_in_generator_hangs_under_yield_record`, `test_child_in_parallel_fan_out`, `test_child_exclude_honoured`, `test_child_retry_ids_unique` | K1 placement |
| `test_children_and_attrs_round_trip[local,sqlite,postgres,clickhouse,mongo,langfuse]` | rows and the `build_tree` nesting survive every store |
| `test_yield_records_reference_inputs`, `test_old_rows_without_inputs_from_read` | trace size fix; old traces |
| `test_trace_visible_while_running[sqlite,postgres,clickhouse]` | running status and finished nodes while an op is blocked |
| `test_killed_run_leaves_partial_trace[sqlite,clickhouse]` | subprocess + SIGKILL: run listed `running`, with its completed nodes |
| `test_dispatch_builds_no_op`, `test_func_op_parses_source_once`, `test_auto_name_disassembles_once_per_site` | K0 |

Postgres and ClickHouse run against throwaway containers (`OPERONX_TEST_PG_DSN`, `OPERONX_TEST_CLICKHOUSE`) and skip
otherwise. Plus a guide page, `operonx/guide/08-runs.md` (every snippet runs in `tests/guide`). Before and after:
the W0 `bench_stream.py` (nothing enabled; within noise), trace bytes from W1's `measure_c15.py`, and the callbot suite
against this branch.

## 4. Studio

The studio reads every run through a store's `get_run`, so `inputs_from` needs no studio change, and `build_tree`
already nests children. On `feat/r2-trace`: `_tree_records` passes `attempt` (to place children of retried attempts)
and `attrs` (for the inspector), and a `running` run gets its own status dot and filter option instead of reading as
succeeded.

## 5. Built — what was measured

- **Trace size** (W1's `measure_c15.py`, a streamed `LLMOp` on a 12 KB RAG prompt, local consumer): 1,171,842 →
  69,429 bytes for the run (`nodes.jsonl` 1,150,478 → 55,931). The batch call is unchanged (27.1 KB).
- **K0**: `dispatch.execute` 1.98 → 0.009 ms per tool call. Building an op from a `@tool` factory 1.52 → 0.08 ms.
- **Stream benchmark** (W0 `bench_stream.py`, n=5000, nothing enabled, base `feat/w1` and this branch alternated three
  times, load average ~11 on a shared host), mean ms base → branch: run flat chain 2624 → 2681 (+2.2%), stream updates
  3086 → 2987 (−3.2%), subgraph per item 2533 → 2573 (+1.6%), collect 2155 → 2251 (+4.5%). Within noise both ways.
- **Callbot** (`refactor/operonx-studio`, `-m "not live and not integration"`, `PYTHONPATH` = this branch): 303 passed,
  2 skipped. Base was the same.
- **Studio** (`feat/r2-trace` against this branch): 636 passed, 4 skipped, 1 failed. The failure is
  `test_scaffold::test_builds_offline_and_extracts`, and it fails on `feat/w1` too: the scaffold template reads an
  output that W0's output-key check refuses. Screenshots: Runs list, running run, agent run with nested children;
  desktop and phone, light and dark.
