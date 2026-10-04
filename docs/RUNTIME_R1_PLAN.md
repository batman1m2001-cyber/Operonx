# R1 — failure semantics

Status: **built, 2026-10-04** (branch `feat/r1-failure`, stacked on `fix/w0`); §4 records what was measured. Source: ROADMAP §2 R1;
`roadmap/track2_langgraph_gap.md` rows 2, 5, 6, 7, 19 and Phase 1; `roadmap/track1_dogfood.md` item 6 (F13, F33).

Every choice below is resolved. Nothing here reopens a rejection in `design/STATE_LOOP_REFACTOR_PLAN.md`: policies
are declared on the op or the engine, error routing is a visible edge, and no op body gains a control-flow call.

## 1. What exists today

| Concern | Today | Where |
|---|---|---|
| An op raises | recorded (`$errors`, `error` cell, trace node), no frame, successors never run | `base.py` `BaseOp.run` `except Exception` |
| Retry | none per op; `Job(on_error="retry:N")` retries at once, no backoff; `LLMOp` retries its transport per resource | `app/jobs/runner.py`, `providers/ops/llm.py::_call_with_retry` |
| Timeout | none per op; a whole run only (`serve_session(timeout=)`, `Job.item_timeout`) | `app/serve/runner.py` |
| Fail fast | none; `ObserveBudgetExceeded` (a `BaseException`) is the one thing that ends a run | `task_scheduler.py` `fatal` |
| Concurrency | one `asyncio.Semaphore(graph.concurrency)` per scheduler; nested graphs multiply (P10: 2×2 → 4) | `task_scheduler.py::_run_once` |
| Concurrent writers | last writer by wall clock wins, silently (P2) | — |

## 2. The API

```python
from operonx import Operon, OpFailed, Retry, Timeout, TRANSIENT, op

@op(retry=Retry(max_attempts=4), timeout=Timeout(run=30))
async def call_crm(order_id: str) -> dict: ...

crm = call_crm(order_id=x, retry=Retry(max_attempts=2))   # per-call override
crm.on_error(apologise)                                    # error edge
Operon(g, errors="raise", max_concurrency=32)              # fail fast; one limiter for the whole run
PARENT.declare(status=None, allow_race=True)               # opt out of the concurrent-writer check
```

All four names live in a new `operonx/core/policy.py` and are exported from `operonx` and `operonx.core`.

### 2.1 `Retry`

`Retry(max_attempts=3, initial=0.5, backoff=2.0, max_interval=30.0, jitter=True, on=TRANSIENT)`, frozen.

- **Delay** after failed attempt *n*: `d = min(max_interval, initial * backoff**(n-1))`; with `jitter`, uniform in
  `[d/2, d]` ("equal jitter": spread out, but never collapsing to an immediate retry).
- **`on`**: an exception class, a tuple of them, or a predicate `error -> bool`. `TRANSIENT` (the default) is a
  predicate: `TimeoutError`, `ConnectionError`, an HTTP status of 429 or 5xx on the error (`status_code`, `status` or
  `response.status_code`), and the transport errors of `httpx` and `openai` when those modules are loaded. It never
  imports either.
- **Never retried**: a `BaseException` (cancellation, `ObserveBudgetExceeded`); a generator that has already yielded
  (retrying would duplicate frames, so the error is recorded instead); an error an inner layer already retried to
  exhaustion (§2.6).
- Every attempt is its own `OpExecution`, with a new field `attempt` (1-based). A failed attempt that is retried
  records `status="error"` under `op_id = "<op_id>@<n>"`, so the last attempt keeps the canonical `op_id` that
  downstream `UpstreamRef`s point at.
- Validated where it is declared: bad numbers, an `on` that is neither, or `retry=3` (a bare int) raise `TypeError`
  or `ValueError` naming the fix (`retry=Retry(max_attempts=3)`).

### 2.2 `Timeout`

`Timeout(run=None, idle=None)`, frozen; at least one, both positive.

- **`run`**: wall-clock seconds per attempt, from the attempt's start to its end. For a generator the deadline keeps
  running while the consumer holds a yield, but it can only fire while the generator itself is working: each
  `__anext__` is awaited with `min(idle, run deadline - now)`.
- **`idle`**: generators only — the longest the generator may take to produce its next item. On a non-generator it is
  refused at construction.
- On expiry the attempt is cancelled and `TimeoutError("<op> exceeded Timeout(run=0.2) ...")` is raised there, so it
  is recorded exactly like any other failure: `$errors`, the `error` cell, an error trace node, no frame, successors
  skipped. `TimeoutError` is `TRANSIENT`, so a `retry=` retries it.
- `bound="cpu"`: the await on the worker thread is cancelled and the thread is abandoned (it runs to completion in the
  background; Python cannot stop it). Documented, as LangGraph does.
- A plain `def` op (`bound="sync"`) runs inline on the event loop, where nothing can interrupt it. A timeout there
  is refused at construction: `ValueError(... use bound="cpu" to run it in a thread the timeout can abandon)`.
- A `@graph` used as an op takes `timeout=Timeout(run=)`: its nested scheduler is cancelled (which cancels its op
  tasks) and the subgraph records `TimeoutError`. `idle` and `retry` on a graph are refused: a subgraph's children
  record their own failures and never raise out of it, so there is nothing to retry.
- Python 3.10 has no `asyncio.timeout`; `policy.py` carries a small deadline context manager (`loop.call_later` +
  `task.cancel`, with `Task.uncancel` on 3.11+ so an outside cancel is never mistaken for the deadline).

### 2.3 Where the policies run

`BaseOp.run` keeps one loop over results. When the op has a policy, that loop reads from
`BaseOp._exec_with_policy(inputs, ...)` instead of `_exec_core(inputs)`: an async generator that owns the attempts,
the deadlines and the backoff, and only ever yields outside its deadline block. An op with no policy takes exactly
the path it takes today, so the no-policy cost is one `is None` test per invocation.

- **Per-call override**: `split_shorthand_kwargs` routes `retry=` / `timeout=` to the constructor **only when the value
  is a `Retry` / `Timeout`**. Anything else under those names stays an input mapping, so an existing
  `fetch(url=u, timeout=10)` keeps working. They are not added to `_BASE_INIT_KEYS` (that would warn on every op
  with a `timeout` parameter). Applies to `@op`, `@graph` and every `Op.of(...)`.
- **Dispatch**: an op with `retry=` always runs as a task, even a plain `def` (the precedent is a producer on a
  bounded edge): its backoff sleeps must not stall the scheduler's main loop. A graph whose children all run inline
  but one has a retry is a task too.

### 2.4 `errors="record" | "raise"`

`Operon(g, errors="record")` is today's behaviour and stays the default (a live session must survive one bad op).

`errors="raise"`: the first op failure that no error edge handles ends the run.
- `BaseOp.run` / `GraphOp.run` record the failure as today (so `$errors` and the trace still show it), then raise an
  internal `BaseException` carrier. It passes every `except Exception` on the way up — a subgraph does not swallow it
  — and reaches each scheduler's existing `fatal` path, whose `finally` already cancels the in-flight siblings.
- At the engine boundary the carrier becomes the public `OpFailed(op, error)` (an `Exception`, with the original
  error as `__cause__`), raised from `run()`, `result()`, `collect()`, iteration and every `stream()` mode.
- A loop that hits its cap (`LoopLimitExceeded`) raises too under `errors="raise"`.
- The run-level settings travel on the run's `MemoryState` as one new slot, `_policy` (`None` for an op called
  directly), read by the scheduler and by the failure path only.

### 2.5 Error edges: `op.on_error(handler)`

```python
@op
def apologise(error: str, op: str, inputs: dict) -> dict:
    return {"reply": "Sorry, try again later."}

@graph
def answer(q):
    lookup = call_crm(order_id=q)
    sorry = apologise()
    START >> lookup >> END
    lookup.on_error(sorry)   # sorry runs once, only if lookup failed (after its last retry)
    sorry >> END
```

- An edge like any other, recorded with `type="error"` in `_edges` (so `nexts`, reachability, serialization and the
  studio see it), compiled into a separate adjacency `_err_adj` so normal routing never looks at it.
- The handler counts the error edge as a hard predecessor, so it runs when the op fails **and** its other hard
  predecessors have landed. It runs once per failed invocation (per context), never per attempt.
- The handler's parameters named `error` (`"TypeName: message"`), `op` (the failed op's full name) and `inputs`
  (the inputs it was called with) receive the failure, written into the handler's own input cells at the failing
  context — the same direct write `.collect()` uses. A parameter the author wired is left alone.
- A failed op whose error edges route a failure is **handled**: still recorded in `$errors` (silence is the failure
  mode), but `errors="raise"` does not end the run for it.
- **Merges**: an op and its handler are exclusive, like two branch arms, so `lookup >> reply; sorry >> reply` merges
  by itself. The auto-soften pass treats an op with error edges as a branch with two arms, "ok" and "error".
- Mechanism: `BaseOp.run` yields a `Failure` event (new, in `_events.py`) when the op has error edges; `_pump` and
  `_drain_inline` hand it to the main loop like an `Interrupt`; `_on_failure` writes the handler inputs and routes
  along `_err_adj`. An op without error edges emits nothing new.
- Refused at wiring time: a handler in another graph, `START`/`END`/`PARENT` as either end, a branch op as the source.

### 2.6 `LLMOp` and op-level `retry=` compose

`LLMOp` has two retries already. `max_retries=` on the op re-asks the model after a parse or validator failure; those
failures are not exceptions and never reach `Retry`. The resource's `max_retries` drives `_call_with_retry`, the
transport retry per resource (429, 5xx, connection, timeout, empty answer), and it walks the `fallback` chain.

The rule: **an error an inner layer already retried is never retried again by an outer one.** `_call_with_retry`
marks the error it gives up on (`policy.mark_retried(error)`), only when it actually retried (`max_retries > 0`).
`Retry` skips a marked error whatever `on` says. So:

- resource `max_retries: 0` (the default) + `@op(retry=Retry(4))`: the op retries the call up to 4 times, with backoff;
- resource `max_retries: 3` + `retry=`: the transport retries 3 times and the op does not retry that error again —
  no 4×4 = 16 calls. `retry=` still covers what the transport does not (an op-level `Timeout`, a refusal surfaced as
  a transient status);
- a fallback chain that is exhausted raises a plain `RuntimeError`, which is not transient.

### 2.7 One concurrency limiter per run

`Operon(g, max_concurrency=N)` creates one `asyncio.Semaphore(N)` per run, on `state._policy`. Every scheduler of that
run, nested ones included, acquires it in `_pump` for a **leaf** op (a nested `GraphOp` does not take a slot: it holds
it while its children wait for one, which deadlocks at small N). A producer parked on a full bounded edge gives this
slot back too, as it does its graph slot. Graph-level `concurrency=` stays as the per-graph cap. Default `None`: no
limiter, no cost beyond one `is None` per task.

Per run, not per engine: a service with many sessions wants a per-resource cap, which is the next item.

### 2.8 `rate_limit:` per resource — planned, not built

It does not fit the run limiter: a provider's rate limit is per API key across every run in the process, while the
limiter is per run; and the call that must wait is chosen inside the op (`LLMOp` picks a resource by ratio and walks
`fallback`), so a cap applied around the whole op would charge the wrong resource. The shape that fits:
`rate_limit: {rpm: 600, concurrency: 8}` on a resource entry, parsed by `ResourceHub` into one process-wide limiter
per resource key, acquired by `_call_with_retry` (and the embedding/rerank/search call sites) around each provider
call. Tests: two concurrent runs share one resource's budget; a fallback resource is charged to itself. Its own
change, next to the provider ops.

### 2.9 Concurrent writers fail the build

In `validate()` (build time, after the cycle rewrite): for each declared cell **without a reducer**, collect its
writers — the graph's children whose outputs push into it (a subgraph or a hidden loop counts as one writer). Two
writers that are neither ordered (a path between them along any edge, soft and error edges included) nor exclusive
(different arms of one branch, or an op and its error handler) raise `GraphValidationError` (the "BuildError" of the
roadmap):

```
cell 'v' of graph 'g_par' has concurrent writers 'w_fast' and 'w_slow': whichever finishes last wins.
  - order them (w_fast >> w_slow), or
  - give the cell a reducer: PARENT.declare(v=..., reducers={"v": fn}), or
  - declare that last-write-wins is intended: PARENT.declare(v=..., allow_race=True)
```

`allow_race=True` covers the vars of that `declare()` call; `allow_race=["v"]` names some (checked like `reducers`).
Out of scope: a single writer fed per item through `.parallel()` (one op racing itself), and writers inside one hidden
loop iteration. Before it becomes an error it runs in report mode over every in-repo graph — agents, `operonx init`
templates, examples, the guide — and over callbot (`refactor/operonx-studio`, read only). Any false positive changes
the rule, not the graph.

### 2.10 Jobs (F33)

`retry:N` waits between attempts with the default `Retry` backoff (0.5 s, 1 s, 2 s … with jitter, capped at 30 s).
A timed-out item keeps its `trace_id`: `RunTimeout` carries the run's trace id, so the hung run can be inspected.

## 3. Tests (each fails before its change)

`tests/internal/core/ops/test_policy.py` and neighbours:

| Test | Asserts |
|---|---|
| `test_retry_transient_then_success` | 2 transient failures then success: output present, 3 `OpExecution`s with `attempt` 1-3, first two `error` |
| `test_retry_not_on_valueerror` | `ValueError` is not `TRANSIENT`: one attempt |
| `test_retry_backoff_spacing` | gaps between attempts follow `delay()`; jitter within `[d/2, d]` |
| `test_generator_not_retried_after_first_yield` | a generator failing after one yield runs once, the one frame is not duplicated |
| `test_generator_retried_before_first_yield` | a generator failing before its first yield is retried |
| `test_timeout_records_and_retries` | 5 s op with `Timeout(run=0.2)`: `TimeoutError` in `$errors` within 0.4 s, successor skipped; with `retry=` it runs again |
| `test_idle_timeout_generator` | a generator stalling 1 s between yields with `idle=0.2`: the first items reach the consumer, then `TimeoutError` |
| `test_cpu_timeout_abandons_thread` | `bound="cpu"` op: run returns at the deadline with `TimeoutError` |
| `test_timeout_refused_on_inline_op`, `test_idle_refused_on_batch_op`, `test_bare_numbers_refused` | construction errors name the fix |
| `test_per_call_override`, `test_timeout_param_stays_an_input` | shorthand routes by type |
| `test_llm_transport_retry_not_doubled` | resource `max_retries=2` + op `Retry(4)`: 3 transport calls, not 12 |
| `test_error_edge_handler_runs_once_with_error_text` | handler gets `error`, `op`, `inputs`; runs once after the last of 3 attempts |
| `test_error_edge_merge_auto_softens` | `f >> r; h >> r` runs `r` on either path |
| `test_errors_raise_mode_cancels_siblings` | `OpFailed` raised; a slow sibling is cancelled; `$errors` still recorded |
| `test_errors_raise_inside_subgraph`, `test_errors_raise_ignores_handled_error` | |
| `test_nested_concurrency_shared_cap` | P10's graph with `max_concurrency=2`: observed max ≤ 2 |
| `test_build_rejects_concurrent_writers` | P2's graph raises; `allow_race=True` builds; ordered writers, branch arms and a reducer build |
| `test_job_retry_backs_off`, `test_job_timeout_keeps_trace_id` | F33 |

Plus guide page `operonx/guide/06-failures.md` (every snippet runs in `tests/guide`), and the W0 stream benchmark
(`bench_stream.py`, no policies set) before and after: within noise (±5%).

## 4. Built — what was measured

- **Race check, before it became an error** (report mode, every hit logged): the operonx suite, the 73 graphs of
  `examples/`, the `operonx init` templates and the `operonx` packages, the callbot suite and its 7 `@graph`s under
  `src/` (`refactor/operonx-studio`). One hit: P2's graph in the new test. No false positive, so the rule stands.
- **`LLMOp` composition:** resource `max_retries: 2` under `Retry(max_attempts=4)` made 12 calls before, 3 after.
- **`max_concurrency`:** P10's graph ran 4 leaf ops at once; with `max_concurrency=2`, at most 2. Without the slot
  release on a full bounded edge, `max_concurrency=1` + `max_pending` deadlocked (measured, then fixed).
- **Python 3.11 / 3.12:** the R1 tests pass on both (the deadline uses `Task.uncancel` there).
- **Benchmark:** see the branch report (W0 `bench_stream.py`, no policies set, base and branch alternated).
