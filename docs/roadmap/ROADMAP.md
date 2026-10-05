# OperonX roadmap, Q4 2026: a production AI workflow foundation

Written 2026-10-04 against operonx 1.14.0 (`a21082d`) and operonx-studio `7de6d60`.

This is the consolidated plan from five investigations. Each one is a full report in this folder, and every finding
in them was checked against the source, a probe, or a reproduction you can run (`evidence/`):

| Track | Report | How it was done |
|---|---|---|
| 1. Hard-to-please user | [track1_dogfood.md](track1_dogfood.md) | 8 small products built on 1.14.0 with a real model; 34 frictions, each reproduced |
| 2. LangGraph audit | [track2_langgraph_gap.md](track2_langgraph_gap.md) | LangGraph 1.2.12 cloned and run side by side; 29-row gap table |
| 3. Agent framework | [track3_agents.md](track3_agents.md) | `operonx.agents` read in full, the callbot audited, 2026 frameworks researched |
| 4. Workflow evaluator | [track4_eval.md](track4_eval.md) | Existing Eval/Review code read, Langfuse/LangSmith/Braintrust/Inspect researched |
| 5. Knowledge platform | [track5_knowledge.md](track5_knowledge.md) | OperonX primitives read, PageIndex/Docling/GraphRAG/ColPali researched |

The goal: production reliability, simplicity, composability and real user value, without becoming a copy of
another framework.

---

## 0. The short version

1. **The happy path is good; the failure path is not.** A real user built a prompt chain, a tool agent, jobs and
   loops, and they worked the first time. But failures go silent: wrong totals, a `200 null` from a crashed
   request, a cancelled run that hangs, a timed-out run that keeps doing side effects. These are fixed first.
2. **OperonX's runtime model is the right one.** It is about 25× faster than LangGraph's superstep barrier on the
   same graph (0.019 s vs 0.509 s to start a dependent node), streams between ops with backpressure, and has
   deterministic context paths. We keep it and do not adopt Pregel.
3. **The production gaps are failure semantics and durability.** No per-op retry or timeout, no crash recovery,
   no durable human approval. The fix is one mechanism: an append-only **journal** of the scheduler's own events,
   which gives resume, durable interrupts, idempotency keys and live traces together.
4. **Three new packages sit on top, never inside the core:** `operonx-agents`, the evaluation system (in core,
   because it extends the existing `Eval`), and `operonx-kb`.
5. **Studio becomes where you debug, evaluate and inspect knowledge.** The Langfuse-grade input/output view is
   already built (studio PR #11).

### Order of work

| Wave | Weeks | Core | Runtime | Eval | Agents | Knowledge | Studio |
|---|---|---|---|---|---|---|---|
| **W0** | 1 | Correctness bugs (§1.1) | n/a | Plan doc, baseline noise | Plan doc | Plan doc, skeleton | I/O view (done, PR #11) |
| **W1** | 2–4 | Debuggability (§1.2) | Failure semantics (§2, R1) | Identity, repeats, statistics | Phase 0 spike, decision D1 | Foundation ingest | Script runs, names, errors |
| **W2** | 5–8 | DX polish (§1.3) | RunContext, streams, live traces (R2) | TraceView, ScoreStore, CLI, CI | Tools, Model, `llm_step` | Hybrid retrieval, citations, eval | Live runs, agent view |
| **W3** | 9–14 | n/a | Journal: durable execution (R3) | Judges, Studio experiments | Runner, state, approvals | Studio Knowledge tab | Experiments, compare, Knowledge |
| **W4** | 15+ | 2.0 cleanups | Threads, run queue, cron lease (R4) | Online eval, queues | Serve, evals, callbot adoption | Enrichment, tree, multimodal, graph | Online scores |

Each item ships on its own branch with its own tests. Each wave has a measured gate before the next starts.

### Decisions only you can make

| # | Decision | Recommendation | When |
|---|---|---|---|
| D1 | Agent loop inside one op, instead of a graph back-edge | Yes, if the W1 spike shows ≤ 2 ms/turn overhead and equal trace fidelity | End of W1 |
| D2 | Agents as a separate repo `operonx-agents` | Yes | W1 |
| D3 | Deprecate `operonx.agents` one release after `operonx-agents` 1.0 | Yes | W4 |
| D4 | Reserved op keywords (`id`, `name`, `start`, `stream`, … 21 of them) move behind a namespace in 2.0 | **Changed 2026-10-05:** not needed. A keyword the function takes is now its input (DX_PLAN X3), so a flat setting never collides; a clashing setting goes through `f.configure(...)`. Flat settings stay, no deprecation (callbot has ~70 correct ones) | W2 |
| D5 | First knowledge corpus (Edupia/educa or another) | Your call; it decides the first internal eval set | W1 |
| D6 | CI gets ClickHouse credentials to write experiments; daily judge budget for online eval | Yes; budget to be set after W2 measurements | W2 |

---

## 1. OperonX core refactoring

### Current problems (all reproduced on 1.14.0)

| # | Problem | Where | Effect |
|---|---|---|---|
| C1 | `.collect()` re-stores every output of the collected op, so a reducer cell gets each item twice | `core/ops/graph/task_scheduler.py:918-937` | Wrong totals; `ReducerError` with a dict reducer |
| C2 | An op failing inside a subgraph does not stop the op after it | `core/ops/graph/graph_op.py:775-780` | Successor runs with `None`; HTTP answers `200 null` |
| C3 | `handle.cancel()` never marks the run done | `core/engine.py:140, 371-374` | `result()` waits forever |
| C4 | `asyncio.wait_for(engine.run())` times out but the graph keeps going | `core/engine.py:780-793` | Side effects after the timeout |
| C5 | `@op(cache=True)` keys only on the op's full name | `core/ops/base.py:850-871` | Two graphs return each other's results (`A7` for `B7`) |
| C6 | Structured output coerces silently; JSON with any preamble fails; XML fails on `&` | `providers/parsing.py:198-241, 348-394` | `urgency: int` = `"2.5"`, `bool("maybe")` = True |
| C7 | A misspelled **output** key builds and returns the consumer's default | `graph/validation.py` (no check) | `total=0` instead of an error |
| C8 | The 1000-iteration loop cap stops silently | `task_scheduler.py:1043-1057` | No `$errors` entry |
| C9 | `stream(mode="updates")` is documented to yield `InterruptEvent` and never does | `interrupt_op.py:16-28` | The stream blocks |
| C10 | `LLMOp(stream=True)`: the final frame repeats the whole text | `providers/ops/llm.py:1134-1140` | Delta consumers send it twice |
| C11 | Websocket frames reach the graph undecoded; a bad JSON body runs the graph instead of a 400 | `app/serve/asgi.py:149-155`, `serve/app.py:314-318` | Silent client, wasted runs |
| C12 | `$errors` keeps the first failure per op only; errors are full tracebacks starting with operonx frames | `core/states/state.py:366-380`, `base.py:1311-1316` | Two failures look like one |
| C13 | Run names come from the wrong variable: services `engine`, jobs `params`, scripts `out` | `core/utils/auto_name.py:155-172` | Every Studio list is mislabelled |
| C14 | Script runs are untraced by default and go to `/tmp/operonx_traces/adhoc` even inside a project | `telemetry/consumers/local.py:83-104` | Not visible in Studio |
| C15 | The trace keeps an LLM call's template and variables, not the messages sent: `_extract_trace_io`, which renders them, has no caller | `core/ops/base.py:783-803` | Studio has to reconstruct the prompt |
| C16 | No fake LLM provider; `operonx init` inside a uv project installs PyPI operonx, not the checkout | `providers/llms/factory.py`, `cli/init.py` | Every template hand-rolls an HTTP server |
| C17 | 21 reserved op keywords collide with domain names (`id`, `start`, `stream`…) | `core/ops/_params.py` | `@op def f(id)` warns |
| C18 | Dead code from the dropped Rust runtime: `operonx pack` | `cli/pack.py`, `graph_op.py:857` | Traceback on any looping graph |

### 1.1 Correctness (W0, about one week)

| Change | Test that fails today |
|---|---|
| **C2:** the batch branch does not yield when the child recorded an error and the declared outputs are all None (mirrors the streaming branch); add `"<graph>.<sub>"` to `$errors` | `test_subgraph_failure_stops_successors`; serve test expects 500, not `200 null` |
| **C3/C4:** `_pump` catches `BaseException` and wakes every waiter; `cancel()` marks done; `run()`/`stream()` cancel the handle when their await is cancelled | `test_cancel_wakes_waiters`; `test_run_timeout_cancels_graph` (`after == 0`) |
| **C1:** `_flush_collect` merges only the collected vars and does not re-fire push-refs | `test_collect_reducer_no_double_write` (list and dict reducers) |
| **C5:** key = graph fingerprint + op qualname + code hash + `blake2b(orjson(inputs))`; bounded LRU | `test_cache_isolated_across_graphs_with_same_names` |
| **C6:** failed coercion is a field error, so `max_retries` fires; bool accepts only true/false/yes/no/1/0; JSON finds the first fenced or balanced block; XML retries with `&` escaped; add `parser="json_schema"` (native `response_format`) | `test_parsing_strict` over every case in `evidence/repros/parse_probe.py` |
| **C7:** `validate_graph` checks `Ref(op, key)` against statically known outputs, with "did you mean" | `test_unknown_output_key` |
| **C8:** the cap records `LoopLimitExceeded` in `$errors`; `if_(..., max_iterations=N)` | `test_loop_cap_reports_error` |
| **C9:** add `mode="interrupts"`; `updates` yields `InterruptEvent`; fix the docstring | `test_stream_updates_surfaces_interrupt_event` |
| **C18:** delete `operonx pack` and the Rust serialize path | CLI help test |

Every repro in `evidence/` becomes a regression test in the same PR as its fix.

### 1.2 Debuggability (W1)

- **C12:** `$errors[op] = {type, message, count, first_ctx}`; tracebacks trimmed to user frames in the message,
  kept whole in the trace; a structured `LLMOp` failure lands in `$errors`; `meta.json` gets `status`.
- **C13:** Job and serve pass the graph's own name; `auto_name` stops scanning lines above the call and falls back
  to the graph function's `__name__`.
- **C14:** `resolve_root` searches upward for `operonx.toml`; inside a project, `Operon()` traces locally by
  default.
- **C15:** record the rendered request on LLM executions: call `normalize_trace_io` when the `OpExecution` is
  built, store `messages` beside the variables. Gate it with the existing `include/exclude` rules.
- **C10, C11:** the final stream frame sets `final=True` with an empty delta (`full_content` carries the text);
  websocket decodes JSON by default; bad bodies get 400 before a run is minted; every reply carries
  `x-operonx-trace-id`.

### 1.3 Developer experience (W2)

- **C16:** ship `api_type: fake` (scripted text, tool calls, status codes, delays, streaming); templates use it;
  `operonx init --editable PATH` and detection of an enclosing uv project.
- **C17:** op settings move behind `op.configure(...)`; deprecate in 1.x, remove in 2.0 (decision D4).
- Logging: `OPERONX_LOG_LEVEL`, stderr handlers, configurable slow-op threshold (LLM and IO ops exempt).
- `operonx serve --host --port --reload`, default host 127.0.0.1 in development, `/healthz`.
- `out["$cells"]` for final reduced values; rewrite the stale `Operon` docstring; one import path per symbol.

### Do not build (core)

- A Pregel superstep barrier, or `Command(goto=…)` routing from inside op bodies.
- Seven stream modes plus versioned stream APIs. Keep four modes, add `tasks` and `interrupts`, allow several at once.
- Pickle anywhere (cache keys, state, journal).
- Rust or any second runtime.

---

## 2. Production runtime capabilities

### Current problems

No per-op retry or timeout (only jobs retry, with no backoff). Errors are recorded and execution continues, with no
error routing and no fail-fast mode. Nested `concurrency` limits multiply (2 × 2 gave 4 concurrent ops) and there is
no per-resource rate limit. Two concurrent writers to a plain cell race silently. The checkpointer only observes, in
memory. A crashed process loses the run, and a rerun repeats its side effects. Interrupts are in-process futures
with `uuid4` ids. Traces are written only when a run ends. Webhook `202` events and `schedule` triggers live in one
process with no lease.

### Proposed architecture

```
R1 failure semantics ──► R2 identities + events ──► R3 journal ──► R4 platform
   retry/timeout          RunContext                 resume          threads
   error edges            idempotency key            durable HITL    run queue
   errors="raise"         interrupt ids              graph fp        cron lease
   shared limiter         multi-mode stream          drain           reconnect
   race lint              live traces                                fork (R5)
```

**R1 (W1): failure semantics.** New `operonx/core/policy.py`:

```python
@op(retry=Retry(max_attempts=4, on=TRANSIENT), timeout=Timeout(run=30, idle=5))
async def call_crm(order_id: str) -> dict: ...

Operon(g, errors="record" | "raise", max_concurrency=32)   # one limiter shared by nested graphs
call_crm(order_id=x).on_error(handler)                      # error edge: handler(error, op, inputs)
```

- Retry wraps `_exec_core` in `BaseOp.run`. A generator is retried only before its first yield, so frames are never
  duplicated. Each attempt is its own `OpExecution(attempt=n)`.
- Timeout: `asyncio.timeout` per attempt; `idle` between yields for generators. `bound="cpu"` ops abandon the
  thread and record `TimeoutError`.
- `errors="raise"` sweeps the root context on the first error and raises `OpFailed(op, error)`. Live sessions keep
  today's record-and-continue default.
- Build-time lint: two writers to a reducer-less cell with no path between them → `BuildError`, opt out with
  `allow_race=True`.
- `rate_limit:` per resource in `resources.yaml`, enforced by the shared limiter.

**R2 (W2): identities and events.**
- A read-only `RunContext` contextvar: `run_id`, `thread_id`, `op_path`, `ctx`, `attempt`, `deadline`,
  `idempotency_key = blake2b(run_id, op_full_name, ctx)`. Information only; control flow stays as visible nodes.
- Deterministic `interrupt_id = hash(run_id, op_full_name, ctx)`.
- `engine.stream(mode=["updates", "custom", "tasks", "interrupts"])` yields `(mode, chunk)`.
- Live traces: `Consumer.on_execution(exec)`, run status `running`, ClickHouse and SQL stores append as ops finish.
- Child executions (`await invoke(op, **inputs)` inside an op records an `OpExecution` under the parent ctx) and
  `OpExecution.attrs` for GenAI attributes. These are prerequisites for the agent framework.
- Providers: one normalised tool-call shape, native structured output, per-resource timeout, logprobs passthrough.

**R3 (W3): durable execution through a scheduler journal.** The scheduler's bookkeeping changes only on `Frame`,
`EOF` and `Interrupt` events. Persisting those events in arrival order, with the post-reducer cell writes the event
bus already emits, is enough to rebuild a run exactly. New package `operonx/durable/`:

```python
engine = Operon(g, journal=SqliteJournal("runs.db"), durability="sync" | "async" | "exit")
h = engine.start(inputs, run_id="order-42", thread_id="cust-7")
await engine.resume("order-42")                                   # after a crash, on any worker
await engine.resume("order-42", answers={interrupt_id: {"approved": True}})
```

- Replay skips ops with an `EOF` in the journal and re-emits their recorded frames in sequence order.
- An op that was in flight runs again (at least once) and gets the idempotency key to deduplicate external calls.
- A generator that partly ran: `on_resume="restart"` replays and checks the first k yields by hash
  (`NonDeterministicResume` on a mismatch), or `"fail"`.
- `InterruptOp` with a journal parks the run: the coroutine ends and `resume(answers=…)` drives it on.
- The graph fingerprint is stored; resume refuses a changed graph unless `allow_graph_change=True`.
- `handle.drain()`: no new dispatches, finish in-flight ops, status `drained`, resumable.
- Off by default. `journal=None` costs one `is None` check, so the live callbot stream path is untouched. Ingress
  graphs can be journaled for audit but are not resumable.

**R4 (W4): platform.** Threads (`carry=[cells]` between runs), a Postgres `runs` table with `SKIP LOCKED` workers,
webhook events persisted before the `202`, `schedule` with a lease row, a per-thread second-message policy
(`reject | enqueue | interrupt | rollback`), stream reconnect `?after_seq=N`, completion callbacks. **R5 (later):**
`history(run_id)` and `fork(run_id, at_seq, patch)` from the journal.

### Tests

- R1: `test_retry_transient_then_success`, `test_generator_not_retried_after_first_yield`,
  `test_timeout_records_and_retries`, `test_idle_timeout_generator`, `test_error_edge_handler_runs_once`,
  `test_errors_raise_mode_cancels_siblings`, `test_build_rejects_concurrent_writers`,
  `test_nested_concurrency_shared_cap`, plus a callbot-shaped benchmark with no policies set that must stay
  within noise.
- R2: `test_trace_visible_while_running`, `test_killed_run_leaves_partial_trace` (subprocess + SIGKILL).
- R3, the core proof: a **property test** that generates random graphs (generators, `.parallel`, `.collect`,
  branches, loops, soft edges), kills them at a random journal position, resumes, and asserts the output equals an
  uninterrupted run. Plus `test_crash_resume_skips_completed`, `test_durable_interrupt_across_processes`,
  `test_replay_reproduces_reducer_and_race_outcomes`, `test_graph_change_refuses_resume`, and a contract suite
  shared by the in-memory, SQLite and Postgres journals.
- R4: `test_webhook_event_survives_restart`, `test_schedule_fires_once_across_two_workers`,
  `test_thread_carries_cells_between_runs`, `test_stream_reconnect_after_seq`.

### Do not build (runtime)

- Snapshot-per-step checkpoints and delta channels. The journal is append-only with periodic compaction.
- `interrupt()` called inside an op body that re-runs the op from the top on resume. The visible `InterruptOp`
  node becomes durable instead.
- A config-dict for runtime injection. Use a plain contextvar.
- A closed platform server. Queue, threads and cron stay open and self-hostable on Postgres.
- Temporal or DBOS as a required dependency. An adapter is a "later" item for the agent layer only.

---

## 3. Workflow evaluation

### Current problems

Evals exist since 1.9.0: `Eval` is a `Job` with `origin=eval` (`operonx/app/evals.py:342`), with JSONL datasets,
five checks plus an LLM judge, a pass-rate gate and a non-zero exit code. Studio has Evals, Datasets and Review
screens. The gaps: evaluators never see the trace (`runner.py:242` drops it); verdicts live only in local
`items.jsonl`, so CI experiments never reach Studio; no experiment identity; no repeats, so flaky cases look like
regressions; no confidence intervals; the judge is untraced, uncached and unversioned; no online eval; no
`operonx eval` CLI; no pytest plugin.

### Proposed architecture

```
Dataset (git JSONL, content hash)
  → Experiment (an Eval job run, with a fingerprint: git sha, graph hash, config hash, dataset hash, evaluator versions)
  → Run + trace (the existing run store)
  → TraceView (same object from a live trace or a stored run)
  → Evaluators (code checks, trajectory, tool-call, budget, LLM judges as traced graphs) — run concurrently
  → Scores (one row type: code, judge, human, online, pairwise; idempotent ids) → ScoreStore
  → Metrics with CI (Wilson, clustered SE) → paired comparison (bootstrap, McNemar)
  → Gate: pass / regressed / inconclusive (+ must-pass tier, infra exit code 3)
  → Report (Markdown, JSON, JUnit) + Studio
```

- **Placement:** core `operonx`, no new dependencies, statistics in pure Python. `operonx.app.evals` becomes a
  package and keeps its import path. `ScoreStore` lives in `operonx/telemetry/scores/`: files + SQLite locally,
  ClickHouse schema v3 (`experiments`, `experiment_items`, `scores`, `judge_cache`) for teams and CI.
- **Evaluators keep their signature** and may also take `trace: TraceView` for trajectory matching
  (strict / unordered / subset / superset), tool-call arguments, per-op outputs and budgets.
- **Judges** are operonx graphs, traced with `role=judge`, versioned (rubric + model + graph hash), cached,
  binary by default, pairwise with position swap. A judge must show agreement with human labels (Cohen's κ,
  TPR/TNR) before it gates silently; reports warn when κ < 0.6 or no alignment exists.
- **Repeats** classify cases as stable-pass, stable-fail or flaky, with pass^k. `calibrate` measures the
  same-version noise floor; `power` says how many cases a given drop needs (a 5-point drop needs about 312).
- **Online eval** is a scheduled Job over the run store: hash sampling, a daily budget, backfill, idempotent
  score ids. Never inline in a service. Failures feed review queues; score metrics feed the existing alerts.
- **Surfaces:** `operonx eval run|compare|report|rescore|calibrate|power|align|dataset|online`, an opt-in pytest
  plugin, CI snippets with baseline lookup against the merge base.

### Phases and tests

| Phase | Deliverable | Gate |
|---|---|---|
| E0 (W0) | `docs/EVALS_PLAN.md`; callbot QC cases run 3× on one sha | Measured flip rate sets default `repeats` |
| E1 (W1) | Fingerprint, repeats, `stats.py`, three-state gate | A/A simulation: same version reported "regressed" ≤ 5%; a true 10-point drop on 300 cases caught ≥ 80%; all 11 existing eval tests unchanged |
| E2 (W2) | `TraceView`, trajectory/tool/op-output/budget evaluators, `rescore` | `from_trace(live) == from_rows(stored)` golden test |
| E3 (W2) | `ScoreStore` (files, SQLite, ClickHouse v3) | An experiment run on host A is visible on host B; a ClickHouse outage loses no verdicts |
| E4 (W2) | CLI, reports, pytest plugin, CI | Exit-code matrix 0/1/2/3; a real MR shows the JUnit widget |
| E5 (W3) | Judges traced, versioned, cached, pairwise, aligned | κ measured on 100 human-labelled callbot reviews |
| E6 (W3) | Studio experiments, compare, case drill-down, dataset editor | Every screen opens in one hop; desktop + phone screenshots |
| E7 (W4) | Online eval, queues, score alerts, trends | One week of staging traces at 5%: judge spend within budget, service latency unchanged |
| E8 (later) | Multi-turn and simulated-user cases (callbot QC port) | Pass rates match the old harness on the same sha |

### Do not build (eval)

A second scheduler for evals (Job is the runner); datasets that live only in a database; 1–10 Likert scales as the
default judge; one judge checking several criteria; a metric zoo (Ragas, BLEU, BERTScore) in core; evaluators
inline in services; a query language over scores; vendor SDKs (LangSmith, Braintrust, DeepEval) in core; an
auto-loaded pytest plugin; diffs reported without a confidence interval.

---

## 4. Agent framework: `operonx-agents`

### Current problems

`operonx.agents` is about 4.7k lines and already enforces the right invariants: one tool result per call,
fail-closed permissions, compaction that keeps calls and results together, cache-stable prompts. Underneath:

- **Security hole:** `TOOL_REGISTRY` is process-global (`agents/tool.py:289`), dispatch looks names up there
  (`dispatch.py:774`), and the default policy allows non-destructive tools (`policy.py:352`). Any agent runs any
  tool registered anywhere in the process if the model names it.
- The ReAct loop is a graph back-edge, and most features needed a workaround for it (`react.py:168, 550`,
  `memory_ops.py:162`, `model_ops.py:137`).
- Nothing is durable: approvals are in-process futures, sessions are lists, the checkpointer observes.
- A destructive tool with no approval handler waits 300 s silently; tool arguments are never validated; tool
  calls and sub-agents are untraced; a 3-call agent trace is 70 records, mostly plumbing.
- The callbot, the one production consumer, uses none of it. Its real needs are a deadline, model fallback,
  runtime enum constraints, cancel-safe commits and redaction.

### Proposed architecture

A separate repo and package, `operonx-agents`, with two front doors:

- **`llm_step`**: a typed op with a deadline, model fallback, runtime `Choice(from_input=…)`, logprobs
  confidence and `on_timeout`. This is what the callbot needs.
- **`Agent` + `Runner`**: an explicit loop inside one op (decision D1), with model calls, tool calls and
  sub-agents recorded as child executions. A serialisable `RunState` saved at turn boundaries handles approvals,
  crash recovery, sessions and cancel-safety with one mechanism. A non-idempotent tool in flight at a crash is
  answered "outcome unknown", never re-run.

```
operonx_agents/
  tools/      tool.py (typed from signatures, ModelRetry), toolset.py (per agent), dispatch.py, policy.py
  model/      model.py (fallback, normalised usage), output.py (native | tool | prompted)
  step.py     llm_step
  run/        agent.py, runner.py, state.py (RunState, StateStore), limits.py (UsageLimits), events.py
  context/    session.py (memory | redis | sql), compaction.py (persisted summary), prompt.py
  compose/    as_tool, as_op, handoff (later)
  safety/     hooks.py (before/after model, before/after tool), redaction.py
  mcp/        stdio + streamable HTTP (2026-07-28 stateless revision), annotation gating
  serve.py    agent_service, SSE/WS events, /resume
  evals.py    trajectory evaluators on TraceView
```

### Must-have / later / avoid

- **Must:** typed tools with argument validation; per-agent toolsets (closes the security hole); `llm_step`;
  `Model` with fallback and native/tool/prompted structured output; a runner with turn budget and parallel tool
  calls; `RunState` + resume, HITL as interruption data; `UsageLimits` including cost and wall clock; sessions
  with persisted compaction; hooks as guardrails; agent-as-tool; typed event stream; child-step tracing with GenAI
  attributes; MCP over stdio and streamable HTTP.
- **Later, on a concrete trigger:** handoffs, OTel exporter, skills with progressive disclosure, Letta-style memory
  blocks, A2A, MCP elicitation/sampling, Temporal/DBOS adapters, DSPy-style prompt optimisation, speech-to-speech
  adapters.
- **Avoid:** agent base classes; a global tool registry; crews, group chat and swarms; a framework-owned planner;
  RAG bundled in the agent layer; unsandboxed code execution; long in-process approval futures; the `Heartbeat`
  timer (the serve `schedule` trigger does it); YAML-defined agents; auto-written long-term memory without review.

### Phases and tests

| Phase | Deliverable | Gate |
|---|---|---|
| A0 (W1, 4 days) | Spike: back-edge loop vs 150-line in-op loop; prototype child executions; `llm_step` deadline on the in-house gateway; native structured-output support per endpoint | ≤ 2 ms/turn at 5 concurrent runs; trace screenshots desktop + phone; you decide D1 |
| A1 (W2) | Core prerequisites in operonx (R2: child executions, attrs, op deadline, raise mode, provider normalisation) | Tests listed in §2 |
| A2 (W2–3) | Tools, Model, `llm_step` | Schema generation across 25 signature shapes accepted by OpenAI and Anthropic validators; **callbot shadow replay of 200 recorded turns: ≥ 99% intent agreement, p95 no worse** |
| A3 (W3) | Runner, RunState, limits, events, sessions | Kill between "in flight" and "tool finished": idempotent tool re-runs, non-idempotent gets "outcome unknown"; cancel mid-turn writes nothing |
| A4 (W3–4) | Approvals, composition, safety, MCP | An approval survives a restart; a model naming another agent's tool gets "unknown tool" |
| A5 (W4) | Serve, Studio, evals, migration shims | HTTP + WS end to end with an approval round-trip; screenshots |
| A6 (W4) | Callbot adopts `llm_step` on `refactor/operonx-studio` only | Shadow diff + 5-CCU load test, TTFA p50 ≤ 0.86 s |

Every phase includes at least one run against a real model, not only scripted ones.

---

## 5. Knowledge and document platform: `operonx-kb`

### Current problems

OperonX has the retrieval primitives (`EmbeddingOp`, `RerankOp`, `VectorSearchOp`, `DocFetchOp`, `Media`) but no
ingestion, chunking, BM25/hybrid, upsert or delete op, versioning or citations. `BaseVectorStore` has no
`delete` (`vector_stores/base.py:31,60`). An unknown resource category silently loads as a raw dict
(`resource_hub.py:242-246`). The Anthropic provider drops native citations (`anthropic.py:232-235`). Searching an
empty index is silent.

### Proposed architecture

A separate repo, `operonx-kb` (import `operonx_kb`), sibling of Operon. Every stage is an operonx op; ingest,
query and maintenance are `@graph`s run as Job, Service and Runbook. No new runtime.

```
Connector → Parser → Structurer → Chunker → Enricher → Encoders → Index
   (files,     (Docling,  (element tree    (structural,   (contextual,   (dense,     (FAISS, pgvector,
    S3, web)    text,      with page        evidence       summaries,     BM25,       FTS5, PG FTS;
                Office)    + bbox spans)    units)         entities)      pages)      Qdrant later)
                                                    ↓
Query: Retriever(s) → Fuser (RRF) → Reranker → hydrate through catalog → Context → Answer → Verify citations
```

- **Three-tier storage.** Catalog (SQLite locally, Postgres in production) is the store of record: documents,
  versions, element tree, chunks, spans, enrichments, graph. A SHA-256 blob store holds files and page images.
  Indexes are derived and can be dropped and rebuilt. Every hit is hydrated through the catalog, which is the
  consistency gate, so updates and deletes need no distributed transactions.
- **One provenance invariant.** Each version has one canonical text. Every element and chunk carries character
  spans into it, and every span maps to page + bounding box. A citation is a span, so it can be verified and
  clicked; eval labels are quotes, so they survive a chunker change.
- **Cheap updates.** Content-derived chunk ids stable across versions; hash-keyed parse, enrichment and embedding
  caches; an unchanged paragraph is never re-embedded. A model or settings change builds a new index generation
  that replaces the old one only after an eval passes.
- **Retrieval modes, one contract** `(query, collection, filter, k) → hits`: hybrid dense + BM25 (default only
  if it wins), tree retrieval (PageIndex-style, from the element tree), graph (cheap co-occurrence first), visual
  page retrieval (fused with text).
- **Filters:** a closed `KBFilter` compiled per backend, with a conformance test that includes tenant leakage.

### Phases and tests

| Phase | Deliverable | Gate |
|---|---|---|
| K0 (W0–1) | `PLAN.md`, model, hashing, ids, span utilities, fakes | Span invariant on 100% of golden docs |
| K1 (W1–3) | Catalog, blob store, parsers, chunkers, dense index, ingest Job, deletes, rebuild, verify | Re-ingest of an unchanged 200-doc corpus: 0 embed calls; one-paragraph edit: ≤ 3 re-embeds; purge leaves 0 entries |
| K2 (W4–6) | Lexical, hybrid, rerank, citations, `KBFilter`, eval sets (incl. ≥ 100 Vietnamese cases) | Hybrid becomes default only if it beats dense on Recall@10 on 2 of 3 sets; citation precision ≥ 0.9 |
| K3 (W7–8) | Admin API + Studio Knowledge tab | 20/20 sampled citations open the right page and box |
| K4–K6 (W9+) | Contextual enrichment and tree, multimodal, graph | Each default-on only with a measured lift. K4 built (gate open: credits), K6 merged (opt-in). **⚠️ K5 multimodal OUTSTANDING — needs a GPU machine; see operonx-kb `BACKLOG.md`** |

### Upstream asks to operonx

U1 `BaseVectorStore.delete()` + `VectorUpsertOp`/`VectorDeleteOp`; U2 entry-point plugin discovery for resource
categories and an error (not a raw dict) for unknown ones; U3 carry provider-native citations in `extras`;
U4 (later) sparse embeddings; U5 remove the dead `RerankingType.COHERE`; U6 move `MediaStore` out of `telemetry`.
The KB ships small shims for U1 and U2 so it never waits on a release.

### Do not build (knowledge)

Content in vector payloads, or any index as the source of truth; LangChain, LlamaIndex or Haystack as
dependencies; a god `rag()` graph with a long kwargs list; an open filter DSL; whole-document re-indexing as the
update strategy; GraphRAG community summaries by default; Neo4j before tables prove insufficient; a parser's type
(`DoclingDocument`) as the core model; non-permissive parsers or weights (MinerU, Marker) in defaults; our own OCR,
layout or embedding models; vision-only or vectorless-only defaults without eval evidence; Studio importing the KB
package (HTTP contract only).

---

## 6. Studio integration

### Done (studio PR #11, branch `feat/trace-io`)

The Langfuse-grade input/output view, keeping the Tree, timeline and Workflow views:
- An LLM call's input is its conversation, role by role, rendered from the template and recorded variables with
  LLMOp's own rule (`str.format_map`); unrecorded variables stay `{name}` chips. Variables, template and knobs in
  folds.
- Its output is the reply (JSON pretty-printed, tool calls, thinking) with a facts line: model, tokens, cost,
  stop reason. Parsed fields follow.
- Every other value reads as text, labelled fields, chips or media. Formatted/JSON switch; widen button.
- 59 JS tests (9 new), 401 studio tests; checked on real callbot ClickHouse runs, desktop + phone + dark.

### Next, by wave

| Wave | Studio work | Depends on |
|---|---|---|
| W1 | Script runs listed (C14); correct run names (C13); structured errors with trace-id links (C12) | §1.2 |
| W1 | Show recorded `messages` when present, falling back to the template rendering (C15) | §1.2 |
| W2 | Live runs: `running` status, nodes appear as ops finish | R2 live traces |
| W2 | Agent view: internal plumbing ops collapsed into `turn → model → tool calls`, ordered by wall time | R2 child executions + `internal` marker |
| W2 | Retry attempts and timeouts on execution rows | R1 |
| W3 | Experiments with CI, A/B compare, case drill-down to the blamed op, dataset editor with case history | E3–E6 |
| W3 | Interrupted runs: parked approvals with an Approve/Deny action that calls `resume` | R3 |
| W3 | Knowledge tab: collections, documents, page viewer with bbox overlays, chunk inspector, query playground with clickable citations | K3 (admin HTTP API) |
| W4 | Online score trends, review queues with rubrics, judge alignment | E7 |
| W4 | Journal history and fork from a step | R5 |

Every Studio change ships with desktop and phone screenshots.

### Do not build (Studio)

A tree-of-spans clone of Langfuse (we keep timeline + workflow); Studio importing `operonx-kb` or
`operonx-agents` (HTTP and the run/score stores only); a second trace format; per-screen bespoke data fetching where
one hop serves the page.

---

## 7. Dependencies between tracks

```
§1.1 correctness ─► R1 failure semantics ─► R2 RunContext/child execs/streams ─► R3 journal ─► R4 platform
        │                    │                     │            │                    │
        │                    │                     │            └─► A1 ─► A2 ─► A3 ──┴─► A4 (durable approvals)
        │                    │                     └─► E2 TraceView (agent trajectories)
        └─► E1 statistics (independent)            └─► Studio live runs + agent view
U1/U2 upstream asks ─► K1 (shims let K start in W1)
E3 ScoreStore ─► Studio experiments;  K3 admin API ─► Studio Knowledge tab
```

- Nothing in evaluation or knowledge waits on the journal.
- The agent framework's durable approvals (A4) need R3; its first win (`llm_step`, A2) needs only R2.
- The live callbot path stays unchanged until A6, and only on `refactor/operonx-studio`.

## 8. How each item ships

- A plan doc is committed before a large phase (`docs/EVALS_PLAN.md`, `docs/AGENTS_V2_PLAN.md`,
  `operonx-kb/PLAN.md`, `docs/DURABLE_PLAN.md`).
- One branch per phase, commits batched, merged when the phase gate is recorded.
- Every claim is backed by a measurement or a failing test first. A benchmark has to measure the shape being
  decided.
- Every guide snippet stays executable (`tests/guide`).
