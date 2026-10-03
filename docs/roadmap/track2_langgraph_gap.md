# Track 2: LangGraph vs OperonX capability audit

Date: 2026-10-04. Sources read:
- **LangGraph**: `git clone --depth 1` of `langchain-ai/langgraph` (langgraph 1.2.12) into a scratch directory. Citations of the form `LG:<path>:<line>` are relative to the LangGraph repo's `libs/`.
- **OperonX**: `/home/thanglq/Operon` at `a21082d` (operonx 1.14.0). Citations of the form `OX:<path>:<line>` are relative to that repo.

Every OperonX claim below was checked against the source, a probe run, or both. The probe scripts are in `docs/roadmap/evidence/probes/`, and their exact output is in Appendix A. Probes `P*`/`OX*` ran with `uv run` from `/home/thanglq/Operon`. Probes `LG*` ran against the cloned LangGraph, installed editable into a throwaway venv.

---

## (a) How each engine works

### LangGraph: Pregel supersteps over versioned channels

- **State** is a set of *channels*: `LastValue`, `BinaryOperatorAggregate` (a reducer), `Topic`, `EphemeralValue`, `NamedBarrierValue`, `UntrackedValue`, and `DeltaChannel` (beta). See `LG:langgraph/langgraph/channels/*.py`. `StateGraph` compiles a TypedDict or Pydantic schema into channels (`LG:langgraph/langgraph/graph/state.py:1815`). Every channel has a monotonically increasing version, and every node records the versions it has seen in `versions_seen` (`LG:checkpoint/langgraph/checkpoint/base/__init__.py:93-139`).
- **Execution** is a loop of *supersteps*, `PregelLoop.tick` → `PregelRunner.tick` → `PregelLoop.after_tick` (`LG:langgraph/langgraph/pregel/_loop.py:609,693`).
  - `prepare_next_tasks` picks every node whose trigger channels changed since that node last ran (`LG:langgraph/langgraph/pregel/_algo.py:392`).
  - All the picked tasks run concurrently.
  - Their writes are buffered. At the barrier, `apply_writes` applies them **sorted by task path**, so the result is deterministic whatever order the tasks finished in (`_algo.py:232-256`).
  - A checkpoint is written per superstep (`_loop.py:743`).
- **Writes from concurrent nodes.** If two nodes write a `LastValue` channel in the same step, LangGraph raises `InvalidUpdateError` (`LG:langgraph/langgraph/channels/last_value.py:64`; probe LG1). Reducer results do not depend on which node finished first (probe LG2: `['a','b']` even though `a` was the slower node).
- **Durability.** Each task's writes are persisted on completion as "pending writes" (`put_writes`, `_loop.py:425-518`).
  - Task ids are deterministic hashes of checkpoint id, namespace, step, node name and path (`_algo.py:550,616`).
  - On resume, tasks that already succeeded have their saved writes re-applied instead of being re-run (`_loop.py:767-778`). Only the tasks that failed or were interrupted run again.
  - `durability = "sync" | "async" | "exit"` sets when checkpoints are flushed (`LG:langgraph/langgraph/types.py:98`).
- **Human in the loop.** `interrupt(value)` raises `GraphInterrupt` from inside a node (`types.py:893-1035`). On resume the node runs again from its first line, and resume values are matched by the order of the `interrupt()` calls (`types.py:1009-1026`). Resuming needs a checkpointer and a `thread_id`, and it works across processes (probe LG5).
- **Functional API.** `@entrypoint` and `@task` run on the same loop. A task's result is a pending write, so a task that already completed is not re-run on resume (`LG:langgraph/langgraph/func/__init__.py:110,262`; probe LG6).
- **Failure.** A task that raises cancels its sibling tasks, and the run raises (`LG:langgraph/langgraph/pregel/_runner.py:618-700`). This can be changed in three ways:
  - `RetryPolicy` (`types.py:427`);
  - `TimeoutPolicy` with run and idle timeouts plus `runtime.heartbeat()` (`types.py:461`, `pregel/_retry.py:128-370`);
  - per-node or default `error_handler` nodes (`graph/state.py:272-335,677`, `_runner.py:172`).
- **Platform.** Threads, runs, assistants, crons, double-texting (`MultitaskStrategy = reject|interrupt|rollback|enqueue`), `on_disconnect`, completion webhooks, and `join_stream(last_event_id)` reconnection all exist as SDK contracts (`LG:sdk-py/langgraph_sdk/schema.py:23,74,81`, `_async/runs.py:74-195,1097`, `_async/cron.py:62`). The server that implements them is **not** in this repo: `langgraph-cli[inmem]` pulls in the separate `langgraph-api` package (`LG:cli/pyproject.toml:25-27`).

### OperonX: an event-driven dataflow scheduler over `(op, var, ctx)` cells

- **State** lives in `MemoryState` cells keyed by `(op_full_name, var, context)` (`OX:docs/architecture/state-model.md`). Graph nesting goes into the name (`outer.sub.c`). Iteration goes into the context tuple:
  - `("main","[2]")` for the third item a generator yields;
  - `("main","g.__loop_0__#1")` for the second iteration of a loop.
- **Shared cells.** `PARENT.declare(x=..., reducers={...})` creates a shared cell (`OX:operonx/core/ops/_edges.py:58`). Reducers are applied **eagerly on each write**, in whatever order the writes arrive (`OX:operonx/core/states/state.py:424-435`).
- **Execution.** There is no superstep. `Scheduler._run_once` (`OX:operonx/core/ops/graph/task_scheduler.py:352`) works as follows:
  - `dispatch` either runs an op inline (`bound="sync"`) or spawns a `_pump` task (`:505-519`). Each pump holds the per-graph semaphore `concurrency` (default 64; `:498`, `graph_op.py:111`).
  - `_pump` turns each item the op yields into a `Frame` event, and the end of the op into an `EOF` event (`:521-625`).
  - The main loop routes frames immediately: it decrements the target ops' ready counts and dispatches each op when its last hard edge (or first soft edge) arrives.
  - A successor starts as soon as *its own* predecessors finish (probe OX7: 0.019 s, against LangGraph's 0.509 s in LG7).
- **Streaming is native.** A generator op runs its downstream ops once per yield. Each edge has its own policy: sequential (the default), `.parallel(max=N)`, or `.collect()`. Edges can be bounded with `max_pending` and `on_full="wait"|"drop_oldest"` (`task_scheduler.py:183-213,626-680`). `@op(transient=True)` frees each item's cells once the item is consumed.
- **Loops.** Back-edges are rewritten at build time into a hidden synthetic loop op (`OX:operonx/core/ops/graph/cycle_rewrite.py`). The loop stops after an iteration in which no back-edge fired, and is capped at 1000 iterations (`task_scheduler.py:21-30`, `cycle_rewrite.py:407`, `_end_iteration` at `:1012-1057`).
- **Failure.** An op that raises does **not** raise:
  - `BaseOp.run` catches the exception, records it in `$errors` and `handle.errors`, and emits no frame (`OX:operonx/core/ops/base.py:1306-1328`);
  - every op downstream of it simply never runs (probe P1);
  - no retry and no timeout exist at the op level (`@op` signature: `OX:operonx/core/ops/transform/func_op.py:29-41`).
- **Checkpointing is an observer only.** `Checkpointer` is a protocol that receives `CellWriteEvent`s and supports `get_state(step)` and `get_updates(step)` (`OX:operonx/checkpoint/base.py:146-203`). The only implementation is `InMemoryCheckpointer` (`OX:operonx/checkpoint/memory.py:18`). Neither `Operon.start`/`run` nor anything else can start a run *from* a checkpoint (probe P5).
- **HITL.** `InterruptOp` is a visible graph node that awaits an in-process `asyncio.Future` (`OX:operonx/core/ops/flow/interrupt_op.py:98-150`), and `state.resume_interrupt` resolves it (`state.py:382`). If the process or task dies, the interrupt is gone, and a rerun repeats every side effect (probe P4).
- **App layer.**
  - `Job`: per-item runs, resume by key, `on_error=retry:N`, `item_timeout` (`OX:operonx/app/jobs/runner.py:56-85,277`).
  - `Runbook`.
  - `Service`: http, websocket, webhook (`202`) and schedule transports (`OX:operonx/app/serve/triggers.py:48,99`), with a whole-run timeout (`OX:operonx/app/serve/runner.py:25-103`).
  - `Application` and `operonx.toml`.
  - Trace sinks for local, Langfuse, ClickHouse, SQL and Mongo. These are written when a run finishes (`OX:operonx/core/engine.py:721`, `OX:operonx/telemetry/runs/base.py:54-68`).

**One-line contrast:** LangGraph is built to be *durable and deterministic*, and pays for it in latency at every superstep barrier. OperonX is built for *low latency and streaming*, and has no durability, no retries or timeouts, and does not order concurrent writes deterministically.

---

## (b) Gap table

Importance is judged for "a production AI workflow foundation": agents, long multi-step jobs, and HITL, not only the live callbot.

| # | Area | LangGraph (how, file:line) | OperonX today (how, file:line or verified absent) | Missing capability | Importance | Recommendation |
|---|---|---|---|---|---|---|
| 1 | Runtime model | Supersteps with a barrier: writes are applied only in `after_tick` (`pregel/_loop.py:693-743`). | Dataflow events; successors start as soon as their own predecessors finish (`task_scheduler.py:505-625`); probe OX7 0.019 s vs LG7 0.509 s. | None. OperonX's model is the better one for latency and streaming. | n/a | **Avoid** the barrier (see c1). |
| 2 | Concurrent writes, last-write-wins | `LastValue` raises `InvalidUpdateError` on 2+ writes in a step (`channels/last_value.py:64`; LG1). | Last writer by wall-clock time wins, silently (P2: `slow` in one run, `fast` in the other, no `$errors`). | Detecting concurrent writers to a non-reducer cell. | **high** (heisenbugs) | **Adopt-simpler**: a *build-time* lint. Two writers with no path between them in the DAG and no reducer is a `BuildError`, with `allow_race=True` to opt out. No barrier needed. |
| 3 | Reducer order | Deterministic: writes are sorted by task path before reducing (`_algo.py:256`; LG2). | Order of completion (P2: `['fast','slow']` vs `['slow','fast']`). | Order-independent results. | med | **Adopt-simpler**: document that reducers on concurrent writers must be commutative; add the `reducers.ordered(fn, key=)` helper, which sorts by a stable key at read time. Replay (row 11) stores the values after the reducer ran, so recovery is exact either way. |
| 4 | Channel kinds | `Topic`, `EphemeralValue`, `NamedBarrierValue` (`defer`), `UntrackedValue`, `Overwrite` (`types.py:1039`). | Shared cell, reducer, `transient` port, `SCRATCH` (an untracked side dict). Barriers come from hard edges. | `Overwrite` (bypass the reducer once), sketched below. | low | **Adopt-simpler** `Overwrite`. Avoid copying the rest: edges and `.collect()` already cover them. |
| 5 | Retries | `RetryPolicy(initial_interval, backoff, max_interval, max_attempts, jitter, retry_on)` per node, with a graph default (`types.py:427`, `_retry.py:573`; LG4: 3 attempts). | **Absent** in core: `@op` has no retry (`func_op.py:29-41`); P1 shows 1 attempt. Exists only per job item (`runner.py:56`) and inside `LLMOp(max_retries)` for parse retries. | Retry on transient errors per op, with backoff and an attempt number. | **critical** | **Adopt.** `@op(retry=Retry(...))`. A generator op is retried only if it fails before its first yield. |
| 6 | Timeouts | `TimeoutPolicy(run_timeout, idle_timeout, refresh_on)` plus `runtime.heartbeat()`; `NodeTimeoutError` feeds the retry policy (`types.py:461`, `_retry.py:128-370`, `errors.py:190`). | **Absent** per op. Whole-run deadline only, and only in the serve layer and jobs (`serve/runner.py:96-103`, `job.py:110`). `InterruptOp(timeout=)` only. | A per-op wall-clock deadline (and idle deadline for streams) that feeds retry. | **critical** | **Adopt** `run_timeout`. **Adopt-simpler** idle timeout: for generators, refreshed on each yield. Skip callback-driven refresh (it depends on LangChain). |
| 7 | Error semantics | Fail fast: a failure cancels siblings and the run raises (`_runner.py:618-700`). Error-handler nodes per node or by default (`graph/state.py:272-335,677`). | Record and continue (`base.py:1306`). Downstream silently never runs (P1). Errors are visible only in `$errors`. No error routing. | (a) Opt-in fail-fast for jobs, tests and batch work; (b) error edges to a handler op. | **high** | **Adopt** both. Keep record-and-continue as the default for live sessions, which is a deliberate choice (`execution-flow.md`, "An op that raises"). |
| 8 | Loop limit | `recursion_limit` (default 10007, `_internal/_config.py:32`) raises `GraphRecursionError` (`main.py:2999`; LG3). | Hard cap of 1000 that is **silent**: P3 ran 1000 iterations, returned normally, `$errors` was `None`. | A loud loop limit, configurable per loop. | **high** (contradicts `failure-modes.md` §1) | **Adopt**: record `LoopLimitExceeded` in `$errors` (raise under fail-fast); `if_(..., max_iterations=N)`. |
| 9 | Cancellation and drain | `RunControl.request_drain()` stops at a superstep boundary in a resumable state (`runtime.py:79-104`, `_loop.py:667`). Task cancellation is recorded as an `ERROR` write (`_runner.py:583`). | `handle.cancel()` (`engine.py:371`); `Interrupt` context sweep (`task_scheduler.py:1088`); the serve layer drains on shutdown. A `bound="cpu"` thread keeps running after cancel (P9; LangGraph has the same Python limit). | A drain that leaves the run *resumable*. | med (high once row 11 exists) | **Adopt** with row 11: `handle.drain()`, meaning no new dispatches, wait for in-flight ops, then status `drained`. |
| 10 | Checkpoint backends | `BaseCheckpointSaver` (`checkpoint/base/__init__.py:177`) with InMemory, SQLite (`checkpoint-sqlite/.../__init__.py:45`), Postgres, async Postgres and shallow Postgres (`checkpoint-postgres/.../postgres/{__init__,aio,shallow}.py`); `copy_thread`, `prune`, `delete_thread` (`:321-415`); encrypted serde (`serde/encrypted.py:8`). | `InMemoryCheckpointer` only (`checkpoint/memory.py:18`). The SQLite backend was deferred "when persistence is a real ask" (`docs/design/STATE_LOOP_REFACTOR_PLAN.md:757`). `RunStore` backends store **finished traces** only (`telemetry/runs/base.py:62-68`). | A persistent store of run state. | **critical** | **Adopt-simpler**: an append-only *journal* (see e3), not snapshots per step. |
| 11 | Durable execution and crash recovery | Pending writes per task plus a checkpoint per step; on resume, writes from tasks that already succeeded are re-applied (`_loop.py:425,743,767`; LG5 across processes). | **Absent.** A dead process loses the run. Recovery means rerunning from the start (P4: side effect executed twice). | Resuming a run after a crash without re-running ops that completed. | **critical** for agents, long jobs and HITL; low for a live call | **Adopt-simpler**: replay the scheduler's journal of `Frame`/`EOF` events, keyed by `(op_full_name, ctx)`, which are already deterministic paths (e3). |
| 12 | Idempotency, task results on resume | Task id = hash(checkpoint, namespace, step, name, path) (`_algo.py:550,616,834`). A completed `@task` is not re-run (LG6: 1 execution). | Absent. There is no stable invocation id; the `interrupt_id` is `uuid4` (`interrupt_op.py:107`). | A stable invocation key, so completed ops are skipped on replay and external APIs get an idempotency key. | **critical** (with row 11) | **Adopt.** `key = (run_id, op_full_name, ctx)`; exposed as `RunContext.idempotency_key`. |
| 13 | Durable HITL | `interrupt()` plus `Command(resume=...)`, which survives a restart (LG5); resume map keyed by interrupt id (`_loop.py:930-950`). | In-memory future. After cancel, `resume_interrupt` returns `False` and the interrupt is gone (P4). | An interrupt that survives the process, and a run that is parked rather than holding a coroutine. | **critical** for approvals | **Adopt** through the journal. **Keep** the visible `InterruptOp` node; avoid copying the in-body `interrupt()` (c3). |
| 14 | Interrupt surfaced in the stream | `__interrupt__` appears in `values`/`updates` output (`types.py:131`; `StateSnapshot.interrupts`, `types.py:711`). | **Broken.** The `InterruptOp` docstring says `engine.stream(mode="updates")` yields `InterruptEvent` (`interrupt_op.py:24-28`). Probe P4: the stream blocked with no `InterruptEvent`. Only `bind_interrupt_bus` delivers it (`checkpoint/bridge.py:240`, used by `AgentSession`). | Interrupts as stream events. | **high** (a documented API that does nothing) | **Adopt**: `mode="interrupts"`, and include interrupts in multi-mode output. Fix the docstring. |
| 15 | History, time travel, `update_state` | `get_state`, `get_state_history`, `update_state`, `bulk_update_state` (`pregel/main.py:1457,1507,1612,2514`); forking from a `checkpoint_id`. | Inspection of a finished run only (`InMemoryCheckpointer.get_state(step)`). No fork, no edit-and-resume (P5). | Fork from step N with patched state. | med | **Adopt-simpler, later** (Phase 5): fork = copy a journal prefix plus patch records. |
| 16 | Stream modes | `values`, `updates`, `checkpoints`, `tasks`, `debug`, `messages`, `custom`; several modes at once; `subgraphs=True` namespaces; `stream_events` v3 (`types.py:131`, `main.py:2645-2706,3601`). | One mode per call: `updates`, `values`, `frames`, `custom` (`engine.py:810-1028`). LLM tokens arrive through `updates`. Nested ops appear by dotted full name (P8: `seen.s.l`). | Several modes at once; a `tasks` mode (op start/end/error); `interrupts`; resumable streams. | med-high | **Adopt-simpler**: `mode=[...]` yields `(mode, chunk)`; add `tasks` and `interrupts`. **Avoid** the 7 modes plus v1/v2/v3 sprawl. |
| 17 | Subgraphs | Checkpoint namespaces per subgraph; per-subgraph `checkpointer=True/False/None` (`types.py:108`); `Command(graph=PARENT)`; state of a nested subgraph via `get_state(subgraphs=True)`. | A `@graph` called inside another graph is an op. Build-time hermeticity check (`graph_op.py:619`). Write-up through `PARENT`. Its own scheduler and semaphore (`state-model.md`). | Inspecting subgraph state mid-run; subgraph-scoped durability. | low-med | Covered by the journal: the key already contains the dotted path. No new API. |
| 18 | Dynamic routing and fan-out | `Send(node, arg)` map-reduce with per-task state (`types.py:738`); `Command(goto=..., update=...)` (`types.py:833`). | A generator op plus `.parallel(max=N)`/`.collect()` gives dynamic-width fan-out. `if_`/`.else_` gives static routing. `Command(goto)` was rejected on purpose (`STATE_LOOP_REFACTOR_PLAN.md:853`). | Routing to a target chosen at run time from an unbounded set. | low | **Avoid** `goto`. Fan-out by generator is as expressive as `Send` and streams. |
| 19 | Concurrency limits | `max_concurrency` in config; thread or async executors (`pregel/_executor.py`). | A semaphore per graph. Nested graphs **multiply** (P10: concurrency=2 in both outer and inner gave 4 leaf ops at once). No per-resource rate limit. | A global (engine- or process-wide) cap, and per-resource QPS caps. | **high** (LLM rate limits, cost) | **Adopt-simpler**: one shared limiter through all nested schedulers, plus a `rate_limit:` key in `resources.yaml`. |
| 20 | Node caching | `CachePolicy(key_func, ttl)` with a pluggable `BaseCache` (memory, redis) (`types.py:530`, `_loop.py:1578`, `checkpoint/langgraph/cache/`). | `@op(cache=True|path)`: a class-level dict keyed by **`op.full_name`**, an FNV hash of `json.dumps(default=str)`, no TTL, no bound (`base.py:325,850-871,1219`). **Bug P6:** two different graphs whose engine variable and op variable share names return each other's cached results (`gb` returned `A7`). | Correct key isolation, TTL and bounds, and a shared backend. | **high** (wrong results) | **Fix** now. Key on graph fingerprint, op qualname and code hash; add `CachePolicy(ttl, key=)`; use `blake2b`; add a Redis backend later. |
| 21 | Long-term store | `BaseStore`: namespaced KV with search, TTL and vector index, and Postgres store (`checkpoint/langgraph/store/base/__init__.py:708`, `checkpoint-postgres/.../store/postgres/base.py:667`); injected through `Runtime.store`. | `MemoryProvider` ABC with only `LocalMarkdownMemory` (`agents/memory.py:60,112`). Vector and doc stores are retrieval ops (`providers/vector_stores/*`). | A namespaced, cross-run KV and memory store. | med | **Adopt-simpler**: a `store:` resource plus `StoreGet`/`StorePut`/`StoreSearch` ops (visible nodes), on sqlite or postgres with pgvector. |
| 22 | Runtime context and DI | `Runtime(context, store, stream_writer, heartbeat, execution_info{attempt, task_id, run_id}, control)` (`runtime.py:125-293`). | Per-run `SCRATCH` dict, the `ResourceHub` singleton, and `user_id`/`session_id`/`request_id` on state. No typed context, no attempt or run info for op bodies. | Read-only run information (run id, op path, ctx, attempt, idempotency key) and a typed `context`. | med | **Adopt-simpler**: read-only `RunContext` contextvar. Control flow stays as visible nodes. |
| 23 | Observability | LangSmith callbacks; `TracePolicy(process_inputs/outputs)` (`types.py:542`); `debug`/`tasks` streams. | Built-in context-tree trace (`workflow_trace.py:107,171`), one record per yield, `include`/`exclude` per channel, `observe_max` circuit breaker, sinks for local, Langfuse, ClickHouse, SQL and Mongo. **But** traces are written only at the end of a run (`engine.py:721`), so a crashed or long run has no trace. | Live, incremental traces; a `running` status. | med-high | **Adopt-simpler**: stream `OpExecution` to sinks as ops complete (the journal can be that stream). OperonX is otherwise ahead here. |
| 24 | Serialization and encryption | `JsonPlusSerializer`/msgpack, strict allowlist, `EncryptedSerializer` (`checkpoint/serde/*`). | Traces are JSON with an orjson `default` (`telemetry/media.py`). No state serde, because nothing is persisted. | A safe serde for journaled values (no pickle), with optional encryption. | high (with row 11) | **Adopt-simpler**: msgpack or orjson with a type registry, `Media` offload (exists), optional AES. **Avoid** pickle. |
| 25 | Threads and sessions | `thread_id`: a checkpoint lineage across runs; conversation memory persists. | `session_id` is only a label (`engine.py:571-589`). `AgentSession` keeps messages in memory (`agents/session.py:64`). | A persistent thread: a sequence of runs sharing state. | high for agents | **Adopt-simpler**: `thread_id` = a journal chain plus declared cells carried between runs (`carry=[...]`). |
| 26 | Platform: task queue, runs, cron, double-texting, webhooks, reconnect | Runs and threads API; `MultitaskStrategy` (`schema.py:81`); `on_disconnect` (`:74`); crons (`_async/cron.py:62`); completion webhooks; `join_stream(last_event_id)` (`_async/runs.py:1097`). The server is commercial and not in the repo. | Services (http, ws, webhook, schedule), Jobs (resume by key), Runbook, Application, CLI. A webhook's `202` events are held **in memory** (`triggers.py:48-70`). `schedule` runs in-process with no lease (no leader election anywhere under `operonx/app`). No double-texting policy per thread. | A durable run queue, and cron with a lease. Also per-thread multitask policy, stream reconnect, and completion callbacks. | **high** for multi-replica production | **Adopt-simpler**: a Postgres `runs` table with `SKIP LOCKED` workers. Reuse the transports. |
| 27 | Graph versioning | `_migrate_checkpoint` on compiled graphs (`graph/state.py:1626`). | n/a (nothing is persisted). `graph.serialize()` exists (`graph_op.py:824`). | Refusing to resume against a changed graph. | high (with row 11) | **Adopt-simpler**: store a fingerprint from `serialize()` in `RunStarted`; resuming against a different fingerprint raises unless `allow_graph_change=True`. |
| 28 | Testing and DX | Unit-test nodes as plain functions; checkpoint-conformance suite (`libs/checkpoint-conformance`). | Ops are callable (`add(a=2)()`); every guide snippet is executed by `tests/guide/test_guide_snippets.py`; `scratch_active` (`core/testing.py`); about 2935 tests. | A conformance suite for journal backends; crash and replay test harnesses. | med | **Adopt**: a shared journal contract suite (the pattern exists in the `RunStore` contract tests). |
| 29 | Backpressure | None on node outputs. | `max_pending` with `wait`/`drop_oldest`, transient ports (`task_scheduler.py:626-680`, guide 03). | — (OperonX ahead) | n/a | Keep. |

---

## (c) Things LangGraph does that OperonX should explicitly NOT copy

1. **The superstep barrier.** Every successor waits for the slowest task in its step. Measured: LG7 started the dependent node at 0.509 s, OX7 at 0.019 s, on the same 10 ms vs 500 ms branches. OperonX's own design log already rejects it (`STATE_LOOP_REFACTOR_PLAN.md:735`, "Full Pregel scheduler rewrite"). Determinism can be had without a barrier: lint at build time (row 2), and recover by replaying journaled post-reducer values (row 11).
2. **`Command(goto=...)` and routing from inside a node body.** It hides control flow from the graph. Rejected in `STATE_LOOP_REFACTOR_PLAN.md:853`, and that rejection still holds. A generator plus `.parallel()` covers `Send`.
3. **`interrupt()` called inside a node body, with "re-run the node from the top" resume.** LangGraph matches resume values by the *order* of `interrupt()` calls within a task (`types.py:1009-1026`) and re-executes all code before the interrupt. That is fragile under refactoring and repeats side effects. Make the visible `InterruptOp` durable instead.
4. **The `RunnableConfig["configurable"]` dict-packing and the LangChain `Runnable` and callbacks coupling.** LangGraph's own code flags this: `runtime.py:306` (in `get_runtime`) has a TODO, "in an ideal world, we would have a context manager for the runtime that's independent of the config". Use a plain contextvar.
5. **Snapshot-per-superstep storage, and the delta-channel complexity it forced.** `DeltaChannel` is beta, and `copy_thread`/`prune` need long caveats so they don't silently corrupt state (`checkpoint/base/__init__.py:351-415`, `channels/delta.py:25-60`). An append-only journal plus periodic compaction is simpler and fits event-driven execution.
6. **Stream-mode sprawl.** Seven modes, `debug` that duplicates two of them, `version="v1"|"v2"`, and `stream_events` v3 with transformers (`main.py:2645-2706,3491-3729`). Keep OperonX's four modes, plus `tasks` and `interrupts`, and allow several at once.
7. **A pickle-based default cache key** (`types.py:530`: "Defaults to hashing the input with pickle"). It is unstable across versions and unsafe to share across trust boundaries.
8. **A global step count as the loop guard** (`recursion_limit` counts supersteps for the whole graph, default 10007). OperonX's cap per loop is the better shape; it just has to be loud (row 8).
9. **Platform features locked in a closed server.** The run queue, threads and cron are only SDK contracts in the OSS repo. OperonX should keep its app layer open source and self-hostable (Postgres plus workers).

---

## (d) Things OperonX already does better

1. **Latency.** Ops start when their own data is ready, with no step barrier (OX7 vs LG7, about 25x on that shape).
2. **Real streaming between ops.** Downstream ops run per item and in order (`.sequential` by default), with `.parallel(max=N)`, `.collect()`, **backpressure** (`max_pending`, `drop_oldest`) and transient ports that keep memory flat over a long stream (`task_scheduler.py:183-213,626-680`). LangGraph can only stream *to the client*. A node's outputs reach the next node only at the barrier.
3. **Build-time validation.** Refs are resolved, edge endpoints and subgraph hermeticity are checked, branch merges are auto-softened, and misused `and`/`or` on a Ref raises (`graph_op.py:291-650`, guide 04). LangGraph finds state-key typos only at run time.
4. **Contexts are deterministic paths:** `("main","[i]")` for items and `#n` for loop iterations (`execution-flow.md` "Contexts"). This is exactly the identity a journal or idempotency key needs. LangGraph has to synthesize one with xxhash over checkpoint, namespace, step and path.
5. **HITL and emit are visible graph nodes** (`InterruptOp`, `EmitOp`), so they show in the graph and traces and obey the `include`/`exclude` filters.
6. **Observability built in.** Per-yield records with upstream refs, per-channel `include`/`exclude`, the `observe_max` circuit breaker, and several sinks (ClickHouse with media, Langfuse, local) without vendor lock-in (`docs/architecture/observability.md`, `telemetry/runs/*`).
7. **An integrated, open app layer.** `Job` (resume by key, `retry:N`, `item_timeout`, records), `Runbook`, `Service` (http, ws, webhook, schedule), `Application`, `operonx.toml`, `operonx init`, and a studio. LangGraph's equivalents live in a commercial server.
8. **A tested agent guide and a culture of documenting failures.** Every guide snippet runs in CI (`tests/guide/`), and `docs/architecture/failure-modes.md` makes "a plausible value instead of an error" an explicit design smell.
9. **Declarative resources** (`resources.yaml` plus `ResourceHub`) with warm-up at engine construction, so a misconfigured resource fails at `Operon(...)` rather than mid-run.

---

## (e) Core-architecture refactoring recommendations

These are ordered so that each phase can ship and be measured on its own. They follow the repo's rules: falsifiable tests first, measure before and after, and no silent defaults.

### Phase 0: correctness bugs found by this audit (small; do first)

| Fix | Where | Test that proves it (fails today) |
|---|---|---|
| Cache key isolation (P6) | `base.py:850-871`: key = `(graph_fingerprint, op.__wrapped__.__qualname__, code_hash, input_hash)`; `hashlib.blake2b` over orjson with a strict default (no `default=str`); `BaseOp._cache_stores` becomes an LRU with a size bound. | `test_cache_isolated_across_graphs_with_same_names`: the P6 script expects `B7` and gets `A7` today. `test_cache_key_distinguishes_objects_with_equal_str`. |
| A loud loop cap (P3) | `task_scheduler.py:1043-1057`: when `fired and n == max-1`, call `state.record_op_error(op.full_name, "LoopLimitExceeded: 1000 iterations")` and expose `if_(..., max_iterations=N)` → `LoopConfig`. | `test_loop_cap_reports_error`: P3's graph must yield `$errors[...loop...]`. |
| Interrupts in the stream (P4) | `engine.stream`: add `mode="interrupts"`; `"updates"` also yields `InterruptEvent` objects. Fix the `interrupt_op.py:16-28` docstring. | `test_stream_updates_surfaces_interrupt_event` (P4: today the stream blocks and yields only dicts). |
| Doc drift | `docs/architecture/streaming.md` and `state-model.md` say per-yield dispatch runs "in parallel by default", which contradicts the tested guide (sequential). `overview.md` still advertises operonx-rs, which has been dropped. | Covered by the guide tests; edit the docs. |

### Phase 1: failure semantics (retry, timeout, error edges, fail-fast, concurrency)

New module `operonx/core/policy.py`:

```python
@dataclass(frozen=True)
class Retry:
    max_attempts: int = 3
    initial: float = 0.5
    backoff: float = 2.0
    max_interval: float = 30.0
    jitter: bool = True
    on: tuple[type[BaseException], ...] | Callable[[BaseException], bool] = DEFAULT_TRANSIENT  # 5xx, timeouts, ConnectionError

@dataclass(frozen=True)
class Timeout:
    run: float | None = None   # hard wall-clock seconds per attempt
    idle: float | None = None  # generators: max seconds between yields

@op(retry=Retry(max_attempts=4), timeout=Timeout(run=30))
async def call_crm(...): ...

lookup = call_crm(id=x, retry=Retry(max_attempts=2))     # per-call override (shorthand kwargs path, _shortcuts.py)
Operon(g, errors="record" | "raise", max_concurrency=32)  # run-level policy + ONE shared limiter
f.on_error(handler)    # error edge: handler(error: str, op: str, inputs: dict) runs instead of silence
```

Implementation points:
- **Retry and timeout** wrap `self._exec_core(_inputs)` in `BaseOp.run` (`base.py:1230`).
  - A batch op gets `asyncio.timeout(run)` around the whole attempt. A generator gets `idle` as a deadline between yields.
  - A generator is retried **only if it fails before its first yield** (`idx == 0`). After that, retrying would duplicate frames, so the error is recorded instead.
  - Every attempt appends an `OpExecution` with `attempt=n`. `bound="cpu"` ops: the timeout abandons the thread (as LangGraph documents, `types.py:466-470`) and records `TimeoutError`.
- **Error edges.** In `_pump`/`_on_frame`, a failed op emits an `ErrorFrame(op, ctx, error)` that is routed only along edges marked `kind="error"`. Normal successors behave as today (they never become ready).
- **Fail-fast** (`errors="raise"`): the first recorded op error triggers `_sweep_ctx(root)`, and `run()` raises `OpFailed(op, error)`. `Job` defaults stay as they are.
- **Concurrent-writer lint** (row 2), in `GraphOp.build()` after the cycle rewrite. For each declared cell that has no reducer, take its writers: if any two have no directed path between them in either direction, raise `BuildError("cell 'v' has concurrent writers a, b; add a reducer or allow_race=True")`.
- **Global limiter.** Store one `asyncio.Semaphore` (or a token bucket per `resource:`) on `state`. Nested schedulers acquire it in addition to their own graph semaphore (`task_scheduler.py:498`).

Tests (each must fail before the change):
- `test_retry_transient_then_success` (3 attempts, `attempts` recorded in the trace);
- `test_retry_not_on_valueerror`;
- `test_generator_not_retried_after_first_yield`;
- `test_timeout_records_and_retries`;
- `test_idle_timeout_generator`;
- `test_error_edge_handler_runs_once_with_error_text`;
- `test_errors_raise_mode_cancels_siblings`;
- `test_build_rejects_concurrent_lww_writers` (P2's graph);
- `test_nested_concurrency_shared_cap` (P10's graph with `max_concurrency=2` → observed max ≤ 2);
- a benchmark showing the callbot-shaped stream path with no policies set stays within noise (`scripts/bench_*`).

### Phase 2: `RunContext`, deterministic identities, unified event stream, live traces

- `operonx/core/runtime.py`: a read-only `RunContext` contextvar exposing:
  - `run_id`, `thread_id`, `op_path`, `ctx`, `attempt`;
  - `idempotency_key` (= `blake2b(run_id, op_full_name, ctx)`);
  - `deadline`, and a typed `context` (`engine.start(inputs, context=MyCtx(...))`).
  Op bodies get information here, never control flow. This respects the rejection of body magic in `STATE_LOOP_REFACTOR_PLAN.md:847-858`.
- **Deterministic `interrupt_id`** = `hash(run_id, op_full_name, ctx)`, replacing `uuid4` (`interrupt_op.py:107`). This is a precondition for durable HITL.
- **`engine.stream(inputs, mode=["updates","custom","interrupts","tasks"])`** yields `(mode, chunk)`. `tasks` yields `OpStarted`, `OpFinished` and `OpFailed` (with attempt), built from the existing `OpExecution` recording path.
- **Live traces.** Add `Consumer.on_execution(op_execution)` (optional) next to `consume(trace)`. `RunStore` gets `status="running"`, and the ClickHouse and SQL stores append nodes incrementally. Tests:
  - `test_trace_visible_while_running`;
  - `test_killed_run_leaves_partial_trace`: subprocess plus SIGKILL, then the store lists the run as `running` or `abandoned`, with its completed nodes.

### Phase 3: durable execution through a scheduler journal (the core gap)

**Design: event-sourced replay of the scheduler.** The scheduler's per-run bookkeeping (ready counts, sequential queues, collect buffers, loop signals) is entirely local to `_run_once` (`task_scheduler.py:352-1100`), and it changes *only* in response to `Frame`, `EOF` and `Interrupt` events.

So persisting those events in their arrival order, together with the post-reducer cell writes the `CellWriteEvent` bus already emits, is enough. Feeding them back through the same main loop rebuilds every piece of bookkeeping exactly. This is the Temporal deterministic-replay argument applied to OperonX's existing event bus. Nothing local needs a snapshot.

New package `operonx/durable/`:

```python
class Journal(Protocol):                       # backends: InMemory, Sqlite, Postgres
    def create(self, run_id, *, thread_id, graph_fp, inputs, scratch) -> None: ...
    def append(self, run_id, records: list[Record]) -> int: ...      # returns last seq; durability per mode
    def load(self, run_id) -> RunLog: ...                             # records ordered by seq
    def set_status(self, run_id, status) -> None: ...                 # running|interrupted|done|failed|drained
    def claim(self, run_id, worker_id, lease_s) -> bool: ...          # single-writer guarantee for resume

# records (msgpack/orjson + type registry; Media offloaded via telemetry.media)
OpYield(seq, op, ctx, idx, outputs) | OpEOF(seq, op, ctx) | OpFailed(seq, op, ctx, error, attempt)
CellWrite(seq, op, var, ctx, value)            # post-reducer — replay never re-runs reducers
ScratchWrite(seq, key, value)
InterruptPending(seq, op, ctx, interrupt_id, payload) | InterruptResolved(seq, interrupt_id, value)
SoftEdgeWon(seq, dst, ctx, src)                # races replay as they happened

engine = Operon(g, journal=SqliteJournal("runs.db"), durability="sync" | "async" | "exit")
h = engine.start(inputs, run_id="order-42", thread_id="cust-7")
await engine.resume("order-42")                                    # after a crash / on another worker
await engine.resume("order-42", answers={interrupt_id: {"approved": True}})
@op(on_resume="restart" | "fail")                                  # generators only; default "restart"
```

**Replay mode in `Scheduler`:**
- When `dispatch(op, ctx)` hits a key that has an `OpEOF` in the log, the op body is not called. A replay driver releases that op's recorded `Frame`/`EOF` events onto the queue **strictly in journal `seq` order**, and `store_result` writes the journaled post-reducer values.
- An op that was dispatched but has no `EOF` was in flight at the crash. It runs again (at-least-once), and it gets `RunContext.idempotency_key` so it can deduplicate external calls.
- A generator with k yields journaled and no `EOF`:
  - `restart` runs it again. The first k yields are compared by hash against the journal and suppressed. On a mismatch, `NonDeterministicResume` is raised.
  - `fail` refuses to resume.
  - Transient and ingress ops are not durable. A graph with `ingress()` can be journaled for audit but `resume()` raises `NotResumable`. The live callbot is unaffected.
- `InterruptOp` checks the log for `InterruptResolved(id)` first. When it is unresolved and a journal is attached, it writes `InterruptPending`, sets the status to `interrupted`, and the run may **park**: the coroutine ends, and `resume(answers=...)` re-drives it later. Without a journal, today's in-process future is kept unchanged.
- **Graph fingerprint**: `hash(graph.serialize())` is stored at create time. `resume` refuses on a mismatch unless `allow_graph_change=True`.
- **Durability modes:**
  - `sync`: `append` is flushed before the frame is routed;
  - `async`: a bounded writer that **applies backpressure and never drops**, unlike `telemetry/writer.py`, which drops;
  - `exit`: written only when the run ends.

Tests (the falsifiable core):
1. `test_crash_resume_skips_completed`. A subprocess runs A → B → C with side-effect counters in a file and a sqlite journal. It is SIGKILLed while C is in flight. A new process calls `resume`. Expected: counters A=1, B=1, C ≤ 2, and the output equals an uninterrupted run. This mirrors LG5 and LG6.
2. `test_durable_interrupt_across_processes`. Same shape as P4, but the second process resumes with the answer, and `plan` ran once (today it runs twice).
3. `test_replay_reproduces_reducer_and_race_outcomes`. Run P2's graph and the race graph from guide 03 (`~` edges), kill at random points, resume, and assert the same `acc` order and the same race winner as the original run.
4. **Property test**. Generate random DAGs (generators, `.parallel`, `.collect`, branches, loops, soft edges) with deterministic ops, kill at a random journal `seq`, resume, and assert the outputs equal those of an uninterrupted run. This is the test that would have caught failure-modes §4, where a fix was right on 2 of 5 dispatch paths.
5. `test_generator_nondeterminism_detected` and `test_ingress_graph_not_resumable`.
6. `test_graph_change_refuses_resume`.
7. A shared contract suite over all `Journal` backends: ordering, idempotent append, and leases.
8. Overhead benchmarks:
   - durability off: no measurable change on the callbot stream path (`journal=None` costs one `is None` check);
   - `sync` vs `async`: the cost per op on sqlite and postgres.

### Phase 4: threads, run queue, and platform semantics

- **Threads.** `thread_id` maps to an ordered chain of runs. `Operon(g, carry=["messages"])` seeds the declared cells of the next run from the last run's final values. `AgentSession` keeps its messages in a thread instead of `self._messages`.
- **Durable run queue** (`operonx/app/queue.py`): a Postgres `runs(run_id, thread_id, status, payload, available_at, lease_until, attempts)` table, with workers claiming rows via `SELECT ... FOR UPDATE SKIP LOCKED`.
  - Webhook `202` events are *persisted before the reply*, which fixes the in-memory `_pending` at `triggers.py:48-70`.
  - `schedule` takes a lease row so N replicas fire once.
  - Completion callbacks: `Service(..., on_done=webhook_url)`.
- **Double-texting per thread:** `Service(..., multitask="reject"|"enqueue"|"interrupt"|"rollback")`. `interrupt` means drain the current run and keep what it did. `rollback` means drain it and mark it discarded.
- **Stream reconnect:** `GET /runs/{id}/stream?after_seq=N` replays from the journal and then tails it.
- **Store:** a `store:` resource (sqlite or postgres, with optional pgvector) plus `StoreGet`/`StorePut`/`StoreSearch` ops.

Tests:
- `test_webhook_event_survives_restart`;
- `test_schedule_fires_once_across_two_workers`;
- `test_thread_carries_cells_between_runs`;
- `test_multitask_enqueue_orders_runs`;
- `test_stream_reconnect_after_seq`.

### Phase 5: history, fork, and `update_state` (lower priority)

`engine.history(run_id)` folds the journal at each `seq`. `engine.fork(run_id, at_seq, patch={cell: value}) -> new_run_id` copies the journal prefix, appends `CellWrite` patch records, and resumes. This gives LangGraph's time travel without snapshot-format complexity (c5). Test: `test_fork_from_seq_reruns_only_downstream`.

### Ordering rationale

- Phase 0 fixes wrong results that exist today (P6) and silent failures (P3, P4).
- Phase 1 closes the gaps that make every production deployment re-implement retries and timeouts.
- Phase 2 creates the stable identities that Phase 3 depends on.
- Phase 3 is the largest piece and the real differentiator. It slots into the existing `Frame`/`EOF` and `CellWriteEvent` buses without touching the hot path when disabled.
- Phases 4 and 5 build on the journal.

---

## Appendix A: probe evidence (verbatim output)

OperonX (`uv run` from `/home/thanglq/Operon`):

```
# probes/p1_errors_retry_parallel.py
P1 keys: ['$errors']
P1 errors: {'out.f': 'RuntimeError: transient 503'}
P1 attempts: 1 | downstream z present: False
P2 run1 final_v/acc: slow ['fast', 'slow'] errors: False
P2 run2 final_v/acc: fast ['slow', 'fast'] errors: False
P3 iterations: 1000 last: 1000 | $errors: None
# probes/p2_interrupt_cache_ckpt.py
P4 stream(mode=updates) blocked on InterruptOp; types seen before block: {'dict'} | InterruptEvent seen: False
P4 interrupt events: 1
P4 after cancel, resume_interrupt -> False
P4 rerun result done= yes | plan() side effects executed: 2
P5 start params: ['self', 'inputs', 'user_id', 'session_id', 'request_id', 'trace_id', 'scratch', 'checkpointer']
P5 checkpointer methods: ['get_state', 'get_updates', 'list_cancels', 'list_steps', 'on_cancel', 'on_cell_write', 'on_step']
P6 cache: ga -> A7 | gb -> A7 (expected B7)
# probes/p3_stream_nest_cancel.py
P7 gen mid-stream error: o= [0, 10] | $errors keys: ['out.g']
P8 updates op names (nested): ['seen.s.l', 'seen.s', 'seen.s', 'seen']
P9 cpu op side effect after cancel: 1
P10 nested concurrency=2 each, observed max concurrent leaf ops: 4
# probes/ox_barrier.py
OX7 a2 started at s: 0.019
```

LangGraph 1.2.12 (cloned source):

```
# probes/lg_probe.py
LG1 LWW concurrent writes -> InvalidUpdateError
LG2 reducer order (a slower than b): {'acc': ['a', 'b']}
LG3 loop cap -> GraphRecursionError
LG4 retry: {'n': 1} attempts 3
LG5 interrupted: True
LG5 pending next: ('ask',)
LG5 resumed: {'plan': 'do X', 'answer': 'yes'} | plan side effects: 1
LG5 history len: 4
LG6 after resume: {'y': 6, 'ok': True} | task executions: 1
# probes/lg_barrier.py
LG7 a2 started at s: 0.509
```

## Appendix B: what OperonX's own design log already decided (respected above)

`docs/design/STATE_LOOP_REFACTOR_PLAN.md:847-866` rejects `Command(goto)`, the `Command` wrapper, in-body `interrupt()`/`emit()`, `STATE[...]`, the wave barrier, and the `"messages"` stream name.

It also defers two things: the checkpoint tree (`thread_id`/`parent_checkpoint_id`) and the SQLite backend ("when persistence is a real ask", `:743,757`). This audit keeps every rejection.

It recommends un-deferring persistence, but in a different shape from the one the design log considered. It should be an event journal, not LangGraph's snapshot tree. The design log's own claim that "operonx captures per-batch atomically, so `put_writes` is not needed" (`:860`) holds only for an observational checkpointer. Resuming after a crash needs completion records per invocation, which are exactly the `OpEOF` records in Phase 3.
