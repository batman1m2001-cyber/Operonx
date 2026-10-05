# R3 — durable execution through a run journal

Status: **plan, 2026-10-05.** Source: ROADMAP §2 R3; `roadmap/track2_langgraph_gap.md` (durability rows); R2
(`RUNTIME_R2_PLAN.md`: `invocation_key`, deterministic interrupt ids, live traces). Every choice below is resolved.
Nothing here gives an op body control flow, and nothing reopens `design/STATE_LOOP_REFACTOR_PLAN.md` §Rejected
(no snapshot-per-step checkpoints, no `interrupt()` that re-runs an op body).

## 1. What exists today (read, not assumed)

| Concern | Today | Where |
|---|---|---|
| What drives a run | the scheduler's `Frame` / `EOF` / `Failure` / `Interrupt` events from `op.run(state, ctx)`, at two call sites (`_pump` for task ops, the inline drain for `bound="sync"` ops), one scheduler per graph level | `task_scheduler.py` `_pump`, `_drain_inline` |
| Identity of one execution | `(op_full_name, ctx)`; ctx is deterministic (`"[i]"` per yield, loop iteration segments); `invocation_key(run_id, op, ctx)` is stable across reruns | `core/runtime.py` |
| State writes | every op output goes `store_result` → `state[op, var, ctx]` → `_write_cell` (reducer applied, then observers get the post-reducer value). Exceptions: the per-op `error` cell and the metric cells, assigned directly in `BaseOp.run` | `states/state.py:454`, `ops/base.py:1611` |
| Failures | `state._op_errors` (`$errors`), the op's `error` cell, `Failure` events on error edges | `state.record_op_error`, `BaseOp.run` |
| Interrupts | `InterruptOp` awaits an in-process future keyed by a deterministic `interrupt_id`; a restart loses it | `ops/flow/interrupt_op.py` |
| Checkpointer | an observer of cell writes, in memory; nothing can resume from it | `operonx/checkpoint/` |
| A crashed process | loses the run; a rerun repeats every side effect | — |

## 2. The model

**An execution** is one `op.run(state, ctx)` call: `(op_full_name, ctx)`. It yields events and ends. A run is
rebuilt exactly from (a) the state each finished execution left and (b) the events it yielded, because the
scheduler's bookkeeping changes only on those events (ROADMAP §2 R3).

**The journal** is append-only, one row per entry, ordered by `seq` within a run:

| Entry | Written | Holds |
|---|---|---|
| `run` | at start | run id, thread id, graph fingerprint, inputs, `durability`, status |
| `step` | when an execution yields (one per yield) and when it ends | the execution `(op, ctx)`, the yield index or `end`, the cell writes it made since its last step (post-reducer values), the event it yielded (frame `item_ctx` + outputs, or a `Failure`), at end its status (`ok` / `error`) and error text and `$errors` record |
| `interrupt` | when an `InterruptOp` parks | `interrupt_id`, the execution, the payload |
| `status` | on end / park / drain | `ok`, `error`, `interrupted`, `drained` |

A `step` is atomic: an execution's writes become durable together with the event that follows them, never
apart. There is no await between `store_result` and the `yield` that follows it in `BaseOp.run`, so no other
op's writes interleave inside one step.

**Resume** = restore, then replay:
1. A fresh state from the journalled inputs; every cell set to the value of its **last** journalled write
   (post-reducer — so reducer order does not have to be re-derived), the `error` cells and `$errors` from the
   journalled ends.
2. The graph runs again from the start. Each execution is looked up in the journal:
   - **ended** (`ok` or `error`): not run. Its journalled events are yielded again in order — no writes (the
     cells already hold them) — so the scheduler dispatches what came after it exactly as before. A finished
     subgraph is one execution: its inner ops are not visited.
   - **partly ran** (a generator with k journalled yields, no end): runs again from the top (at least once). With
     `on_resume="restart"` (default) its first k yields are checked against the journal by a hash of their
     outputs and their writes are not repeated; a mismatch raises `NonDeterministicResume`. `on_resume="fail"`
     refuses to resume such a run.
   - **not in the journal** or **in flight with no step**: runs live. Its `run_context().idempotency_key` is the
     same as in the run that crashed, which is how an external call deduplicates.

## 3. Decisions

| # | Question | Decision | Why |
|---|---|---|---|
| D1 | Where the journal hooks in | One wrapper around `op.run(state, ctx)` at both scheduler call sites (`_journaled(op, state, ctx)`), plus a write observer on the state for the step's writes. `journal is None` → `op.run` as today: one check per execution | every level (root, subgraph, synthetic loop) dispatches through these two sites; no op is special |
| D2 | What a step holds | the writes since the execution's last step + the event; the `error` cell value and `$errors` record at end | the direct `error`-cell assignment is the one write the funnel does not see; the subgraph-failure rule (#92) reads it |
| D3 | Restore by last write, not by re-applying | cells are set to their last journalled value; replayed events carry no writes | reducer order and races then come back exactly (`test_replay_reproduces_reducer_and_race_outcomes`) without re-deriving them |
| D4 | Serialization | `pickle` (protocol 5) of the values; the journal is trusted storage, like a checkpointer, and must round-trip any value an op returns. A value that cannot be pickled fails the run at that step with the op and var named (`JournalError`), not later at resume | exactness; JSON would turn a tuple into a list and a dataclass into a string, and the resumed run would differ |
| D5 | Durability modes | `"sync"`: a step is committed (`await to_thread(journal.append)`) before its event reaches the scheduler; `"async"` (default): steps go to a background writer, committed in order in batches — a crash loses at most the unflushed tail, which runs again; `"exit"`: written when the run ends, parks or drains | the roadmap's three; `async` keeps the event loop free, `sync` is for runs whose side effects cannot repeat |
| D6 | Graph changes | `run` stores a structural fingerprint of the compiled graph — every op's full name, type and callable source digest, every edge and error edge, recursively through subgraphs and the cycle-rewrite's synthetic loops (`operonx.durable.graph_fingerprint`). `resume` refuses a different graph with both fingerprints named, unless `allow_graph_change=True` | an op renamed or rewired makes journalled `(op, ctx)` pairs mean something else. The evals' `graph_hash` cannot be used: it hashes `GraphOp.serialize()`, which refuses graphs with loops, and core does not import `app.evals` |
| D7 | Durable interrupts | with a journal, `InterruptOp` writes an `interrupt` entry and the run **parks**: no new dispatches, in-flight ops finish, status `interrupted`, `handle.result()` returns `{"$interrupted": [{interrupt_id, op, payload}]}`. `engine.resume(run_id, answers={id: value})` replays to that execution, which returns the answer at once. Without a journal, today's in-process future | the visible node becomes durable (ROADMAP "Do not build": no `interrupt()` that re-runs an op body) |
| D8 | Drain | `handle.drain()`: the scheduler dispatches nothing new, in-flight ops finish and are journalled, status `drained`; `resume` continues it | a deploy stops a worker without losing runs |
| D9 | API | `Operon(g, journal=…, durability="async", on_resume="restart")`; `engine.start(inputs, run_id=…, thread_id=…)`; `await engine.resume(run_id, answers=None, allow_graph_change=False)` returns a handle like `start`; `engine.runs(status=…)` lists journalled runs | the roadmap's shape; `run_id` becomes the trace id so a resumed run continues the same trace |
| D10 | Journals | protocol `Journal` (`open_run`, `append(run_id, steps)` atomic per batch, `read(run_id)`, `set_status`, `runs(status)`); `MemoryJournal` (tests, one process), `SqliteJournal` (WAL, one file); Postgres arrives with R4's `runs` table. One contract suite runs every journal | the contract is what the property test relies on |
| D11 | Doors | a graph with `ingress` can be journalled (audit) but not resumed: `resume` refuses it, naming the door | a door's input is a live connection, not a journalled value |
| D12 | Streams on resume | a resumed run's `stream()` yields the replayed events too, each marked `replayed=True` in `tasks` mode; `result()` is the same as an uninterrupted run's | a consumer that wants only new events filters on the flag |

## 4. Phases

| Phase | Ships | Gate |
|---|---|---|
| **R3a** | journal protocol, `MemoryJournal`, `SqliteJournal`, the wrapper + write observer, restore + replay, `engine.resume`, fingerprint refusal, `durability` sync/async/exit | the property test (§5) green on 500 generated graphs; `test_crash_resume_skips_completed` across a real SIGKILL; journal off: callbot suite and `bench_stream` within noise |
| **R3b** (done) | durable interrupts (park + `resume(answers=)`), `drain()`, generator `on_resume`, door refusal | `test_durable_interrupt_across_processes`; drain then resume equals an uninterrupted run |

## 5. Tests (each fails before its change)

- **The core proof** — `tests/internal/durable/test_resume_property.py`: hypothesis generates graphs (chains,
  fan-out/fan-in, generators, `.parallel`, `.collect`, branches, soft edges, a loop, a subgraph, a reducer cell
  written by parallel ops, an op that fails with and without an error edge); runs each once uninterrupted, then
  again with a crash injected after journal entry *n* (random), resumes, and asserts outputs, `$errors` and the
  final cells equal the uninterrupted run's, and that every execution journalled as ended ran exactly once
  across the two processes' worth of work (counted).
- `test_crash_resume_skips_completed` (subprocess, SIGKILL mid-run, resume in a new process).
- `test_replay_reproduces_reducer_and_race_outcomes`, `test_graph_change_refuses_resume`,
  `test_generator_partial_restart_checks_hashes`, `test_unpicklable_value_names_op_and_var`,
  `test_durability_sync_commits_before_routing`.
- Contract suite over `MemoryJournal` and `SqliteJournal`.
- R3b: `test_durable_interrupt_across_processes`, `test_drain_then_resume`, `test_door_graph_is_not_resumable`.
- Journal off: no new allocation per execution (the `is None` check), callbot suite, `bench_stream` ±5 %.
