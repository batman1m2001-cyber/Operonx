# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.17.5] - 2026-10-08

### Added

- `operonx studio [DIR]`: open the project at or above here in operonx-studio. It starts the
  studio when none is running and otherwise hands the project to the running one, which adds it
  to its list and shows it. operonx never imports the studio: the command finds `operonx-studio`
  and says how to install it when it is missing.
- Guide page 9, "Seeing a project": installing operonx-studio from its repository
  (`install.sh`), opening a project, reading the canvas (Workflow and Data Flow views) and what
  each page is for. `operonx init` copies it into new projects with the rest of the guide, and
  ends with how to open the project. The install docs gain a studio section.

## [1.17.4] - 2026-10-06

### Changed

- The guide, the init AGENTS.md and the auto-synced `<!-- operonx:guide -->` block say how to
  upgrade a project: `uv lock --upgrade-package operonx && uv sync`, then `uv run operonx guide`.
  The block also carries the naming and `if_` rules, so existing projects get them at their next
  sync.

## [1.17.3] - 2026-10-06

### Fixed

- **Tuple unpacking named one op on Python 3.11+.** In `a, b = f(), g()` the second op was named
  `b` and the first kept its function name; on 3.10 neither was named. Neither is now, on every
  version, so names (and state keys, trace names) no longer depend on the Python version. Chained
  `x = y = f()` keeps its name. This was the Python Compatibility CI failure.
- The docs build: `docs/api/app.md` no longer references the removed `JobSpec`, and the agents
  guide is linked by URL.

### Changed

- The guide and the `operonx init` AGENTS.md say: let names come from variables (`name=` only when
  other code reads the name, one op per line), and branch with `if_(...).else_(...)` inline, never
  a hand-built `BranchOp`.

## [1.17.2] - 2026-10-06

### Fixed

- Projects made by `operonx init` pass `ruff format --check` under ruff 0.16, which also formats
  Python inside Markdown: the template excludes the generated `.operonx/` guide copy, and the
  `AGENTS.md` example is formatted. The repository is formatted with ruff 0.16 (dev pin
  `ruff>=0.16`), so CI's format check passes again.

## [1.17.1] - 2026-10-06

### Changed

- **The guide follows the installed packages.** Each operonx package ships its own guide and
  registers it under the `operonx.guides` entry point (operonx-agents 0.1.4: `agents`; operonx-kb
  0.2.5: `kb`, a new page). A project's `.operonx/guide/` holds one folder per installed package
  plus a generated `README.md` index; a sync adds, updates and removes folders to match.
- `operonx guide [DIR]` syncs (it printed the index before); `--check` exits 1 when the copy is
  stale, for CI; `--sync` is still accepted. Any `operonx run|serve|play|eval` inside a project
  with a guide copy syncs a stale one first and says so in one line on stderr.
- `AGENTS.md` keeps a marked `<!-- operonx:guide -->` block naming the installed packages; it is
  appended once to an existing `AGENTS.md`, and the rest of the file is never touched.
- Guide page 09 (agents) moved to operonx-agents (`agents/01-agents.md`).
- `operonx.guide.testing`: the snippet runner and stand-in model, for a package to test its own
  guide.

## [1.17.0] - 2026-10-06

### Changed

- **One `Job`.** `Job(items=...)` loops over a list, an iterable or async iterable, a function that
  yields items (called on every run: the custom loader), or a `.jsonl` path. Every result is kept
  in the record's `results.jsonl` and read back as `run.results`; a resumed run carries the earlier
  results for the keys it skipped. `output=` (a `.jsonl` path or `fn(key, result)`) exports each
  result as it finishes. `reduce=graph` runs once over every result (`run.reduced`).
  `steps=[...]` runs jobs in order as one command. `retry=Retry(...)` and `timeout=` per item.
- A graph without doors is bound by name (dict items fill parameters; `input=` hands the whole item
  to one parameter; a non-dict item goes to the only free parameter).
- Runs are recorded under the project root by default: `.operonx/jobs` (`[jobs] dir` overrides),
  `.operonx/evals` for evals.
- `operonx run <job> --items file.jsonl` replaces `--source`/`--sink`.
- `pytest` skips tests marked `slow` (real sleeps, subprocesses, the guide snippets) by default;
  `-m slow` or `-m ""` runs them.

### Removed

- `Runbook`, `Sequential`, `Parallel`; every source and sink class and the `source:`/`sink:`
  resource categories; `session="stream"` jobs; `schedule=`; `on_error="retry:N"`/`"record"`;
  `item_input=`, `item_timeout=`.
- `[[job]]` blocks in `operonx.toml`, TOML-declared evals, `Eval.from_spec`,
  `OnlineEval.from_spec`, `Gate.from_options`: declare jobs and evals in `Application(jobs=[...])`.
  A `[[job]]` block is refused with a pointer to `app/main.py`.
- The `operonx.core.jobs` alias. See MIGRATION.md.

### Fixed

- A Python-declared `Application` kept the manifest's `[[queue]]` blocks.
- `operonx eval run --strict` (and other flags) no longer changed the declared eval in-process.

## [1.16.0] - 2026-10-06

### Removed

- **The built-in `operonx.agents`** (deprecated since 1.14). `operonx.agents` now names the
  operonx-agents package; see MIGRATION.md.
- **Graph factories as a door's graph.** `variants=` bind a module-level `@graph`'s parameters; a
  plain function that returns a graph is refused, naming guide 05.

### Added

- `operonx.agents` and `operonx.kb`: the separately installed operonx-agents and operonx-kb by a
  short name — the same module objects, submodules included; a missing one raises `ImportError`
  with its pip install.
- A provider op's `resource=` may be a graph input (`LLMOp`, `EmbeddingOp`, `RerankOp`,
  `VectorSearchOp`, `VectorUpsertOp`, `VectorDeleteOp`, `DocFetchOp`): the model or store is
  picked per run, each key on its own bound copy of the op, so concurrent runs stay apart.
- Guide 05: every `@graph` is defined at module level, never inside a function; the `operonx init`
  AGENTS.md says so too.

## [1.15.0] - 2026-10-05

### Added

- **Developer experience** (docs/DX_PLAN.md). `api_type: fake` — a scripted LLM (text, tool calls,
  an HTTP status raising the SDK's error, delays, echo, streaming in chunks; no network); the chat
  template's tests use it instead of a hand-rolled server. A keyword the op/graph function takes
  is its input (`@op def f(id)` called `f(id=7)` lost the 7 to the op's id); a colliding setting
  goes through `f.configure(...)(...)`. Results carry `$cells` (the root's declared cells, final).
  `OPERONX_LOG_LEVEL` (over `LOG_LEVEL`), logs on stderr, `OPERONX_SLOW_OP_MS`, and no "Slow op" for
  provider, graph, agent, door or generator ops. `operonx serve --host/--port/--reload`; every
  listener answers `GET /healthz`. `operonx init --editable PATH` (the default inside a checkout).
- **`rate_limit:` on a resource** (`resources.yaml`): `{concurrency: N, per_second | per_minute: M}`,
  one limiter per key for the whole process — every op, run and nested graph calling it waits its
  turn; an async generator method holds its slot until it ends; a call from inside another call to
  the same resource passes through. Applied to the instance the hub builds, so its class is unchanged.
- **Second messages, stream reconnect, completion callbacks** (R4b). A queued webhook's
  `multitask="enqueue"|"reject"|"interrupt"|"rollback"` decides what a second message on a busy
  thread does (`interrupt`/`rollback` stop the running run through its queue row, on whichever
  replica runs it). `callback_hosts=[...]` lets a request name `?callback=<url>`, POSTed `{run_id,
  service, status, output, errors}` when the run ends. A streamed `http` door numbers its events
  (`id:`); `?run_id=R&after_seq=N` (or `Last-Event-ID`) reads a dropped stream on from event N.
  The `serve` extra now includes `httpx`.
- **Durable triggers and a run queue** (`operonx.app.queue`, R4a). `Service(kind webhook|schedule,
  queue="runs.db")` (or `queue = {url = "postgresql://…"}`): a webhook writes the event to the
  queue before its `202`, and every replica claims events with a renewed lease — a replica that
  dies leaves a lease that lapses, and another runs the event again under the same run id (at
  least once; `max_attempts`, then `failed`). Rows of one thread run one at a time, in order. A
  schedule's ticks line up on the clock and each fires on one replica. `SqliteQueue` (one host)
  and `PostgresQueue` (`FOR UPDATE SKIP LOCKED`) pass one contract suite. `requeue(id)` retries an ended
  row by hand (same run id); `counts(service)` says how many rows are in each status.
- **Threads carry cells between runs:** `Operon(g, journal=…, carry=["history"])`; a run started
  with `thread_id=T` begins with T's declared cells as its last run left them and saves them when
  it ends. `start`/`run` take `thread_id=`.
- **Durable runs: resume after a crash** (`operonx.durable`). `Operon(graph, journal=
  SqliteJournal("runs.db"))` records each op's writes and events as it runs; after the process
  dies, `await engine.resume(run_id)` in a fresh process restores the cells, replays what had
  ended without running it again, and re-runs only what was cut off (a generator's earlier
  yields are checked against the journal: one that yields otherwise raises
  `NonDeterministicResume`). `durability="sync" | "async" | "exit"` picks when steps reach
  the journal; `engine.runs(status)` lists them; a changed graph is refused unless
  `allow_graph_change=True`. Values are journalled as tagged JSON (no pickle: reading a journal runs no code) and come back
  exactly — tuples, sets, bytes, dates, dataclasses, pydantic models, enums; any other type names
  its op and var.
  Without `journal=` nothing changes (no cost: same timing as before on a 3000-item stream).
  Guide 08 "Durable runs".
- **Durable approvals and drains** (R3b). With a journal, an `InterruptOp` parks the run instead
  of holding the process: the question is journalled, the ops in flight finish, nothing new
  starts, and the run ends `interrupted` with `"$interrupted": [{interrupt_id, op, ctx,
  payload}]` in its result. `await engine.resume(run_id, answers={interrupt_id: value})` — in
  any process — continues it (an unanswered question parks it again; an answer to a question
  the run never asked is refused). `await handle.drain()` stops a run for a deploy the same way
  (status `drained`, `"$drained": True`); `resume` continues it. A graph with an `ingress` door
  is journalled but `resume` refuses it. Proved by a property test: 500 runs drained at a
  random step resume to the uninterrupted result.
- **Online evals: production runs judged after the fact** (`operonx.app.evals.OnlineEval`).
  A `[[job]]` with `runs = {origin = "service", name = "call"}` instead of a `graph` reads the
  run store from a cursor, keeps a stable `sample` (by `sha1(trace_id)`), runs reference-free
  evaluators (one taking `expected` is refused) and writes scores with `rule = <name>` and a
  clipped `snapshot`. `budget_usd_per_day` caps judge spend per UTC day across hosts; failing
  runs can go to a review queue (`queue = {to = …}`). `operonx eval online backfill <name>
  --since 7d` judges a past window without moving the cursor. Nothing runs on the service's
  path. `TraceView.input` / `.output` give a stored run's request and answer.
- **Review queues** (`operonx.app.evals.queues`): `[[queue]]` in `operonx.toml` (`name`,
  `rubric`, `reviewers`); `review()` writes a person's verdict as `source="human"` scores, so
  `align` measures judges against reviews; `queue_agreement` reports κ between reviewers;
  `operonx eval queue add|list`; Studio's `reviews.jsonl` reads as scores
  (`reviews_as_scores`) and `operonx eval migrate-reviews` writes it to the score store.
- **Alerts on scores:** `score_mean:<score>` (fires when the mean drops below) and
  `score_fail_rate:<score>`, evaluated over the score store with `evaluate(..., scores=)`.

- **`child()`'s handle takes `redact`** (`dict -> dict`), applied to the
  step's inputs and outputs by every exporter — `row_of` (every run store),
  the Local consumer's view and the Langfuse consumer — through
  `OpExecution.exported()`. The record in memory keeps its values. For a
  step that saw a credential: `operonx-agents` scrubs its traces this way, at
  the cost of the exporter, where scrubbing on the event loop cost
  0.03–0.05 ms per turn of its 0.3–0.45 ms budget.

- **`OpType` names `"agent"`**, the type `operonx-agents`' `AgentOp`
  (`Agent.as_op()`) sets, so the Literal agrees with the ops that use it.

- **`child(..., current=False)`** records a step held open across an async
  generator's `yield` (a streamed model call) without making it the current
  frame. The consumer's code runs between the yields in the same context;
  with the default it ran inside the step, so its own `child()` blocks nested
  under the stream, and an abandoned, unclosed stream left every later step
  of the op nested under a dead record.

- **K7: provider contract for `operonx-agents`.**
  - `LLMOp`'s `tool_calls` output is one shape whichever provider answered:
    `{"id", "name", "args"}`, with `args` a dict, or the model's text when it
    is not a JSON object. `normalize_tool_call` / `openai_tool_call` in
    `operonx.providers.llms.base` convert; the backends send it back in their
    own wire form. Before, it was OpenAI's wire object, and the compaction
    summary read each call's `name` as `None`.
  - `timeout:` on an `llm:` resource bounds every network wait of a request
    (connect, send, each read). Before, a silent gateway held a call for the
    shared client's 120 s read timeout, and the key was ignored.
  - `structured_output: native | tool | prompted` on an `llm:` resource
    (default `prompted`) declares how it gives schema-shaped answers.
    `native` is refused on an `anthropic` resource.
  - Streamed answers carry `extras["logprobs"]` (they were always `None`);
    Azure sends `logprobs`/`top_logprobs` (they were filtered out).

- **`@op(retry=Retry(...), timeout=Timeout(run=, idle=))`** (R1, F13). A
  failed op runs again after a growing, jittered pause when the error is
  worth another try (`TRANSIENT`: a timeout, a connection error, HTTP 429 or
  5xx); a generator only before its first yield. An attempt past its
  deadline fails with `TimeoutError`, recorded like any failure, and a
  `bound="cpu"` op abandons its thread. Each attempt has its own trace node
  (`OpExecution.attempt`). `my_op(x=..., retry=..., timeout=...)` overrides
  either for one use; a subgraph takes `timeout=Timeout(run=)`. An error
  `LLMOp`'s transport retry already retried is not retried again by the op.
- **`Operon(g, errors="raise")`** (R1). The first op failure no error edge
  handles ends the run: the ops still running are cancelled and `run()`,
  `result()`, `collect()`, iteration and `stream()` raise `OpFailed(op,
  error)`. It is still recorded in `handle.errors` and the trace. A loop at
  its cap raises too. The default stays `errors="record"`.
- **Error edges: `op.on_error(handler)`** (R1). The handler runs once when
  the op fails (after its last attempt), with its `error`, `op` and `inputs`
  parameters fed the failure. The failure stays in `"$errors"`, and
  `errors="raise"` leaves a handled failure alone. An op and its handler
  merge by themselves, like branch arms (`[look, sorry] >> reply`).
  Serialized edges carry `"kind": "error"`; no other edge changes.
- **`Operon(g, max_concurrency=N)`** (R1). One cap on the ops of a run that
  are running at once, shared by every nested graph. A graph's own
  `concurrency=` caps only that graph, so nested graphs multiplied (2 × 2
  ran 4). Subgraphs hold no slot, and a producer parked on a full bounded
  edge gives its slot back, so `N=1` does not deadlock.
- **Concurrent writers fail the build** (R1). Two ops that may run at once,
  both writing a `PARENT.declare(...)` cell without a reducer, raise
  `GraphValidationError`: the cell kept whichever write landed last (probe
  P2 read `slow` in one run, `fast` in the next). Ordered writers, branch
  arms and an op with its error handler are fine;
  `PARENT.declare(..., allow_race=True)` (or a list of names) opts out.
  Checked against every graph in the repo and in callbot: no false positive.
- **A read nothing orders fails the build.** `s = use(y=f["y"])` with no
  path `f >> ... >> s` raised nothing: both started together and `s` got
  `f`'s value or a missing argument depending on timing. It is now a
  `GraphValidationError` naming the edge to draw (`START >> f >> s`); the
  push form `f["y"] >> s["y"]` is checked too. `PARENT[...]` and
  `SCRATCH[...]` reads are not. The only unordered reads in the repo,
  operonx's tests and callbot were the guide snippet demonstrating the bug.
- **`engine.stream(mode="interrupts")`, and `InterruptEvent.resume(value)`.**
  When an `InterruptOp` pauses, `mode="updates"` now yields its
  `InterruptEvent` after the updates that landed before it, and
  `mode="interrupts"` yields only the events. `event.resume(value)` answers
  the op (it outputs `response=value`) and returns whether it was still
  waiting. Events from `bind_interrupt_bus` have the same method.
- **`if_(..., max_iterations=N)`** sets the iteration cap of the loop the
  branch closes (default 1000). It is refused on a branch that closes no
  loop, and two different caps on one loop are refused.
- **An eval run is an experiment** (`docs/EVALS_PLAN.md`, phase E1).
  `run.json["eval"]` gains a `fingerprint` (git commit and dirty flag at
  the eval's root, `graph_hash`, `config_hash` over the resolved resources
  with secrets dropped, `dataset_version`, evaluator versions and
  `evaluators_hash`, `operonx_version`), `metrics` (each check and `pass`
  as a mean with a Wilson, CLT or clustered 95% interval) and a `gate`
  block with the verdict and exit code. Item verdicts carry `case`,
  `repeat` and `case_hash`.
- `Eval(repeats=N)`: N items per case (`<id>#<r>`), cases classed
  stable-pass / stable-fail / flaky, and pass^k. `Eval(cluster="field")`
  groups cases for the clustered SE.
- `operonx.app.evals.Gate`: per-metric thresholds, a baseline (`"latest"`
  or a run id) compared paired — exact McNemar and Newcombe's interval for
  0/1 checks, a seeded paired bootstrap otherwise, Holm across gated
  metrics, Benjamini–Hochberg for the rest — with a tolerance, a must-pass
  tag, an error budget and `strict`. Verdicts `pass`, `inconclusive`,
  `failed`, `regressed`, `error`; exit codes 0 / 0 (2 strict) / 1 / 1 / 3,
  which `operonx run <eval>` now returns. `[[job]]` evals read `repeats`,
  `cluster` and a `[job.gate]` table.
- `operonx.app.evals.stats`: Wilson, CLT and clustered SE, pass^k, exact
  McNemar, Newcombe's paired interval, a paired (cluster) bootstrap, Holm
  and Benjamini–Hochberg — pure Python, seeded.
- **Evaluators can read the run** (phase E2). An evaluator that takes
  `trace` gets a `TraceView` of the case's run: every op execution in
  order with inputs, outputs, status, timing and cost; `ops`, `first`,
  `last`, `path`, `llm_calls`, `tool_calls`, `errors`; and the run store's
  totals. A view of a live run equals the view of the same run read back
  from a store (`TraceView.from_rows` / `from_record` / `from_store`). An
  eval whose evaluators do not take `trace` pays nothing for it.
- `trajectory.ops` and `trajectory.tool_calls` (AgentEvals' strict /
  unordered / subset / superset, tool arguments exact / subset / ignore,
  the reference from the case's `trajectory`), `trajectory.op_output` (any
  check on one op's outputs, blaming its `op_id`), and `budget(ms,
  cost_usd, tokens, llm_calls)`.
- `rescore(run, evaluators, store=)` and `Eval.rescore(run_id)`: judge a
  recorded eval run again without running its graph. Verdicts note
  `output_clipped` when the record holds only a preview.
- A case's async evaluators run at the same time (50 cases × 3 async
  judges: 2.06 s → 0.74 s).
- **`operonx.telemetry.scores`: the ScoreStore** (phase E3). Experiments,
  experiment items and one `Score` row type for every judgement (code,
  judge, human, online, pairwise; targets item / trace / op / session /
  pair) with ids derived from what is judged. Backends `files` (JSONL + an
  SQLite index, the default), `sqlite` and `clickhouse`; `score_store:` is a
  resource. Contract: experiments upsert/list/get, items, scores,
  `score_series`, a judge cache.
- `Eval(scores=…, scores_timeout=10)` writes the experiment, items and
  scores through a background writer; a store outage loses no verdict (the
  job record has them). `publish(run, store)` sends a recorded run.
  `[[job]]` evals read `scores = "score_store:<name>"`.
- A verdict carries the case run's own `cost_usd`, `tokens_in` and
  `tokens_out` when it made LLM calls.
- The agent guide's `07-evals.md`, run by `tests/guide/`.
- `scripts/bench_eval_overhead.py`: what an eval costs over a plain job,
  per case and per run.
- **`operonx eval`** (phase E4): `run`, `compare`, `report`, `rescore`,
  `calibrate`, `power`, `list`, `dataset validate|stats|diff`. `run` exits
  with the gate's code (0 pass or inconclusive, 1 failed or regressed, 2
  inconclusive under `--strict` or a command that could not run, 3
  infrastructure), takes `--repeats`, `--split`/`--tag`/`--cases`/`--sample`,
  `--baseline`/`--tolerance`/`--strict`, `--variant`, and writes
  `--report md,json,junit`. Its experiment goes to the project's score
  store unless `--no-store`.
- `Gate(baseline="main" | "git:<ref>")`: the experiment of `git merge-base
  HEAD <ref>` from the score store (`"main"` is `git:origin/main`); none
  there stops the run before it starts, saying how to get one.
- `project_score_store(root)`: `[evals] scores = "score_store:<name>"`,
  else the ClickHouse sink of `[tracing]`, else files under the runs root.
- `load_experiment` / `ExperimentData` (one experiment from its record or
  the store), `compare(a, b, tolerance=)`, `calibrate(experiments)` (the
  A/A noise floor measured through the gate, per number of repeats),
  `stats.paired_sample_size` / `detectable_drop`, and `report.markdown` /
  `junit` (valid against `junit-10.xsd`) / `as_json`.
- `Dataset.select(split=, tags=, ids=, sample=)`, `Dataset.problems()`;
  `Eval(variant=)`; `run.json["eval"]` gains `p95_ms`, `cost_usd`,
  `selection` and `variant`.
- An opt-in pytest plugin, `operonx.app.evals.pytest_plugin` (never in
  `pytest11`): a session is one experiment, `run_case` runs a case, its
  verdict is the test's outcome, reports and the gate at the end.
- `Dataset.update(case_id, changes)` edits one case in place (`expected`,
  `tags`, `split`, `cluster`, `trajectory`, `note`, `status`), every other
  line byte for byte; `add()` and `update()` lock the dataset's folder.
  A case with `status: "archived"` stays in the file and is out of every
  run and of `dataset_version` (`docs/EVALS_PLAN.md` D63, D64).
- `experiments_of(…, items=False)` and `ExperimentData.from_experiment`:
  experiment summaries for a list, from one store query and each record's
  `run.json` (`JobRun.load(path, items=False)`) (D62).

- **`BaseVectorStore.delete(ids=None, filter=None, collection=None)`**,
  for FAISS (by id, on an id-mapped or IVF index), pgvector (by id or the
  search filter dialect; returns the row count) and Qdrant (by point ids
  or a condition tree, `wait=True`; returns `None`). Exactly one of `ids`
  and a non-empty `filter`: neither, both, or `{}` raise rather than read
  as "delete everything". `ids=[]` deletes nothing; a missing id is not
  an error. One contract suite runs against every backend
  (`tests/internal/providers/test_vector_store_contract.py`; live
  Postgres and Qdrant via `OPERONX_TEST_PG_DSN` / `OPERONX_TEST_QDRANT`).
- **`VectorUpsertOp` and `VectorDeleteOp`**, the write half of
  `VectorSearchOp` (op types `vector-upsert`, `vector-delete`).
- **`VectorSearchOp` output `empty_index`**, with a WARNING, when an
  unfiltered search returns no hits: the index holds no vectors. Before,
  an index nobody populated answered every query with three empty lists.
- **`operonx.resources` entry points.** A package declares the resource
  categories it registers, `[project.entry-points."operonx.resources"]
  my_category = "my_pkg.registry:register"`, and the hub loads that entry
  point on first use of the category; no import order needed.
- **Anthropic citations.** With citation-enabled `search_result` /
  `document` blocks, the completion's message (and a stream's last delta)
  carries `citations`: spans `{start, end, block_index, text, citations}`
  of the joined answer, with Anthropic's citations unchanged.
- `operonx.core.media_store`: `MediaStore`, `LocalMediaStore`,
  `detect_media` and `MediaInfo`, moved from `operonx.telemetry.media`,
  which still exports them.
- **`run_context()`** (R2). An op body reads the run it is in as a frozen
  `RunContext`: `run_id` (the trace id), `thread_id` (the `session_id` as
  given), `op_path`, `ctx`, `attempt`, `deadline`/`remaining` (from
  `Timeout(run=)`), the caller's `context` (`start`/`run`/`stream(...,
  context=obj)`), and `idempotency_key`, a hash of run, op and ctx that is the
  same on every attempt. Information only; `None` outside an op.
- **`child(name, inputs, op_type=)`** (R2, K1/K2). `async with child(...) as
  c:` records a step an op runs itself (a model call, a tool call) as an
  `OpExecution` under the op's record: ctx `parent + "name[n]"`, full name
  `parent + ".name"`, nested by every consumer and the studio with no stored
  link. A generator's child hangs under the yield being produced. `c.attrs`
  fills the new `OpExecution.attrs` (`gen_ai.*`); errors are recorded and
  re-raised, a cancellation is `cancelled`; the op's trace filter applies.
- **`engine.stream(mode=[...])` and a `tasks` mode** (R2). A list of modes
  yields `(mode, chunk)` pairs from one run. `tasks` yields `TaskStarted`,
  `TaskFinished` and `TaskFailed` (with the attempt; `retrying` when
  `retry=` runs another) per op invocation and per child execution.
- **Live traces** (R2). `Consumer.on_start(trace)` and
  `Consumer.on_execution(trace, execution)` see a run as it goes. The
  ClickHouse and SQL run stores use them by default (`live: false` turns it
  off): a run is listed as `running` and its executions land as they finish;
  a process killed mid-run leaves the run `running` with what it finished.
- **Guide page 8, "Inside a run"**: `run_context()`, `child()`, stream modes,
  live traces.

### Fixed

- A durable run's resume replays the `Interrupt`s its ops had yielded (they were not
  journalled, so a resumed run lost them); `Interrupt.SELF` survives pickling.
- **An online eval's budget holds when judges run at once.** Each judge checked what had been
  spent when it started, so judges running together all saw the same total: on 121 recorded
  calls a $0.05 budget spent $0.12. A judge in flight now counts at the day's cost per judged
  run, the first waits for a price to be known, and a run is judged only while its estimated
  cost fits — the same pass spent $0.056.

- **An online eval accepts the default judge.** `judge(...)` defaults to `reference="auto"`
  (it shows `expected` when a case has one), so it declares `expected`, and `OnlineEval`
  refused every default judge. It now refuses only what cannot judge without a reference: a
  judge with `reference=True`, or a function whose `expected` has no default.

- **A subgraph no longer hands on its own input as an output when the op writing it failed.**
  An output named like one of the graph's inputs shares that input's cell. When the op
  writing it raised — or never ran, because an op before it raised — the cell still held what
  came in, so the outputs were not all `None`, the failure went unnoticed, and the op after
  the subgraph ran on the graph's input as if it were its answer. An output now counts only
  when one of its writers finished without an error in that run; otherwise the subgraph
  fails as any other whose op raised (`SubgraphError` in `$errors`, its successors skipped).
  Declared cells are unchanged. A run without a failure pays nothing for the check.

- **A subgraph whose branch went around its stream hands on its output.** A subgraph with a
  generator handed its parent one output per stream context; a run that took a branch around
  the stream (`if_(misses > 0, each).else_(done)`) had none, so the op after the subgraph
  never ran, with no `$errors`. Such a run now hands on its one output at its own context. A
  generator that yields nothing still hands on nothing.

- **An op joining a stream's ops with ops outside the stream runs, once both have landed.**
  An op after a `.collect()` (or after a subgraph whose stream ends in one) that also waits
  for a sibling op never ran: the collect's context counted none of the sibling's arrival,
  and the run reported nothing. A per-item op waiting for a sibling outside the stream ran
  as soon as its item arrived, before a slow sibling had finished, without its value. A
  context below another now counts the parent's arrivals from ops outside its stream, those
  already in and those still to come.

- **A loop entered from a branch arm runs.** `START >> g >> if_(g["go"] == True, step).else_(skip)`,
  where `step` starts a loop (`if_(..., ...).else_(step)` below it), never ran the loop:
  the cycle rewrite moved `step` into the hidden loop, but the branch kept routing to
  the name `step`, so nothing after the branch ran and the run reported no error. The
  rewrite now retargets an outside branch's arm to the loop it enters.

- **The Anthropic backend no longer drops `response_format`.** A
  `json_schema` request came back unconstrained with no error; it now
  raises `ValueError` naming the alternative (`structured_output: tool`).

- **`-> dict` under `from __future__ import annotations`.** PEP 563 hands
  the return annotation over as the string `"dict"`, which was read as "not
  a mapping": an op returning a dict it did not build as a literal
  (`return helper()`) got one scalar output, `value`. Graph validation
  refused every reader of its real keys, and at run time the op failed with
  `KeyError: '(flow.s, a) not found in schema'`. The return annotation is
  now evaluated in the function's globals; a forward reference that does
  not resolve counts as unannotated.
- **A retried attempt no longer fails the run.** An op that failed once and
  then succeeded under `retry=` left an `error` trace node: the result was
  clean and `handle.errors` empty, but `trace.status` was `"error"`, a job
  marked the item `failed`, and run stores counted an error. A superseded
  attempt now has `status="retried"` (`STATUS_RETRIED`), keeping its error
  text; `trace.status`, the job runner, run-store summaries and `meta.json`
  ignore it, `roots()`/`leaves()` skip it, and Langfuse shows it as a
  warning. The last attempt of an op that never succeeds stays `error`.
- **`operonx init`'s `AGENTS.md` names guide page 6** (failures).
- **An op with no edges is no longer reported as never running.** In a
  graph without `START >>` it runs as an entry, beside every other entry;
  the build warning now says so.
- **A job's `retry:N` waits between attempts** (F33): the default `Retry`
  backoff, 0.5 s, 1 s, 2 s … jittered. It retried back to back, three
  attempts inside a few milliseconds. **A timed-out item keeps its
  `trace_id`** (it was `None`), so the run that hung can be looked up;
  `RunTimeout.trace_id` carries it.
- **An op failing inside a subgraph stops the ops after the subgraph**, as
  it does flat. The subgraph yielded its all-`None` outputs, so the next op
  ran on `None` and an HTTP door answered `200 null` (now `500`).
  `$errors` gets a `"<graph>.<sub>"` entry naming the op that raised. A
  subgraph that wrote some of its outputs still yields them.
- **`handle.cancel()` ends the run for everyone waiting on it.**
  `result()`, `collect()`, `await handle[op, var]` and `async for` waited
  forever; they now raise `asyncio.CancelledError`. Cancelling a finished
  run keeps its result and no longer interrupts its trace consumers.
- **`asyncio.wait_for(engine.run(...), t)` cancels the graph** when it
  times out (or when the caller is cancelled). The graph kept running, and
  the op after the timeout still ran.
- **`.collect()` no longer writes the collected op's outputs twice.** A
  reducer cell fed by one of its outputs got every item a second time, as
  one list (`ReducerError` with `dict_merge`).
- **A loop that reaches its iteration cap reports it**: `LoopLimitExceeded`
  in `$errors` under the hidden loop, and nothing after the loop runs. It
  stopped silently.

- **`@op(cache=...)` no longer answers one graph with another's result.**
  The store was keyed by the op's full name, which is spelled from
  variable names, so two graphs built under `engine = Operon(...)` with an
  op bound to the same name returned each other's cached outputs. The key
  is now BLAKE2b over the root graph's fingerprint (every op's full name
  and identity, every edge), the op's identity (a function op: its
  qualname and a hash of its code; an LLM op: model, prompt, `fields`,
  `parser`, `validators`) and its inputs encoded exactly. Inputs that are
  not JSON values, dataclasses, pydantic models, sets or bytes are an op
  error instead of a `str()` key. Each store is an LRU of 1024 entries.
  Cache files have a new versioned format; an old file starts empty with
  a warning. Changing any op in a graph starts a fresh cache for it.
- **Structured output fails loudly.** In `fields=` parsing, a value that
  is not the declared type is a field error, so `max_retries` asks again:
  `int` from `"2.5"`, `bool` from `"maybe"` (it takes only
  true/false/yes/no/1/0), where both used to pass with `error: None`. A
  `list` field wraps a lone value. Every parser reads the first fenced
  block anywhere in the answer; JSON then falls back to the first
  balanced object, so prose around it parses. XML retries once with a
  bare `&` escaped. `convert_type` raises `ValueError` on such a value.
- **Every serve door decodes its payload the same way.** HTTP, webhook
  and websocket doors take `codec="json"` (default) or `"text"`
  (`codec =` in `[[serve]]`). A websocket text frame is decoded as an
  HTTP body is, instead of reaching the graph as a raw string. A body the
  codec cannot read is answered `400` before a run is minted (it used to
  run the graph with the raw string); an empty body is the item `None`. A
  websocket frame it cannot read gets `{"error": ...}` back. A websocket
  run that fails before sending anything sends an error frame with its
  `trace_id`. HTTP and webhook replies carry `x-operonx-trace-id`.
- A graph using `Ref.apply(fn)` crashed the eval fingerprint:
  `GraphOp.serialize()` refused a Python callable (an operonx-rs rule). It
  now serializes as `{"python_callable": fn}`, and the fingerprint hashes it
  by name and source.

### Changed

- **`LLMOp.tool_calls` is `{"id", "name", "args"}`, not OpenAI's wire
  object.** Code reading `call["function"]["name"]` or parsing
  `call["function"]["arguments"]` reads `call["name"]` and `call["args"]`.
  Messages that echo the calls back need no change.

- **A step that failed inside a retried attempt is not the run's failure**
  (R2): `WorkflowTrace.status`, `summarize` and the run stores skip the
  child executions of a `retried` attempt (`superseded_ids`). `TraceView`'s
  trajectory reads (`ops`, `path`, `tool_calls`, `errors`) skip a retried
  attempt and its steps (`TraceView.counted`); `llm_calls` and the totals
  keep them. `path()` lists `child()` steps only with `children=True`;
  `OpRow` gains `attempt`, `attrs` and `is_child`.
- **An interrupt's id is deterministic** (R2): `invocation_key(run_id,
  op, ctx)`, the `InterruptOp`'s idempotency key, not a `uuid4`. Still 32 hex
  characters.
- **A generator's trace records stop repeating its inputs** (R2). Every
  record of one generator invocation after the first carries `inputs_from`
  (the first record's `op_id`) and a stored row omits its inputs: a streamed
  LLM call with a 12 KB prompt wrote 1.17 MB of trace, 69 KB now. In memory
  `node.inputs` is unchanged, and every store's `get_run` puts the inputs
  back; old traces read as before. Rows now also carry `attempt` and `attrs`
  when they are not the default; ClickHouse migration 3 adds the columns.
- **`engine.stream()` refuses an unknown mode before the run starts**; it
  used to start the run, then raise.
- `SqliteRunStore` and `PostgresRunStore` are one `SqlRunStore` with a
  different driver, and keep running runs in a `live` table.
- **Tool dispatch calls the tool's function** (K0) instead of building an op
  per call to read its `.core`: 1.98 → 0.009 ms per call, and a tool taking
  an argument named `concurrency` or `bound` no longer fails every call.
  Building any op from an `@op` factory reads its function's source and its
  call site's bytecode once (1.52 → 0.08 ms per op).

- `$errors` of a run whose subgraph failed has one more key, the
  subgraph's.
- `docs/architecture`: per-yield dispatch is sequential by default (it said
  parallel), and the overview no longer advertises `operonx-rs`.

- **Breaking: the closing frame of `LLMOp(stream=True)` adds no text.**
  Its `content` is `""` and the whole answer is the new `full_content`
  output, so joining every frame's `content` gives the answer once. It
  used to repeat the answer under `content`, and a consumer that
  forwarded every frame sent it twice. A batch call sets `content` and
  `full_content` to the answer. Read `full_content` where you read the
  closing frame's `content`.
- **Breaking: a websocket door decodes text frames as JSON by default.**
  A client that sends plain text declares the door
  `websocket(..., codec="text")`.

- **An unknown resource category raises `ResourceCategoryError`** (a
  `KeyError`) instead of parsing to the raw YAML dict, which was cached,
  so a category registered later never resolved. A config that does not
  parse raises "invalid config" instead of "not found". `has()` and
  `keys()` answer from storage without parsing. operonx's own
  `run_store:`, `trace_*:`, `langfuse:`, `source:` and `sink:` categories
  now resolve in a script that has not imported their modules.
- `BaseVectorStore` has a new abstract `_delete()`: a custom backend must
  implement it.

- **Breaking: `$errors` entries are records, not text (C12).** Each
  `"$errors"` / `handle.errors` value is `{type, message, count,
  first_ctx}`: the exception's class name; the first failure's traceback
  trimmed to the user's frames (operonx's own `BaseOp.run` / `_exec_core`
  frames dropped; an exception raised inside operonx is its last line
  alone); how many times the op failed in the run (two stream items
  failing used to read as one); and the ctx the first one ran in, so the
  failed trace node is `f"{op}#{first_ctx}"`. The full traceback stays in
  the trace node and the op's `error` cell. Read `record["message"]` where
  you read the text; `"X" in out["$errors"][op]` now tests the record's
  keys. A structured `LLMOp` step that fails (it returns `error` rather
  than raising) is recorded too, as `ParserError` — or its exception's
  class under `on_failure="error"`. The trace carries the same records
  (`trace.errors`) and a `status`; the local consumer's `meta.json` gets
  `status` and `errors`, and run summaries count a run whose only failure
  is such a record as `error`.

- **Runs are named after their graph (C13).** A job's runs were all
  called `params` and a service's `engine`, and `out = await
  Operon(flow).run()` was `out`: when the bytecode showed no assignment,
  `auto_name` guessed from source lines up to six lines *above* the call.
  That guess is gone. A `@graph` not assigned to a plain variable is named
  after its function, and `Job` and the serve layer build a `@graph` with
  `name=<its function's name>`. `engine = Operon(flow)` in a script is
  still `engine`. `auto_name()` no longer takes `source_fallback`.

- **A script inside a project can trace into the project (C14).**
  `trace="local"` writes to `<project>/.operonx/runs` when an
  `operonx.toml` is at or above the working directory; it wrote to
  `/tmp/operonx_traces` unless an `Application` had set the root (new
  `operonx.core.workflow_trace.active_project()`). New
  `Operon(flow, trace="project")` uses the project's own sinks —
  `[tracing] sinks`, else `[project] trace`, else local — chosen the way
  its services and jobs are, each key checked against the hub; outside a
  project it raises. `Operon()` without `trace=` still records nothing.
- **A run started inside an op of a running engine calls no trace
  consumer.** It is part of that op's run and keeps its own
  `handle.trace`; a helper graph a service op runs per call no longer
  files a second root trace per call when its engine has consumers.

- **A failed HTTP run's 500 body carries its `trace_id`**, the same id as
  the `x-operonx-trace-id` header, for a client that keeps only the body.

- **An LLM execution's trace records the request it sent (C15).**
  `inputs["messages"]` holds the conversation the model received — a
  template rendered with the recorded variables, or the `messages=` it was
  given — beside the variables and the template, with image and audio
  blocks as `Media`. `normalize_trace_io` had no caller, so it never
  reached a trace. It honours `exclude=`/`include=`: `messages` can be
  hidden on its own, and a template whose variable is hidden is not
  rendered. Every record's I/O now goes through the op's
  `normalize_trace_io`, on the filtered copy; the uncalled
  `BaseOp._extract_trace_io` is gone. A batch call's trace grows by the
  rendered prompt once (15.2 → 27.1 KB on a 12 KB RAG prompt).
- `operonx.app.evals` is a package (`dataset`, `evaluators`, `job`,
  `fingerprint`, `stats`, `gate`); every 1.14.0 import still works.
- `ItemResult.as_dict()` is shallow: the deep copy of an eval's verdict was
  most of what writing an item cost.
- Without a `gate`, an eval passes, fails and exits exactly as in 1.9.0.
- `llm_judge` evaluators carry `eval_kind = "judge"`.
- ClickHouse schema version 3 (`experiments`, `experiment_items`, `scores`,
  `judge_cache`) is added to the run store's migration chain: opening a run
  store on a 1.14 database creates the four tables (no `CREATE DATABASE`
  for a table-only user). The connection and migration are shared
  (`ClickHouseConnection`, `migrate`).

### Removed

- `operonx pack` and the `operonx-pack` script: they serialised graphs
  for the dropped Rust runtime and raised on any looping graph.

- `RerankingType.COHERE`, which no factory branch built: `api_type:
  cohere` failed at first use. It is now refused when the config is read.

## [1.14.0] - 2026-10-04

### Added

- **Trace media in ClickHouse: `media: clickhouse` on `trace_clickhouse:`
  and `run_store: {backend: clickhouse}`.** Blobs (audio, images, arrays)
  go to a `media` table beside the runs instead of a local `media_dir`,
  so a studio on another host plays the audio. They ride the writer's
  background batches (one `media` insert per batch, before `nodes`;
  `consume()` still only enqueues). A sha this process already wrote is
  not written again (an LRU of 4096). A blob expires with the last run
  that wrote it, plus a day: the table is `ReplacingMergeTree(expires_at)`,
  so a re-put extends it. The blob bytes a batch holds are capped by
  `media_batch_bytes` (32 MB) plus one run's. `store.media.get(sha)` reads
  a blob back, so `project_stores(...)` → `open()` → `media.get` works
  unchanged. The default stays `media: local`. The new store is
  `operonx.telemetry.runs.clickhouse.ClickHouseMediaStore`.
- `scripts/bench_clickhouse_media.py`: what media in ClickHouse costs,
  against a media directory.

### Changed

- The ClickHouse schema is at version 2, which adds the `media` table. A
  version 1 database upgrades on first use with one `CREATE TABLE IF NOT
  EXISTS`, and older writers keep working against it.

## [1.13.0] - 2026-10-04

### Added

- **`operonx.telemetry.runs.project_stores(root)`: the stores a
  project's trace sinks can be read from, from its files alone.** It reads
  `[tracing]` in `operonx.toml` (project-wide, per service, per job, and a
  block's own `trace =` where nothing overrides it) and `resources.yaml`
  with `${VAR}` from the project's `.env` under the environment, and
  returns one `StoreSource` per sink: `"local"` and `trace_local:` as
  `files`, `trace_clickhouse:` as `clickhouse`, `trace_langfuse:` as
  `langfuse` through its client, `run_store:` as itself; a project's own
  consumer comes back unreadable with the reason. Relative paths anchor
  where the writer anchors them. The studio uses it to read what a project
  actually writes.
- `open_run_store` and `run_store:` take `timeout` (ClickHouse's connect
  timeout).
- `operonx.core.registry.storage.yaml.flatten_resources`: a resources
  file's top level as `category:name` keys, nested and flat forms alike.

- **`[tracing]` in `operonx.toml`: which trace sinks are on, in one
  place.** `[tracing] sinks = ["local", "trace_langfuse:edupia"]` sends
  every run to all of them; `[tracing.services.<name>]` and
  `[tracing.jobs.<name>]` override one service or job, and `sinks = []`
  turns tracing off there. `"local"` is the built-in local consumer (also
  accepted by `trace=` anywhere, `Operon(trace="local")` included); any
  other entry is a resource key, as in `trace=[...]`. Precedence, most
  specific first: `[tracing.<services|jobs>.<name>]`, the service's or
  job's own `trace=` (or `trace =` on its block), `[tracing] sinks`,
  `Application(trace=...)` / `[project] trace`, then the default (a job
  records locally; a service is not traced). A typo, a non-list, a
  malformed sink or a name that is no service or job fails at load, naming
  the key; a sink missing from `resources.yaml` fails when the service or
  job starts, naming the level that chose it. `operonx-serve --list`,
  `operonx-run --list` and `Application.describe()` (`sinks`,
  `sinks_from`) show each service's and job's sinks and where they came
  from. `[project] trace` and `[tracing] sinks` together is an error.
- `ResourceHub.declares(key)`: whether a key is configured, without
  parsing or caching it.

- **A ClickHouse run store and trace consumer** (`pip install
  "operonx[clickhouse]"`). `trace=["trace_langfuse:edupia",
  "trace_clickhouse:default"]` records each run into ClickHouse beside
  Langfuse, and `run_store: {backend: clickhouse}` opens the same database
  for the studio. It passes the shared `RunStore` contract tests.
  - **A run never waits on the database.** `consume` only queues the
    finished trace (about 50 µs for a 4000-execution run). A background
    thread builds the rows and inserts them in batches with `async_insert`.
    While ClickHouse is slow or down, runs past `queue_size` are dropped and
    counted in `store.writer.stats`, with one warning per outage. Measured on
    a 2000-yield streaming run: no difference with gaps between runs; back to
    back, the writer's CPU (about 46 µs per execution) shares the GIL with
    the next run (`scripts/bench_clickhouse_consumer.py`).
  - **A user granted only tables in an existing database works.** The
    store creates the database only when `EXISTS DATABASE` says it is
    missing, so a user without the `CREATE DATABASE` grant can still write
    and read instead of failing with code 497.
  - **Tables**: `runs`, `nodes` and `op_rollups`, as `ReplacingMergeTree`
    (a retried batch never duplicates a run), partitioned by month and
    ordered for the contract's queries. Every row has a TTL from
    `expires_at`: operonx's per-origin retention, or `ttl_days`. The schema
    is created on first use and versioned in `schema_version`.
  - **Media**: `Media` values, and `bytes` or arrays from `media_threshold`
    up, are stored once in `media_dir`, named by their SHA-256. The row
    keeps `{"$media": sha, "mime", "size", "duration_s", "store"}`.
    `prune_media()` removes blobs nothing references.
- **`operonx.telemetry.media`**: `detect_media()` names a blob's type from
  its magic bytes: WAV (rate, channels and duration from the header), MP3,
  OGG/Opus, FLAC, WebM, PNG, JPEG, GIF, WebP, PDF and `.npy`. Raw PCM takes
  the rate a `Media` declares in its mime parameters
  (`audio/L16;rate=16000`). It also adds the `MediaStore` interface,
  `LocalMediaStore`, `offload_to_store()` and `json_default()` (an orjson
  hook that sanitises and offloads in one pass), all usable by any store.
- **`operonx.telemetry.writer.BackgroundWriter`**: a bounded queue and a
  batching thread. `submit` never blocks or raises; items past the bound
  are dropped and counted; failed batches are retried, then dropped.
- **`operonx init`: a new project a coding assistant can build on at once.**
  `pip install operonx` → `operonx init myapp [--template hello|http|chat|agent]
  [--name NAME] [--force]` writes the layout of
  `operonx/guide/05-project-layout.md`: `operonx.toml` (it only points the
  CLIs at `app.main:APP`, plus `[tracing] sinks = ["local"]`), `app/main.py`
  with the `Application` declaring the template's services and jobs, one
  feature as `src/<feature>/graph.py` + `ops.py`, `resources.yaml` and
  `.env.example` (secrets as `${VAR}`), tests that run offline, a
  `pyproject.toml` on `operonx>=<this version>`, `.gitignore` and a README.
  For assistants it adds `AGENTS.md` (read the guide first, the ladder, the
  layout rules, the commands, no `print()`), a `CLAUDE.md` holding
  `@AGENTS.md`, and `.operonx/guide/`, a copy of the installed guide.
  `hello` is pure compute (a job and an HTTP service), `http` a service
  tested in-process, `chat` an `LLMOp` tested against a local fake model,
  `agent` a ReAct agent with one `@tool` tested with a scripted model. An
  existing file is never overwritten without `--force`, so on an existing
  project `init` only adds what is missing, and says so when that is
  nothing. Every template is tested end to end: its own tests pass, and
  `operonx serve --list` / `operonx run --list` list every service and job.
- **`operonx guide`** prints the guide's index; `--path` prints where the
  installed guide is; `--sync [DIR]` copies it into the project's
  `.operonx/guide/` (removing pages the installed version dropped) and
  writes its version to `.operonx/guide/VERSION`. Run it after upgrading.
  `operonx.guide.sync(project)` does the same from Python.

### Changed

- `tests/internal/cli/test_extras.py` also checks quoted install hints
  (`pip install "operonx[postgres]"`), which it used to skip.
- **One command: `operonx run`, `operonx serve`, `operonx pack`,
  `operonx play`** beside `operonx init` and `operonx guide`. Each takes
  exactly the arguments its `operonx-*` script took and is the same
  `main(argv)` (the rest of the command line is handed over untouched, so
  there is one parser per command); `operonx --help` lists them all. Usage
  lines, `--list` hints, the guide, `docs/`, the README and the examples
  now spell them `operonx <command>`.

### Deprecated

- **`operonx-run`, `operonx-serve`, `operonx-pack`, `operonx-play`.** They
  still work, exactly as before, and print one line to stderr:
  ``DeprecationWarning: `operonx-run` is deprecated and will be removed in
  the next release; use `operonx run` ``. Deployed projects and Dockerfiles
  call them, so they stay for this release; switch to `operonx <command>`.

### Fixed

- **`[[job]] trace = []` is kept.** It read as "nothing declared", so the
  job inherited the application's consumers (or recorded locally) instead
  of tracing nothing, unlike `Job(trace=[])`.


## [1.12.2] - 2026-10-03

### Fixed

- **A `.collect()` inside a subgraph handed its result up twice.** The
  scheduler listed the collect's context twice among the contexts a
  subgraph reports: once when the buffer was flushed, and again when the
  consumer's frame arrived there, since nothing had seeded that context and
  it looked like a new stream item. The subgraph yielded and stored the
  same result twice. An op after the subgraph still ran once, which is why
  a minimal nested graph looked right; a reducer cell written straight from
  the subgraph's output (`sub["out"] >> PARENT["log"]`) got every value
  twice, inside a loop or not. Two collects off one stream got it three
  times. The ReAct loop's `run_tools` now gathers its tool messages with a
  `.collect()` inside the subgraph, beside the stream it ends.
- **A `.collect()` over a stream whose every item failed runs, with `[]`.**
  A failed item is left out of the list, but the collect's group was opened
  by the first item to *reach* it, so with none reaching it the consumer
  never ran, and nothing after it did either, with no error of its own.
  The group now opens when the generator mints the stream, so the consumer
  runs once, with an empty list, per stream (per inner stream when nested).
  A generator that yields nothing still has no stream to collect.
- **A turn whose every tool call fails at the op level no longer ends the
  agent with no answer.** A call whose dispatch failed before the tool ran,
  such as an approval sink that raises, produced no tool message. Every
  call failing ended the loop after that turn, and one failing among
  several left that call unanswered in the history, which a real provider
  rejects on the next request. `run_tools` now answers each call that has
  no tool message with an error (`DISPATCH_FAILED`), the cause stays in
  `$errors`, and the model takes the next turn.
- `tests/internal/providers/test_embedding_openai.py` passes on every
  supported openai SDK. On openai < 3 `AsyncAzureOpenAI` sends the API key
  as `Authorization: Bearer <key>` next to `api-key`. That is the SDK's
  own behaviour (it inherits `AsyncOpenAI.auth_headers`; a bare SDK client
  does it), the same key to the same host. The test pins exactly that, and
  no `authorization` header on openai 3.

## [1.12.1] - 2026-10-02

### Changed

- **The ReAct turn is drawn as zones.** `build_react_agent` builds each turn
  as three stages: `context` (a `build_context` subgraph: compaction,
  memory, skills, the assembled prompt, cache marks), `model` (the
  `call_model` step, named `model` instead of a hash such as `784228a4`),
  and `tools` (a `run_tools` subgraph: one dispatch per call, up to 8 at
  once, one tool message each), then back. A viewer or a trace shows the
  loop's shape — context → model → tools — with each stage's steps one
  level down, instead of fifteen nodes in a row. Trace op names move with
  it: `…__loop_0__.planned` is now `…__loop_0__.context.planned`,
  `…__loop_0__.disp.run` is `…__loop_0__.tools.disp.run`. The messages,
  `turns`, `stopped_early`, `truncated`, `finish_reason` and `final` an
  agent returns are unchanged.
- **An agent node is named after its variable and shows `final`.**
  `research = build_react_agent(...)(messages=...)` is the node `research`,
  and a viewer shows its answer rather than its last `stopped_early` flag.
  `show_keys=` passed at the call still wins. `build_react_agent` still
  returns a `@graph` factory to anything that inspects it (its `messages`
  parameter, its `react` name, the marker the serve layer and Studio read).

### Known issues

- **A turn whose every tool call fails at the op level ends the agent with
  no answer.** A nested graph whose streamed items all failed yields
  nothing, so the loop's `.collect()` over `tools` never fires and the next
  turn does not start; the error is in `$errors`. This is not a tool that
  raises — that is answered with an error tool message, as before — but an
  op inside dispatch failing, such as an approval sink that raises. Before
  1.12.1 the next turn ran, on a history with that call unanswered (which a
  real provider rejects). Pinned by an `xfail` in
  `tests/internal/agents/test_agent_zones.py`.
- The loop gathers the tool messages in the parent, not with a `.collect()`
  inside `run_tools`: in the agent's loop that collect handed its result up
  twice. A minimal nested graph (generator → parallel op → collect, inside
  a subgraph) does not reproduce it, so the trigger is not yet pinned down.

## [1.12.0] - 2026-10-01

### Added

- **Triggers: `webhook(...)` and `schedule(...)` listeners.** A run started
  by an event rather than a caller who waits. `Service("mail",
  webhook("/mail", port=8200), graph=g)` answers the POST `202
  {"accepted": true, "run_id": ...}` at once and runs `g` in the background,
  traced like any service run (`?trace_id=` keeps the sender's id);
  `max_inflight=N` answers `429` beyond N pending runs. `Service("sweep",
  schedule(every="5m"), graph=g)` or `schedule(at="08:00")` starts a run per
  tick; a tick that lands while the last run is going is skipped and counted
  (`ScheduleTransport.skipped`), and a failing run does not stop the clock.
  Before, an `http` service answered only when the run ended — a webhook
  sender gave up long before an agent finished — and there was no clock.
- **`MCPClient.call_value(name, args)`: a tool's value, for code.** `call()`
  returns text, which suits a model; code building on it could not tell a
  one-item list from a record (a list arrives as one text block per item) or
  an empty list from nothing. `call_value` returns the server's structured
  value, unwrapping the `{"result": ...}` a list or scalar comes in, and falls
  back to the text parsed as JSON.
- **An agent is a node: `agent["final"]`.** The ReAct graph ends on `final`,
  so the op after an agent inside a larger graph reads its answer directly;
  before, it was reachable only through `agent_result()` after the run.

### Fixed

- **A nested graph's declared cell lost its input and doubled its writes.**
  A graph that declares a cell and also takes it as an input — an agent's
  `messages` — has one cell in both roles. Nested in another graph, the
  parent's value never entered it (a shared cell is never pulled), and when
  the graph finished its output was stored back into the same cell, so the
  reducer appended the cell's own value again. An agent used as a node ran
  without the question it was asked, and the next op saw every message
  twice. Standalone runs were correct, which is why nothing caught it.

- **`api_type: openai` embeddings reach the embeddings endpoint.** They were
  sent through the vLLM client, which posts to `base_url` as written, so the
  documented `base_url: https://api.openai.com/v1` (the providers docs, the RAG
  guide, ex07, ex12, ex16) posted to `/v1` and got a 404 from OpenAI. `openai`
  and `azure` now use the OpenAI SDK, like the `llm:*` backends: `base_url` is
  the API root. A `base_url` ending in `/embeddings` — the only form that
  worked before — still works, with a `DeprecationWarning`. `vllm` is
  unchanged: its `base_url` is the exact endpoint.
- **`dimensions` is sent.** It was never in the request, so a 256-wide
  resource got 1536-wide vectors. It is now sent, and a reply of any other
  width raises instead of reaching an index built for the configured one.
  A model that cannot shorten its vectors (`text-embedding-ada-002`) rejects
  `dimensions`: leave it unset there.
- **Azure embeddings authenticate.** They sent `Authorization: Bearer <key>`
  and no `api-version`; they now send `api-key` and `?api-version=`.
  `api_version` is a field of the embedding config (it was documented but
  silently dropped) and is required for `azure`; `base_url` is the resource
  endpoint and `model` the deployment. A deployment URL is refused with the
  fix in the message.
- `api_type: openai` embeddings need only `operonx[openai]` — no longer
  `operonx[providers]` for aiohttp.
- An unsupported embedding `api_type` names the supported ones.
- **`bootstrap()` then `hub.get("embedding:…")` works without importing
  `operonx.providers` first.** The built-in categories register on that
  import; without it the resource stayed a raw dict and failed with
  "No factory registered for dict". The hub now loads the built-in plugins
  when a category is unknown, and a category no provider serves gets an
  error naming it and the fix.
- `VLLMEmbedding.get_output_dim()` returns the configured `dimensions`; it
  read an attribute nothing set, so every call raised `AttributeError`.
- New mock-only tests (`tests/internal/providers/test_embedding_openai.py`,
  marked `unit` so the default `pytest` runs them) pin the request on the
  wire, and check that every `openai` embedding resource shipped in
  `resources.yaml` and `examples/` reaches `/embeddings` as written.

## [1.11.1] - 2026-09-30

### Added

- **A bound on a stream edge:** `ref.sequential(max_pending=N)` and
  `.parallel(max=M, max_pending=N)`. At N waiting items the producer is not
  advanced, so the backlog stops growing and the pressure reaches its input.
  Before, the edge queue had no bound: a callbot load test measured 695
  items waiting at 12 calls. `on_full="drop_oldest"` drops instead, counted
  in `handle.drops`. Unbounded stays the default.

## [1.11.0] - 2026-09-29

Every open finding in `docs/design/OPEN_FINDINGS.md` is fixed, and so are
the silent failures the guide used to document as "never do X". Most of
the changes turn a plausible wrong value into the right one or a clear
error, so check the **Changed** list when upgrading.

### Changed — behaviour you may notice

- **An op that raises is reported.** `run()`, `collect()` and `result()`
  return `"$errors"` (`{op_name: error_text}`) when an op failed, and
  `handle.errors` holds the same dict. The run still does not raise. The
  key is absent when nothing failed.
- **Loops:** an op on a loop's exit arm, and an op after a loop (or after
  a sub-graph holding one), runs **once**, with the final values. Before,
  it ran every iteration and its outputs were lists. An exit arm the loop
  did not take no longer runs. A back-edge source that raises stops the
  loop instead of spinning to the 1000-iteration cap.
- **`.collect()` behind a per-item op** hands over one list per stream,
  in yield order (it handed over one-item lists).
- **`.parallel(max=N)`** caps the items in flight (it capped nothing).
- **Hard and `~` soft edges into one op:** the op waits for every hard
  edge and the first soft one (two soft arrivals could fire it early).
- **Branches:** comparing two Refs in `if_()` works (it always took the
  first case); two ops with the same output name no longer collide in a
  condition; `.build()` with no match runs no target (it ran every one);
  `START >> if_(predicate_op(...))` and `[a, b] >> if_(predicate_op(...))`
  run the predicate.
- **Refs:** `and` / `or` / `not` / `if` / `in` on a Ref raise `TypeError`
  naming `&`, `|`, `~` (they silently used one side). Iterating a Ref
  raises (it never returned). `ref.field` on a dict value reads the key.
  An op input combining two Refs (`op(x=a["n"] + b["n"])`) is a
  `TypeError` at build time.
- **Streams:** every `stream()` mode raises the fatal error `run()`
  raises (`updates` and `custom` ended cleanly). `__interrupt__` is no
  longer in `run()` / `collect()` / `result()` payloads.
- **Inputs:** an op's first call resolves inputs like every later call
  (Media unwrapped, ancestor contexts walked).
- **`LLMOp`:**
  - A template placeholder named like a model setting (`{user}`) fails
    at build time.
  - A Ref in `validators=` is a `TypeError` at build time (it hung the
    loop or passed everything).
  - `batch_mode=True` keeps `fields=` / validators / retries; it refuses
    `fallback=` and more than one resource.
  - Colliding field output keys are refused; `"user.id as user_id: str"`
    names an output.
  - An absent `?` field skips its validators; `"@@x"` is the literal
    `"@x"`.
  - A structure where a scalar field was declared is an `error`, not a
    Python repr.
  - A streaming fallback is taken only before the first delta.
- **Agents:**
  - A budget-exhausted turn answers its pending tool calls ("Not run: …"),
    so the history stays valid.
  - `AgentSession.send` leaves the history unchanged on any failure and
    cancels the timed-out run.
  - Tool arguments are redacted in the approval payload.
  - Cache breakpoints reach Anthropic as content-block `cache_control`
    and are stripped for OpenAI-compatible backends (at most 4).
  - A sub-agent sees only the tools its policy allows, and an `ask` in a
    child is refused at once.
  - `agent_result` gains `truncated` and `finish_reason`; `stopped_early`
    is also true for a cut answer.
  - Compaction keeps tool results with their calls and counts the tool
    definitions against the budget.
  - On the last turn of the budget, `make_llm_caller` sends
    `tool_choice="none"`, so the model answers in text; a hand-written
    `call_model` gets `last_turn=True` when it declares that parameter.
- **Anthropic backend:** sends `tools=` / `tool_choice`, and translates
  `tool_calls` and tool results both ways (streaming too), so it can
  drive an agent.
- **OpenAI-shaped backends** (and the batch path) send only the message
  keys Chat Completions defines, so an agent's `id` / `status` no longer
  trip strict gateways.
- **MCP:**
  - A client can be closed from any task (the connection is owned by its
    own task).
  - Registration is all-or-nothing, `close()` withdraws the client's
    tools, and a second `connect()` is refused.
- **Heartbeat:**
  - `max_beats` is exact under `overlap="queue"`.
  - `stop()` propagates cancellation, and `start()` during a stop is
    refused.
- **Jobs:** `operonx.toml` accepts every `on_error` value `Job` accepts,
  `record` included. `on_error="retry3"` is refused.

### Added

- `ExecutionHandle.errors`, `Ref.get_all_refs()`,
  `operonx.agents.unregister_tool` / `unregister_mcp_tools`,
  `Redactor.scrub_data`, `ToolPolicy.refusal`,
  `make_llm_caller(...).tools` / `.with_tools()`, `estimate_tool_tokens`,
  `plan_compaction(reserved_tokens=)`.

### Removed

- The dead classic loop re-dispatch path, `LoopConfig.until`,
  `loop_iters`, and the op-exception handlers that never ran.

### Docs

- The guide drops the rules the fixes made unnecessary.
- `CLAUDE.md` and `HANDOFF.md` describe the current API.
- `docs/design/OPEN_FINDINGS.md` records each finding as fixed.
## [1.10.3] - 2026-09-29

### Added

- **`LLMOp(on_failure="error")`** — for a step whose output is optional.
  A hard failure (a timeout, a transport error, a refusal), once retries and
  `fallback` are spent, returns what a parse failure already returns: every
  field `None` and `error` set, so a downstream op can carry on without it.
  The default, `"raise"`, is unchanged. Requires `fields` — the failure is
  reported in the `error` output, which only structured mode has.

## [1.10.2] - 2026-09-29

### Fixed

- **An inline branch no longer borrows a name from the lines above it.**
  `x >> if_(x["is_bot"], a).else_(b)` has no variable, so its name was
  guessed from nearby source lines — and a keyword argument there
  (`role="agent",` parses as an assignment) named it: real graphs had
  several branches called `role`, one called `filter_meta`. The variable a
  branch is assigned to is now read from the bytecode only; inline, it is
  `route_N`, as it already was when no guess matched.
- `auto_name()`'s source fallback no longer reads a line ending in a comma
  as an assignment — it is an argument in a call that spans lines.
  `auto_name(source_fallback=False)` skips the guess altogether.

### Docs

- The guide: write a branch inline; assign it only when something else
  refers to it.

## [1.10.1] - 2026-09-28

### Fixed

- **`Application(trace=[])` survives `operonx.toml`.** Loading the app
  through `[project] app = "module:APP"` merged only the object's truthy
  project values, so an explicit empty `trace` was dropped and every job
  without consumers of its own fell back to the local consumer, recording
  whole runs under `.operonx/runs`. The object's `trace` now wins even when
  empty; its empty default description still gives way to the file's.

### Docs

- The guide says how to use a `@graph` parameter: by its name. In the
  body it already is `PARENT["x"]`, so `PARENT["x"]` beside a parameter
  `x` says the same thing twice; `PARENT[...]` is for what the graph does
  not declare (a loop cell, a write-back). And a `Job` given a `@graph`
  makes every parameter a runtime input, so a signature default never
  applies there — the value goes in `Job(inputs=...)`.

## [1.10.0] - 2026-09-28

### Added — replaying real sessions

- `Service(..., replay=True)` (`[[serve]] replay = true`): each run of the
  door carries what the client sent — a script of toy messages, in order
  and stamped (`replay_script`), and the connection's query
  (`replay_query`), the same shape a playground session keeps — so a real
  session can be replayed in the playground after a fix. Text and JSON
  are kept; audio, bytes and messages over 64 KB are only counted. Off by
  default.
- `Codec.to_toy(item)`: what a client sent, as a toy message (the inverse
  of `to_door`). A door whose JSON frames carry audio overrides it so
  that audio is counted, never kept. `describe_service` says `replay`.

### Added — a guide for coding assistants

- `operonx/guide/` ships with the package (`python -m operonx.guide` prints
  where): op types, the composition ladder (op → operon → Job / Runbook /
  Service → Application → `operonx.toml`), control flow, the failures that
  raise nothing, and a project layout. Every example is a complete program
  that `tests/guide/` runs. Pointed to from `llms.txt`, the README and the
  package docstring.

## [1.9.0] - 2026-09-28

### Changed — where local runs go

- **`trace_local` files runs by where they came from:**
  `services/<service>/<day>/<run>`, `jobs/<job>/<job run>/<run>`,
  `evals/…`, `playground/<day>/<run>`, `adhoc/<workflow>/<day>/<run>`.
  The root is `root:` when set (a relative one now resolves against the
  project), else `$OPERONX_RUNS_DIR`, else `<project>/.operonx/runs`,
  else `/tmp/operonx_traces`. Before, a run went to `<root>/<run>/`, and
  the root defaulted to `/tmp/operonx_traces`. `layout: flat` keeps the
  old shape; any
  other string is a template over `{origin}`, `{name}`, `{group}`,
  `{day}`, `{trace_id}` and the run's metadata keys. **This is the change
  an upgrade notices:** a reader of the old flat directory sets
  `layout: flat` (and `root: /tmp/operonx_traces`) or follows the new tree.

### Added — every run knows where it came from

- **Origins.** A service's runs carry `origin=service` with `service`,
  `transport` and `variant`; a job's carry `origin=job` with `job`,
  `job_run` and `key`, plus `runbook` and `runbook_run` inside a
  runbook; an eval's `origin=eval`; the playground's `origin=playground`;
  anything else `adhoc`. Consumers read them: the local consumer files
  by them, Langfuse receives them as tags, a run store indexes them.
  `operonx.app.origin` holds the vocabulary.
- **Default consumers.** `Application(trace=[...])` (or
  `[project] trace = [...]`) reaches every service and job that names
  none; `trace=[]` stays an explicit "trace nothing". A job with no
  consumers anywhere records locally, so its item records never point at
  traces that were not written.
- **The code's version.** `Application.bootstrap()` stamps the project
  and the git commit (with a dirty flag) once per process; every trace
  carries them.

### Added — run stores (`operonx.telemetry.runs`)

- **`RunStore`**, one contract for where finished runs live: `put_trace`,
  `list_runs` (a `RunFilter`, an order, a cursor), `get_run`, `rollups` /
  `op_stats`, `delete_runs`, plus `groups`, `count` and `refresh`. A
  store keeps each run in full and as a summary with per-op rollups, so
  one run opens fully and a month of runs summarises without opening any.
  A store is a trace consumer: `trace=["run_store:default"]` records into
  it.
- **Backends:** `files` (the local consumer's directories plus a SQLite
  index; indexing is batched — 376 runs 8.9 s → 0.54 s cold), `sqlite`,
  `postgres` (`operonx[postgres]`), `mongo` (`operonx[mongo]`, new extra),
  and `langfuse` (read-only). `run_store:` in `resources.yaml`;
  `open_run_store()` for tools that must not import the project.
- **`summarize()`**, the one definition of a run's numbers. A priced zero
  is a price; an unpriced call is never $0 — a run's cost is `None` only
  when nothing in it was priced, and unpriced calls are counted.
- **Retention:** `DEFAULT_RETENTION` (services 30 days, jobs and evals
  forever, playground 7 days, ad hoc 30 days), `plan_retention`,
  `apply_retention`.
- **Alerts** (`operonx.telemetry.runs.alerts`): `error_rate`, `p95_ms`
  (of runs, or of one op), `cost_per_hour` and `runs` (too few) over a
  trailing window, from what the store already keeps; `step` turns an
  evaluation into firing / reminder / resolved, `deliver` posts it to a
  webhook in the shape Slack and Teams take.

### Added — the playground bridge (`operonx.app.play`, `operonx-play`)

- A service's doors driven from outside, over JSON lines on stdio, in
  the project's own interpreter — through the same gate, hooks, door ops
  and consumers as production, with `origin=playground`.
- **Codecs** translate toy messages (text, JSON, bytes, audio) to door
  items and back: built-ins for http (`JsonCodec`) and websocket
  (`TextCodec`) doors, `Service(playground=...)` (or `[[serve]]
  playground = "module:attr"`) for a door with its own protocol, and
  `PcmCodec` for raw PCM frames — the Voice toy.
- **Conditions** per session: latency, dropped items, noise, leading
  silence, and resources that fail for that session only.
- **A simulated user:** an LLM persona holds the conversation for a
  number of turns.
- **Re-run one op** of a service's graph or a job's, with the inputs it
  had in a recorded run, as a run of its own.
- A playground run records to the service's **local** consumers only,
  unless the session asks for `remote: true`; it carries its connection
  query and what the toy sent, and when — all a replay needs.
- A session's **ops stream as they finish** (`ops` events), so a canvas
  can follow the run live; `serve_session` hands the handle to
  `on_start` as the run begins.
- `Service(key_ops=[...])`: the ops a dashboard pins first;
  `describe_service` carries them.

### Added — evals (`operonx.app.Eval`)

- A dataset of cases, the graph under test and evaluators, run as a job
  with `origin=eval`. Evaluators are plain, async or `@op` functions
  taking `input`, `output`, `expected`, `row` or `outputs`; built-ins
  `exact`, `contains`, `fuzzy`, `json_match` and `llm_judge`. Each case's
  verdict is on its item record, the pass rate in `run.json`, and a
  `threshold` (or any failing case) fails `operonx-run` — a CI gate. In
  the manifest, a `[[job]]` with `dataset`, `evaluators` and `threshold`.

### Fixed

- A resource entry with every field at its default
  (`trace_local: {default: {}}`) resolved as "not found" while listed as
  available.
- `trace="trace_local:…"` no longer depends on something else having
  imported `operonx.telemetry` first.

### Docs

- New guides: *Runs: stores, retention and alerts*, *The playground
  bridge*, *Evals*. The *Tracing* guide is rewritten for the consumer
  API (it still described tracer classes removed in the V3 rework). API
  pages for `operonx.app` (now in the nav) and `operonx.telemetry.runs`.

## [1.8.1] - 2026-09-26

### Fixed

- A job whose source (or sink) is a function — a generator of items —
  is described by where it is defined (`qc.cases:all_cases`) in
  `describe()`, `operonx-run --list` and the studio, not by its
  `<function … at 0x…>` repr.

## [1.8.0] - 2026-09-26

### Changed — declarations moved to where they are true

- **A door op says what it is.** `@op(door="ingress")` / `@op(door="egress")`
  (and `door_default` on an op class); the built-in `ingress()` /
  `egress()` declare it. `Service(ingress=, egress=)` and the
  `[[serve]]` keys are gone — using them is an error that says so.
- **The graph's signature is the door's contract.** `Service(inputs=)`
  and the `[[serve]]` key are gone. A declared `on_session` hook's
  `RunRequest.inputs` must be exactly the graph's runtime parameters;
  anything else is refused at the door, naming what is missing and what
  is not a parameter. (Fixed on the way: `compile_graph` set
  `engine.inputs_expected` inside a `try` that swallowed the failure —
  `Operon` has slots — so it was never set. It is a slot now.)
- **The process is shaped by the listeners.** `websocket(..., workers=4)`
  (and `http`, `asgi`, `[[serve]] workers`) runs that listener as N
  worker processes, each loading the application again from
  `operonx.toml`; a one-worker listener runs in the main process.
  `Service(on_startup=[...])` (`[[serve]] on_startup`) runs in that
  listener's workers only; `Application(on_startup=)` still runs for
  every listener. `Application.serve()` / `operonx-serve` do all of it,
  and stop the workers on SIGTERM; services sharing an address must
  agree on `workers`. `operonx-serve --list` prints workers and hooks.

### Changed — a Runbook is wired like a graph body

`with Runbook("nightly") as nightly:` then one `>>` statement per line —
`fetch >> [score, audit]`, `score >> [report, export]`,
`[report, audit] >> notify` — as many lines as the flow needs. The
runbook is the set of wires from all its lines: a DAG, not a tree. A job
starts when every job wired into it has finished; `on_error="stop"` now
skips only what is downstream of a failed job (before, it stopped every
sequence that had not started). A cycle is refused when the block
closes, naming its jobs. `Runbook("nightly", a >> [b, c])` still works.
`Runbook(schedule=...)` declares a runbook's schedule in Python (a
`[[job]]` block's `schedule` reaches the object too). A runbook prints —
`tree()`, `operonx-run --show` — as its wires (`a >> [b, c]`), and its
record carries `wires` beside the per-job `tree`. `Sequential(...)` and
`Parallel(...)` remain, as `a >> b >> c` and `[a, b]`.

### Added — jobs for a whole project

- `Job.main()` / `Runbook.main()`: any job is its own command line, with
  `operonx-run`'s flags minus the name. `python -m jobs.x --resume`.
- `operonx-run --set KEY=VALUE` (graph inputs, repeatable; JSON when it
  parses), `--source`, `--sink`.
- A job with no source runs its graph once, on one empty item.
- `DirSource` (every matching file, `{"path", "name"}`) and `DirSink`
  (one `<key>.json` per item, written whole; `skip_existing`). Kind `dir`
  for both, and a directory path picks them.
- `Job(preflight=[resource keys])`: the run fails in seconds, before any
  item, when one of them does not answer.
- `on_error="record"`: a failed item is recorded and handed to the sink,
  but does not fail the run — for a job whose next step reads every outcome.
- A graph with no `ingress` runs doorless: a job treats it as a function —
  inputs in, the run's result is the item's result. `item_input` binds the
  item when the graph wants it. Doors (`ingress`/`egress`) stay for graphs
  that declare them and for `stream` jobs, which now refuse a graph
  without `ingress`.

### Changed

- File sinks default to mode `auto`: a fresh job run starts the file
  over, a `--resume` run adds to it. Before, every run appended, so
  running a job twice doubled its output. Used directly, a sink still
  appends; `mode="append"` keeps the old behaviour everywhere.

### Fixed

- `operonx-run` loads `.env` before reading `operonx.toml`, so a
  `${VAR}` there that only `.env` sets is no longer read as unset.
- A `[[job]]` key a Job does not read (a typo such as `concurency`) now
  warns, naming the keys it does read. It is still kept in
  `JobSpec.options`.

## [1.7.3] - 2026-09-25

### Added — the application, declared in Python

`Application("name", services=[Service(...)], jobs=[Job(...)],
on_startup=[...])` with `websocket(...)`, `http(...)`, `asgi(...)`
listeners and `env(NAME, default)`: the graph, the door hooks and the
variants' bound objects in one place a reader can see, instead of
`module:attr` strings in a file. `operonx.toml` then says only what is
not code — `[project] name`, `src`, and `app = "module:APP"` — and
`operonx-serve`, `operonx-run` and the studio read the object through
`Application.find()`. TOML services keep working; `Service` builds the
same `ServeSpec`, so nothing downstream knows which way it came.

### Added — the door's contract, and the doors

`inputs=` on a service (or `[[serve]]`) names the graph's runtime inputs
the door builds. A graph that takes something else fails at boot naming
the parameter; a hook that builds something else is refused at the
door. `ingress=` / `egress=` name the door ops; `describe()` carries
both, and `operonx-serve --list` prints them.

### Changed

`Application.graphs`, `describe()` and `operonx-serve --list` name
objects as `module:qualname`. ex17 and ex18 are declared in Python.

## [1.7.2] - 2026-09-25

### Added — `[serve.variants]`: one door, one compiled graph per variant

A door whose graph differs by caller declares the variants. `graph`
names a factory — a plain function that takes the bound parameters and
returns a `@graph` — and each variant binds them (`module:attr` values
are loaded, the rest are literals). `operonx-serve` compiles one engine
per variant at boot; `on_session` picks with `RunRequest.variant`, and a
session naming none or an unknown one is refused at the door.
`compile_graph(entry, bind=…)` is the same thing from Python;
`Application.graphs` lists one graph per variant (`build[formal]`) under
the service; `describe()` carries `variants` per service and `bind` per
graph. `examples/python/ex18_variants` is the worked example.

### Added — `[project] src`

Import roots relative to the manifest (default `["."]`).
`Application.bootstrap()` puts each on `sys.path`, so a `src/` layout
needs no path juggling in entry points.

### Fixed — a value with a `name` is not an op

`resolve_value` took any object with a ``name`` attribute for an op
reference, so a dataclass bound into a nested graph at build time — the
thing a variant does — became a `Ref` to itself and failed the scope
check with a message about PARENT. Only ops, graphs and `PARENT` are
references now (`is_op_like`).

## [1.7.1] - 2026-09-25

### Changed — the application layer is `operonx.app`

`serve`, `jobs` and `manifest` move from `operonx.core` to `operonx.app`:
they are what puts work *into* an Operon, not the Operon. The old paths
keep working for one minor release with a `DeprecationWarning` and go in
1.8.0; `sed 's/operonx\.core\.\(serve\|jobs\|manifest\)/operonx.app.\1/'`
is the whole migration.

### Added — `Application`

The loaded manifest, with one method per thing production does:
``Application.find().serve(only=…)``, ``.run("nightly", resume=…)``,
``.asgi(port=…)`` for a process that runs uvicorn itself, and
``.describe()`` — graphs, services and jobs as plain data, without
importing the project. `operonx-serve` and `operonx-run` are now that
object plus argument parsing; the three copies of "parse the manifest,
bootstrap, resolve entry points" are one. `compile_graph(entry)` is the
one way a manifest entry becomes an engine. A size test keeps the object
under 200 lines: it is a composition root, not a framework object.

## [1.7.0] - 2026-09-24

### Added — Jobs: running Operons over data that does not talk back

`operonx.app.jobs.Job` puts work into a graph from a *source* — a JSONL
or CSV file, a Python iterable, or a ``source:`` resource — one run per
item, writes what `egress` sends to a *sink*, and leaves a record per
run (``run.json`` + ``items.jsonl``: key, status, error, trace id, ms,
sent). The graph is the same graph `[[serve]]` would put behind a route:
a ``JobSession`` is a ``Session`` over a source and a sink, so `ingress`
and `egress` need no change and neither does anything between them.

Per-item outcomes are ``ok``, ``failed``, ``empty`` (the run finished and
sent nothing — the batch bug that otherwise reports OK) and ``skipped``
(done in the run being resumed). ``on_error`` is ``skip``, ``stop`` or
``retry:N``; ``concurrency`` bounds items in flight; ``key`` names the
item field that identifies it, so ``run(resume=True)`` reruns only what
the last run did not finish. A graph with no doors takes the item as an
input (``item_input=``) and its result is the item's result — the
``engine.batch()`` shape, with a record.

``source:`` and ``sink:`` are resource categories (``kind: jsonl | csv |
python``), so resources.yaml names a job's inputs and outputs the way it
names an LLM.

``[[job]]`` in ``operonx.toml`` declares one beside the ``[[serve]]``
blocks — the same graph served and run over a file from one manifest;
paths are relative to the manifest, ``schedule`` is cron text that is
listed, not executed. ``operonx-run <name>`` runs it, ``--list`` prints
them, ``module:attr`` still names a Job object directly.

``session = "stream"`` feeds every item through one run — the callbot's
shape: shared state, one trace, no per-item accounting; the record counts
``fed`` and ``sent`` and holds the trace id, and cannot resume.

Every run a job mints carries ``job``, ``job_run`` and ``key`` on its
trace, as fields and as tags, so Langfuse filters one job, one run or one
item. A job is never a span. ``serve_session(metadata=…)`` is how they
get there, and is open to any transport.

**Runbook — many jobs, one command.** ``Runbook("nightly", extract >>
[embed >> cluster, score])``: ``>>`` is `Sequential`, a list is
`Parallel`, composed *above* the engine and walked by asyncio — never a
graph of jobs, whose trace would nest a job inside a run inside a job.
A failed step ends its sequence (``on_error="continue"`` runs on); a
parallel branch always finishes; hand-off between stages is by naming
the same resource. The record, ``jobs/<runbook>/<run>/run.json``, holds
the tree with a status per node and each job's own run id. A runbook is
never a span. In the manifest: ``[[job]] name = "nightly" runbook =
"module:attr"``.

**A deadline per item.** ``item_timeout`` on a Job (and in the manifest)
cancels a run that overstays and records the item ``timeout``, which
``retry:N``, ``stop`` and ``--resume`` treat like a failure.
``serve_session(timeout=…)`` is the mechanism and raises ``RunTimeout``,
the one case in which a transport cancels the run it minted.

Example: ``examples/python/ex17_jobs``. Design: ``docs/JOB_PLAN.md``.

### Fixed — `BoundedSession.end_input()` on a full bound

It put its end-of-input sentinel with ``put_nowait`` and raised
``QueueFull`` when the bound was reached — a peer hanging up while its
packets were still queued. The sentinel only wakes a reader parked in
``get()``, and none is parked while the queue is full, so the case is now
a no-op and ``recv`` ends once it has drained. Found by a stream job
whose source outran its graph.

## [1.6.4] - 2026-09-23

### Added — transient retry and a configurable timeout on Triton

`TritonClient.infer` retries transport failures that say "no answer
arrived" — `DEADLINE_EXCEEDED`, `UNAVAILABLE`, `RESOURCE_EXHAUSTED`,
`ABORTED`, `INTERNAL` — with backoff and jitter. Refusals
(`INVALID_ARGUMENT`, `NOT_FOUND`, ...) and unclassifiable errors are
raised on the first attempt: a retry that cannot help still costs a full
deadline.

`EmbeddingConfig` gains `timeout`, `max_retries`, `retry_base_delay` and
`retry_max_delay`. The timeout was a literal inside the op, so a
deployment needing longer had to patch operonx.

Why retry rather than a longer timeout: measured against a deployed
bge-m3, a full batch of sentences at five-way concurrency answers in
5.5s against a 30s deadline. Deadlines still expired — because the
budget went somewhere other than inference, on a cold channel's TLS
handshake or a response landing while the event loop was busy. Both
succeed on the next attempt; no timeout value fixes either.

Defaults are 2 retries and 30s, so existing behaviour is unchanged apart
from one-off failures now recovering.

## [1.6.3] - 2026-09-23

### Fixed — gRPC teardown noise after every Triton run

`TritonClient.get()` caches a client per `(url, ssl)` and nothing ever
closed the channel. An open aio channel is torn down by
`AioChannel.__dealloc__` during interpreter shutdown — after `grpc_aio`
has cleared its own globals — so it reaches for a `POLLER` that is
already `None`:

    Exception ignored in: 'grpc._cython.cygrpc.AioChannel.__dealloc__'
    AttributeError: 'NoneType' object has no attribute 'POLLER'

Harmless, and printed twice under the run's own result on every process
that embedded anything, which reads as a failed run.

`close_all()` is now registered with `atexit`, which fires while grpc
still has its globals, so that path is never taken. Failures inside it
are swallowed: the function exists to remove noise at shutdown and must
not become a source of it.

## [1.6.2] - 2026-09-23

### Fixed — completion content arriving as blocks instead of a string

`LLMOp` declares `content (str)` and `parse_and_extract` calls `.strip()`
on it before anything else. Providers speaking the Anthropic/Gemini
content-parts dialect answer with a list instead:

    [{"type": "text", "text": "{...}", "thoughtSignature": "AY89..."}]

`_extract_completion` passed that through untouched, so parsing raised
`'list' object has no attribute 'strip'`.

The raise is caught, which is what made it expensive: it lands in the
op's `error` field, `result` becomes `None`, and downstream that is
indistinguishable from "the model found nothing". A scanner op that had
correctly flagged a violation reported a clean result. HTTP 200, right
answer from the model, wrong answer from the pipeline.

Content blocks are now collapsed to their text in `_extract_completion`,
so every backend benefits rather than one. Blocks carrying no text —
reasoning signatures, refusals — are dropped rather than stringified, and
a list with no text at all collapses to `""` so `_is_empty_completion`
can route it to the transport retry. That check now sees through blocks
too.

hush carried this same collapse in the same method; the Operon port
dropped it. This restores it.

## [1.6.1] - 2026-09-23

### Fixed — auto-soften when the deciding branch is itself a predecessor

`_auto_soften_edges` proves two predecessors of a merge are mutually
exclusive by asking each which arm of a shared branch it arrived through.
That question has no answer when the predecessor *is* the branch:

    B --[cond]--> gate -> ... -> P --+
      \-[else]-----------------------+--> M

`P` reports arm `gate`; `B` reports nothing, because reaching `B` from its
own successors would need a cycle. With no shared ancestor the pair was
never found exclusive, both edges stayed hard, and on a run down the else
arm the scheduler never dispatched `M` — no error, no output, the graph
simply stopped.

`B`'s edge into `M` does have an arm: it is `M`. Recording that zero-hop
case makes the signatures disjoint exactly when they should be.

Every "gate that can skip a step" has this shape — a detector that runs
on some inputs only, whose verdict rejoins the path the rest took
directly.

## [1.6.0] - 2026-09-22

Gateway-shaped deployments. Everything here came out of running operonx
against a bank's Databricks + Triton stack, where the endpoint in front of
a model shapes the request as much as the model does.

### Added — `oauth2:` token provider

The sibling of `keycloak:`, for endpoints issuing tokens from a plain
`grant_type=client_credentials` POST (Databricks, Azure AD). Same shape:
lazy first fetch, a daemon refreshing ahead of expiry, `get_token()`.

`api_key: "oauth2:<name>"` now resolves the same way `keycloak:` always
has, because the hub stopped hardcoding one prefix — `TOKEN_REF_PREFIXES`
lists the categories whose instances expose `get_token()`, and adding an
auth scheme is an entry there plus a `REGISTRY.register`. `_resolve_keycloak`
and `_refresh_keycloak` remain as aliases; instances carry `_token_provider`
with `_keycloak_provider` kept pointing at the same object.

A key containing a colon is no longer mistaken for a reference:
`sk-proj:abc` stays a literal, since only a registered category counts.

`verify_ssl` on `OAuth2TokenConfig` defaults to **False**, matching where
these endpoints usually sit (behind a TLS-inspecting proxy whose CA is not
in the image). The request carries a client secret, so set it True wherever
the chain does validate.

### Added — Databricks LLM backends (`db-anthropic`, `db-gemini`)

Two endpoints on one workspace host that do not accept the same body:

* `/serving-endpoints` — Claude. `DatabricksAnthropic` keeps Anthropic
  content-parts and `cache_control` intact so prompt caching works, and
  enforces the 4-breakpoint limit locally, where the error can say so.
* `/ai-gateway/mlflow/v1` — Gemini. `DatabricksGemini` flattens multi-part
  text and strips `cache_control`. Sent unflattened, that endpoint answers
  `401 - Credential was not sent or was of an unsupported type`, which
  sends you to look at your token for a message-shape problem.

The suffix belongs in `base_url`; neither class rewrites it.

### Added — transport retry on `LLMOp`

`LLMOp.max_retries` was the only retry operonx had, and it is *semantic* —
it re-asks the model after a parser or validator rejects a well-formed
answer. A rate-limited gateway needs backoff, not a re-prompt.

`_call_with_retry` is that second thing, driven by the resource:
`max_retries`, `retry_base_delay`, `retry_min_delay`, `retry_max_delay`,
`retry_on_empty` on `LLMConfig`. It fires on 429 (honouring `retry-after`),
`APIConnectionError` / `APITimeoutError`, any 5xx — 4xx propagates, since
retrying a bad request only burns quota — and on an HTTP 200 whose content
is empty, which is how Anthropic reports transient overload. Defaults to
`max_retries: 0`, so nothing retries unless a resource asks.

Both the primary and each fallback go through it, each on its own policy.

### Added — `generation_extras` on `LLMConfig`

Per-resource vendor knobs merged into every call for that resource:
`reasoning_effort` for Gemini, `thinking` for Anthropic. Merged
per-attempt, so a fallback gets its own and not the primary's; the call
site wins over the resource default.

**A null value removes the key** rather than sending null — which is how a
resource opts out of something operonx would otherwise send. Claude 4.6
rejects a request carrying both `temperature` and `top_p`, and declares
`top_p: null`. `_prepare_params` makes `temperature` / `top_p` optional to
complete that contract.

### Added — Triton embedding backend (`api_type: triton`)

`EmbeddingType.TRITON`, with the config fields that go with it:
`max_length`, `output_name`, `tokenizer_path`, `input_name`, `ssl`.

Two input contracts, selected by `input_name`: unset sends
`input_ids` / `attention_mask` as INT64 and the client tokenises
(`tokenizer_path` required); set, it sends that one input as utf-8 BYTES
and the server tokenises — in which case `max_length` stops meaning
anything, because the server decides where to cut.

The op contract is `texts -> embeddings` in both modes. Token ids never
appear in a signature, which is what keeps `triton` substitutable for
`tei` / `vllm` / `onnx`.

`output_name` is read directly, never pooled — BGE-M3 exports both
`token_embeddings` and `sentence_embedding`, and mean-pooling the first
gives a different vector from the second.

### Added — `ResourceHub.alias(alias, target)`

An in-memory `alias -> key` hop, so a graph can name a *role* at the call
site (`resource="scanner"`) while an operator still chooses which resource
fills it. Tooling that reads a graph without importing it can only see a
literal, and this keeps the literal literal.

Deliberately not `register()`, which persists through to storage and
rewrites `resources.yaml` without its comments. One hop only: a chain is
refused at declaration rather than left to become a cycle at first use.

### Fixed — `LocalConsumer` crashed writing traces on Windows

`meta.json`, `nodes.jsonl` and `view.txt` were written with no `encoding`,
so on a cp1252 locale the arrow in `view.txt` raised
`UnicodeEncodeError` and the whole trace was lost. All three now pin UTF-8.

### Fixed — BYTES tensors were unmappable

`numpy_to_triton_dtype` matched dtypes by equality, which never matches a
parameterised string dtype: `|S1` is not `== np.bytes_`. Any model with a
text input was unreachable. Matching is now by dtype *kind*, covering
`object` / `bytes_` / `str_`.

### Changed — `TritonClient.get()` takes `ssl`

An endpoint behind an ingress on 443 needs a TLS channel, and TLS is not
expressible in a `host:port` URL. The process-wide client cache is keyed by
`(url, ssl)`, since a plaintext and a TLS channel to the same address are
different connections and sharing one fails at handshake time.

### Changed — `validators=` accepts a callable

Alongside the per-field allow-list, `validators=` now takes a
`Callable[[dict], bool]` over the whole parsed dict, for structural checks
an allow-list cannot state ("`result` must be a dict containing
`violation`"). It runs on model output, so a validator that raises counts
as a rejection rather than taking the graph down.

### Added — `tests/live/`

Opt-in tests against real endpoints, off unless `OPERONX_LIVE=1`, so the
default `pytest` run stays offline and CI-safe. Each test skips — naming
the reason — when its resource is missing from `resources.yaml` or a
`${VAR}` it needs is unset, so a partial `.env` runs the part it can and
adding credentials later turns the rest on with no code change.

Split by **network zone**, not by feature, because a corporate VPN and
the public internet are usually mutually exclusive:

* `-m public` — OpenRouter and OpenAI. Covers everything
  provider-agnostic: transport retry (including that a 4xx is *not*
  retried), the `generation_extras` merge and its null-strips-key rule,
  callable validators, `ResourceHub.alias` routing a real call, and the
  LLMOp path stringing them together.
* `-m vpn` — the private half, limited to what a public endpoint cannot
  prove: fetching a token from one specific OAuth2 issuer, the message
  shaping the two Databricks proxies demand, and a Triton server whose
  input contract differs by deployment.


## [1.5.2] - 2026-09-20

### Changed — Langfuse consumer ships the ctx tree, run-scoped ids, real dates

`LangfuseConsumer` no longer guesses a parent from data edges
(`parent_strategy` is accepted and ignored). The tree comes from `ctx`:
a generator's yield record is the container of everything dispatched for
that item, an op whose edges stop at a GraphOp boundary attaches to the
yield of its ctx, GraphOp members nest under one container span per
(graph, ctx), and a transient stream's unrecorded yields get one
stand-in span each (`audio_in [357]`). Observation ids are
`f"{run_id}/{op_id}"`, so a call no longer overwrites the spans of the
call before it; times use the trace's wall anchor instead of a perf
counter read as an epoch (every span used to date from January 1970).
`LLMOp` records ship as `generation` with model and token usage. Names
are op names; a yield is `synthesize [2]`. Per-event errors in the
ingestion reply are logged. `OpExecution` gains `op_type` and
`is_yield`; the local consumer writes both. `build_tree(trace)` is
public for other viewers.

### Added — wall-clock anchor and run identity on the workflow trace

`WorkflowTrace.wall_started_at` is `time.time()` taken with the
perf-counter `started_at`; `trace.wall_of(perf)` converts any record
timestamp to epoch seconds, so a consumer writes real dates while the
hot path keeps its single monotonic clock. `trace.run_id` names the run
for external ids (`f"{run_id}/{op_id}"`), because `op_id` alone repeats
in every run of the same graph. The local consumer writes `wall_start`
per record and `wall_started_at` in `meta.json`. Groundwork for the
Langfuse consumer rewrite in `docs/TRACING_CTX_TREE_PLAN.md`.

### Added — `show_keys`: the outputs that stand for an op

An op can name the one or two outputs a viewer should print for it
when it has room for a line instead of a port list (the studio card,
zoomed in). Resolution, first hit wins: `my_op(..., show_keys="text")`
at the call site (graphs and op classes too), `@op(show_keys="text")`
on the function, then the class attribute `show_keys_default`. Unset
is empty, and the viewer picks from the dataflow.

Built-in defaults: `LLMOp` → its extracted field keys when
`fields=[...]` is set, else `content`; `BranchOp` → `target`;
`EmbeddingOp` → `embeddings`; `RerankOp` → `reranks`;
`VectorSearchOp` → `ids`, `scores`; `DocFetchOp` → `rows`.

`show_keys` is a reserved op keyword: an `@op` function with a
parameter of that name gets the existing collision warning. Keys are
not validated against the op's outputs.

## [1.5.0] - 2026-08-29

### Fixed — `@op(transient=True)` silently dropped data past two ops

Two bugs, one cause. Anything longer than a producer wired straight into
one consumer lost items, and an async consumer lost all of them:

```
transient=False  mid=sync  ->  OK
transient=False  mid=io    ->  OK
transient=True   mid=sync  ->  ['i0', None, None]   only the first survived
transient=True   mid=io    ->  [None, None, None]   nothing survived
```

The release guard freed a context while a consumer still had to read from
it. `tasks_by_ctx` sees dispatched work, `inline_pending` sees inline work,
`seq_queues` sees work parked behind a sequential edge — and none of them
sees a frame still sitting on the scheduler queue, whose consumer has
therefore not been dispatched at all. A `bound="sync"` consumer is drained
inline and so was usually visible by then; a `bound="io"` consumer never
was.

The guard now also counts, per context, the events enqueued but not yet
handled, and release is re-attempted once each event has been processed.

Both hid for the same reason: every test written for this feature used a
`bound="sync"` consumer, and a two-op chain cannot reach either bug. The
regression tests are parametrised over `bound` and chain length, and they
assert the other direction too — retention stays flat at 9 cell entries
from 50 items to 2000, against 60,009 for the same graph without
`transient=True` — because every correctness test here would still pass if
the guard simply stopped releasing anything.

**Anyone using `@op(transient=True)` on 1.4.0 should upgrade.**

### Added — serve layer: a manifest that boots, not one that only describes

`operonx.toml` had declared `[[serve]]` all along, because nothing derived
from a graph can say what puts work into it — uvicorn calls an ASGI route,
which calls `engine.start()`, and that hop is not an op. Nothing in
operonx read the file. Meanwhile `engine.serve()` delegated to
`operonx.serve.OperonApp`, a package that is not installed, not a
dependency and not in this repository, so every call raised. Two halves
describing the same thing, neither working, never connected.

* `operonx.app.manifest` parses `operonx.toml`. `[[serve]]` names the
  graph it runs by entry point and carries session semantics;
  `${VAR:default}` resolves through the same code `resources.yaml` uses.
  `[[graph]]` remains, for graphs nothing serves. A stream transport must
  declare `max_inflight` — there is no version key and no way to opt out,
  because an unbounded queue behind a socket is how `operonx.io.Channel`
  came to exist.
* `operonx.app.serve` is a transport **interface**, with `http`,
  `websocket` and `asgi` as built-ins that register through the same call
  a project uses for its own. A SIP trunk or a Kafka consumer is two
  methods and a registration. The in-memory transport and a third-party
  gate — a transport inheriting nothing and importing no internals,
  driving a real graph — were written *before* any network built-in, so
  the built-in could not quietly become the contract.
* `Session.send()` returns a bool. A caller that paces audio has to report
  what actually landed — the callbot's `play_frame` sends forty-odd chunks
  per spoken frame and its audit line is the row you read when audio
  sounds wrong. Fire-and-forget would have forced that op to keep talking
  to a WebSocket directly, which is the one coupling this interface exists
  to remove.
* `ingress` / `egress` bind the transport as ops, so it keeps real spans
  and sweepable contexts instead of being an async seam beside the graph.
  Neither names a resource: the run was minted by a transport and already
  carries its session.
* `session = "per_request"` answers 500 when a run produces nothing, never
  200 with an empty body. Op exceptions are caught by the scheduler and
  logged rather than raised, so from the transport's side that is what a
  failure looks like — and for one caller waiting on one request, nothing
  is a failure.
* A run always finishes; a transport never cancels it. Work that has to
  happen after the peer has gone — writing a call record — only survives
  if the run is allowed to drain.
* `on_session` / `on_close` are the door: a connection becomes a run, and
  a run becomes a record, exactly once, on every path including failure.
* `[project] on_startup` runs before any endpoint accepts. Once
  `operonx serve` owns the process it owns process startup, and warming a
  model after the first caller has arrived is the same as not warming it.
* `handle.graph_name` — reading a declared cell from a teardown hook needs
  the graph's name, and the caller does not hold the engine.
* `serve.json_object()` returns a dict or the default, never anything
  else. Guarding `JSONDecodeError` guards the wrong half: `[1,2,3]`,
  `"hello"`, `42`, `true` and `null` all parse cleanly and are not
  objects, and `null` slips past an `is None` check written before the
  parse.
* `operonx-serve` boots every listener a manifest declares; `--list`
  prints what would run without booting anything.

### Fixed — six quiet failures in the serve layer

Found by an adversarial pass written to break the layer rather than
confirm it. None of them raised anything a person would see.

* A raising `on_session` leaked the socket, skipped teardown and logged
  nothing — so a project counting active connections in that hook inflated
  its counter for the life of the process.
* A drained session blocked a second reader forever, with no error and no
  log.
* A `close()` that raised turned a completed run into a failed one.
* A `send()` that raised killed the run instead of costing its item.
* `on_session` returning the wrong type failed frames later, as an
  `AttributeError` on `.inputs` from inside the run.
* Ports were not range-checked, so 99999 parsed and failed at bind.

Every unresolvable import now names the manifest entry that asked for it.

### Changed — `Operon.serve()` runs instead of raising

It builds the same `ServeSpec` the manifest would and serves it. Its two
dead arguments now raise: no caller can depend on them, since every
previous call raised `ImportError`, and accepting `backend="rust"` while
quietly serving Python is a promise the caller thinks was kept.

`tomli` moves from a dev extra to a core dependency — the manifest is how
a project declares what it serves, and `tomllib` is stdlib only from 3.11
while the floor here is 3.10.

### Added — `@op(transient=True)`: streaming runs stop retaining every item

A run never freed per-item state. `MemoryState._cells[idx]` is keyed by
context, every dispatched item mints one, and nothing in the package
removed an entry — the scheduler pops its own bookkeeping and leaves the
cells alone. Request-response graphs never notice; a long-lived streaming
run grew linearly with items forever.

```
generator -> one downstream op, 32 KB items

  items     before      after
    250     8.6 MB     1.0 MB
   1000    33.6 MB     3.1 MB
   4000   134.4 MB     2.0 MB      cells flat at 9
```

`@op(transient=True)` marks an op's per-item outputs store-deliver-evict:
released when the consuming context finishes. Opt-in, because a general
context GC would have to prove nobody else will read a value —
`.collect()` buffers, push refs into shared cells, interrupt replay — and
each is a way to be silently wrong.

Transience propagates along pull refs. Reading an input caches it in the
reader's own cell, and both cells hold the *same object*, so marking only
the producer frees nothing. One flag on the producer covers the chain.

The tracer had to learn it too: an `OpExecution` holds its inputs and
outputs for the life of the run, so copying a transient payload there
pinned exactly what the eviction released. Transient values are
summarised, a transient generator emits one span for the whole stream
rather than one per yield, and a transient op emits no per-item node on
success. Failures always emit.

Three compile-time guards, each naming the offending hop: `.collect()` on
a transient chain, more than one consumer of a transient port, and a
shared cell marked transient by propagation. Pushing a transient value
*into* a declared cell stays legal — the shared cell keeps it.

The trade, stated plainly: transient paths lose per-item observability.
One span with a count, and every failure.

### Fixed — a log message kept losing its own square brackets

Rich uses `[tag]` for markup, so any message carrying literal brackets —
every JSON array — looked like markup and was deleted:

```python
LOGGER.info("%s", '{"transcript": [{"speaker": "agent"}], "a": 1}')
#  ->  {"transcript": , "a": 1}
```

Silent data loss, on both console paths and for different reasons.
`PlainTextFormatter` called `strip_markup()`, which *does* restore
`\[...]` escapes — but nothing produced them for an ordinary log call, so
the content was stripped first. `ColoredRichHandler` removed every `[...]`
outright below INFO, and at INFO handed the raw message to Rich, which
parsed the brackets as tags.

`_escape_non_rich` is the routine that gets this right — it keeps known
Rich tags and escapes the rest — and had only ever been applied to
templated events. Both paths use it now.

It was also wrong on nesting. Replacing whole `[...]` regex matches means
a match swallows its own contents, so a bracket nested inside another was
never examined:

```
[[1,2],[3]]   ->   [,[3]]
```

Nested arrays are ordinary JSON, so this affected real payloads. It now
scans each opening bracket and decides individually, which also handles an
unclosed `[`. Rich markup still renders: `[bold]hi[/bold]` gives `hi`.

Found while moving a voice agent's per-call records off `print()` — the
CRM payload it logs is a transcript array, and the logger was quietly
dropping it.

## [1.4.0] - 2026-08-27

### Added — `operonx.io`: ending a stream that feeds a graph

Streaming into a graph was always possible — hand it an `asyncio.Queue` and
write a generator op that drains it. What that left to every project was the
*ending*: how a producer says "no more input, finish what you started".

`handle.cancel()` does not answer it, and cannot be made to. Cancellation is
abrupt by design. Measured on a graph whose downstream op takes 300 ms per
item, with three items pushed and cancellation applied while the first is in
flight:

```
sentinel  started=[0, 1, 2]  finished=[0, 1, 2]
cancel    started=[0]        finished=[]
```

For a call that ends with a spoken goodbye, the second row is the goodbye cut
off mid-sentence. So projects invent an in-band sentinel, usually `None`,
which quietly makes `None` unsendable.

- **`Channel`** — a bounded conduit. `push()` applies backpressure; `close()`
  is a graceful end, so queued items still arrive and the graph completes on
  its own. The end-of-stream marker is a private sentinel compared by
  identity, so any value a producer legitimately sends passes through.
  `close()` is idempotent and broadcasts to every consumer.
- **`channel_source`** — the receiving half as an op, written once.
- **`receive()`** — for sources that cannot use `async for` because they
  interleave the read with a timeout. It raises `ChannelClosed` at
  end-of-stream rather than returning `None`, for the same reason the marker
  is private.

No transport adapters ship, and none are planned: a WebSocket, an SSE
stream, a Kafka consumer and a file are the same three lines against
`Channel`, written where that transport's own concerns already live.

### Fixed — a declared cell's mutable default leaked between runs

`PARENT.declare(bag=set())` evaluates `set()` once — when the graph is
**built**, at import — and that object becomes the cell's default in the
schema. Every run then built its own `MemoryState` and its own `Cell`, but
`Cell(v)` stored the reference, so both pointed at that one object:

```
schema default id : ...737216   built ONCE

run 1:  MemoryState ...468896   Cell ...553472   VALUE ...737216   [1]
run 2:  MemoryState ...469216   Cell ...556864   VALUE ...737216   [1, 2]
                    ^ new             ^ new            ^ SAME
```

Per-run isolation was never missing — each run really does get its own
state. What was shared is the value that state *starts from*, so an op
mutating it in place wrote into every future run's starting point. A frozen
default was always safe, because `replace()` rebinds the cell rather than
editing what it points at; a `set` / `list` / `dict` was not.

Found in a voice agent whose `committed_turns` cell holds the turn ids a
call has already recorded. The first call filled it; the second found those
ids already present, recorded nothing, and produced an empty transcript.
The symptom appears on the **second** run, which is why no test caught it —
none of the six test files using `declare()` starts the same engine twice.

`list`, `dict` and `set` defaults are now copied per run, for declared
cells. Deliberately **not** a blanket `deepcopy`: a default may legitimately
hold a resource handle — an ONNX session, a Triton client — and those reject
deepcopy with "no default `__reduce__`". Anything outside those three
containers keeps its aliasing, and a test pins that so the fix is not later
"improved" into a deepcopy.

Cost is one shallow copy per declared cell per **run**, not per op: 1.8 µs
across seven cells, against calls that last tens of seconds.

Nested mutables inside a copied container are still shared — a shallow copy
is what makes the common case correct without guessing at contents.

### Fixed — `handle.cancel()` left the ops it spawned running

`ExecutionHandle.cancel()` cancelled the scheduler coroutine and the pump.
Neither touched `tasks_by_ctx`, so an op parked on something the scheduler
does not own — a generator draining a queue, a socket read — outlived the
run that owned it and never ran its `finally`.

The practical consequence was that **a streaming graph could not be stopped
from the outside at all**. Every caller had to invent an in-band sentinel
value and push it through the same queue the op was reading:

```python
await utterance_queue.put(None)     # the workaround this forced
```

Measured before the fix, on a graph whose source op awaits a queue:

```
handle.cancel()  ->  generator's finally ran: False   (the op leaks)
```

The scheduler's main loop now runs under a `try/finally` that cancels any
task still live in `tasks_by_ctx` and awaits it, reusing the same
cancel-then-`gather` idiom `_sweep_ctx` already uses for `Interrupt`. On a
normal exit it is a no-op — each `_pump` clears its own entry — and a
regression test covers both paths.

## [1.3.1] - 2026-08-19

### Fixed — `pip install operonx[...]` could not import `operonx.providers`

A packaging fault, not a code one, and it affected most extras rather than
one. `operonx/providers/llms/base.py` imports `httpx` at module scope while
the extras relied on it arriving transitively through the OpenAI SDK. openai
3.x moved to `httpx2`, so a fresh resolve stopped supplying it. Measured in
clean virtualenvs, **four of five extras tested failed outright**:

```
operonx[openai]   -> ModuleNotFoundError: httpx
operonx[faiss]    -> ModuleNotFoundError: httpx
operonx[gemini]   -> OK
operonx[bedrock]  -> ModuleNotFoundError: httpx
operonx[pgvector] -> ModuleNotFoundError: httpx
```

- `httpx>=0.24` is now declared by every extra that reaches
  `operonx.providers`, honouring the self-contained-extras rule stated above
  the extras block.
- The OpenAI **type** imports in `llms/base.py` moved under `TYPE_CHECKING`.
  All fourteen uses are annotations; nothing in the package resolves hints
  at runtime, `BaseLLM` is a plain ABC, and no module re-exports them. This
  finally delivers what `llms/__init__.py` has always documented: a
  retrieval-only install (`[faiss]`, `[pgvector]`, `[qdrant]`, `[onnx]`,
  `[postgres]`) no longer drags in an LLM SDK to do vector search.

### Fixed — install hints that pointed nowhere

- **The `providers` extra was restored.** It was deleted in 1f830c7 (Apr
  2026) while the install-tier comment and *nine* in-code references were
  left pointing at it. pip does not fail on an unknown extra — it warns and
  installs the base package — so the fix our own `ImportError` recommended
  appeared to work and then failed again. Two tutorial examples pinned it
  and silently got nothing.
- `DocStoreType.MONGO` and `.REDIS` have no backend module, so configuring
  one now raises `NotImplementedError` naming what does exist, instead of
  blaming a missing `operonx[mongo]` extra that never existed either.
- New regression guard, `tests/internal/cli/test_extras.py`: every install
  hint in the codebase must name a declared extra. Twenty-seven checked.
  Companion to `test_entry_points.py`, which guards the same class of rot
  for `[project.scripts]` after an identical migration-era regression. Both
  failure modes are only reachable when a user hits a missing dependency,
  which is exactly why they went unnoticed for months.

### Added — project tooling (separate distributions, not shipped with core)

`packages/operonx-project` and `packages/operonx-studio`: project
conventions, a manifest, deterministic graph extraction to a Project IR,
surgical config and source editors, and a local studio that renders a
project's graph, resources, env contract and dependencies. Neither is part
of the `operonx` distribution. See
[`docs/design/UI_PLATFORM_PLAN.md`](docs/design/UI_PLATFORM_PLAN.md).

## [1.3.0] - 2026-08-12

### Known issues

Four adversarial reviews produced **31 reproduced findings**; nine are
fixed here and **22 remain open**, each with a runnable repro, in
[`docs/design/OPEN_FINDINGS.md`](docs/design/OPEN_FINDINGS.md). The
highest-severity open ones: `Media` unwrapped only on an op's first
invocation (core), a budget-exhausted turn stranding an unanswered
`tool_call` in the stored history (agents), `grep` answering differently
depending on whether `ripgrep` is installed (harness), and an `MCPClient`
closed from the wrong task cancelling an unrelated task.

### Fixed — cancellation and fatal errors

Found by auditing the *shapes* the existing 29 interrupt tests never used:
every one of them applies `.parallel()` or a generator, which puts the
emitting op below the root context and gives it its own task. The default
shapes were all broken.

- **`Interrupt.SELF` at the graph root was still `Interrupt.ALL`.** The
  earlier fix moved the default from `()` to the emitter's context; for an
  op running at `("main",)` those are the same total sweep, so a flat graph
  kept the original defect. It now raises `InterruptTargetError` naming
  both ways out. A *nested* subgraph is exempt — its root ctx is also
  `("main",)` but its sweep runs in its own scheduler and cannot reach the
  parent, so the blast radius is bounded by construction.

- **Inline (`bound="sync"`) ops were never swept.** `@op` on a plain `def`
  resolves to `"sync"` — the default — and those run from
  `inline_pending`, which `_sweep_ctx` never touched. Measured:
  `Interrupt.ALL` cancelled 0 of 4 downstream ops and the run returned a
  normal-looking result.

- **The emitter's own queued EOF was dropped.** A non-generator op enqueues
  its `Interrupt` and its `EOF` in one event-loop slice, so the sweep found
  the EOF already queued and discarded it — while the sequential-edge
  section skipped the emitter precisely because it expected that EOF to
  advance the queue. Measured: 6 items in, 1 out, no error.

- **A `BaseException` in `_pump` deadlocked the scheduler.**
  `ObserveBudgetExceeded` is a `BaseException` by design, and escaped
  without enqueuing anything: `inflight` reached zero while the main loop
  was parked on `queue.get()`. The run hung forever. Reachable from a plain
  `run()` since `observe_max` became always-on in this release cycle.

- **A reused `Interrupt` object was mutated in place.** A module-level
  `STOP = Interrupt(...)` was resolved once and kept the first emitter's
  context; later emissions swept a stale context and reported the wrong
  emitter. It is stamped into a copy now.

### Fixed — parsing and MCP

- **`convert_type` coerced a list object rather than its elements.**
  Repeated XML siblings build a list, so the output type depended on the
  data: one `<item>` gave `"a"`, two gave the string `"['a', 'b']"`, and a
  field declared `int` held a list. Introduced by the repeat fix earlier in
  this cycle.

- **MCP `list_tools` read only the first page.** A paginating server's
  later tools were never registered, and `allow=` then reported them as
  "not provided" — blaming the server for a tool it does provide. Measured
  against a 5-tool server with page size 2: operonx saw 2.

- **MCP tool names were not sanitised.** Only the namespace was. MCP allows
  dots and arbitrary length; providers require `^[A-Za-z0-9_-]{1,64}$` and
  reject the *whole request*, so one `github.create_issue` stopped every
  tool working, local ones included.

- **A structured-only MCP result read as empty.** A spec-legal server may
  answer entirely in `structuredContent`; reading only `content` returned
  `""` as a success.


### Fixed

- **`Interrupt()` with no `ctx_to_cancel` no longer cancels the entire
  run.** The field defaulted to `()`, which is a prefix of every context,
  so the sweep matched all of them. The run came back as
  `{"__interrupt__": …}` with no error anywhere — omitting one keyword
  argument silently discarded everything in flight and looked like
  success. The default is now `Interrupt.SELF`, a sentinel the scheduler
  resolves to the emitting op's own context; cancelling the whole run is
  spelled `Interrupt(ctx_to_cancel=Interrupt.ALL)`. Measured on eight
  parallel branches with one emitting an untargeted interrupt: two
  results before, seven after.

  The sentinel is deliberately not a tuple, so a code path that forgot to
  resolve it would raise rather than quietly matching everything again.

- **`@op(exclude=…)` / `@op(include=…)` now filter the V3 trace.**
  `base.py` documented these as "Checkpointer + Tracer both respect
  these"; only the checkpoint, custom and interrupt buses ever consulted
  them, and `OpExecution` recorded inputs and outputs verbatim. The one
  documented way to keep a credential out of an observable artifact
  excluded it from the durable log and printed it in the trace — and in
  every consumer built on the trace. Both the per-yield record for
  generators and the final record for batch ops are filtered.
  `should_emit_for_channel` moved to `operonx.core.ops.base` (still
  importable from `operonx.checkpoint.bridge`) because core cannot import
  the checkpoint package.

- **`@op(observe_max=N)` is enforced on every run, not only when a
  checkpointer is bound.** The counter lived in `bind_checkpointer`'s
  closure, which `engine.start` builds only when `checkpointer is not
  None` — so the runaway-generator circuit breaker did nothing under a
  plain `engine.run()`, the cheap path a runaway is most likely to be in.
  Measured: 50 frames emitted against a budget of 5, no error. It now
  lives in `bind_observe_budget`, bound on every run, and binds nothing
  when no op declares a budget. Vars silenced on *both* observer channels
  still cost nothing, because `ObserveBudgetExceeded`'s own message
  offers `@op(exclude=[…])` as a remedy.

- **`stream(mode="updates")` delivers each write as it lands.** It was
  paced by `async for _ in handle`, which only ticks on *output* frames,
  so a graph whose single output arrives at the end buffered every
  intermediate update and released them together. Measured: four
  generator yields 150ms apart, all delivered at once after the run —
  which is the shape of an LLM streaming into a consumer, so the one mode
  able to watch it was not actually live. Pacing now comes from the write
  bus.

  The related half is a contract, now written down rather than changed:
  `handle` frames and `mode="frames"` carry the graph's **outputs**. An op
  feeding only a downstream consumer emits none, whatever it yields.
  Widening that would put every intermediate var into `result()`, which is
  built from the same frames.

- **A missing field is a parse error instead of a silent `None`.**
  `{"bad": 1}` against `fields=["result: str"]` returned `{"result":
  None, "error": None}` — so `max_retries` never fired and the caller
  could not tell "the model answered wrongly" from "the model answered
  null". An absent field now reports an error naming it and the keys that
  were actually present; a field explicitly set to null is still an
  answer. A validator's `@default` counts as an answer, so it still
  applies.

  **A field may be marked optional** with `"name?: type"`, which is
  required for a *union schema* — one field list covering several
  response shapes, where most entries are expected to be absent on any
  given call. Without the marker such a call reports missing fields on
  every turn and burns its retries. See the migration note below.

- **A cancellation inside a nested subgraph reached nobody, and the
  parent reported a `None` result for it.** A subgraph runs its own
  scheduler with `output_queue=None` — correct for frames, which the
  outer scheduler forwards via `_out_vars`, but it also dropped the
  `__interrupt__` record, so `handle.interrupts` stayed empty. Meanwhile
  `GraphOp.run` yielded the cells as they stood, all-`None`, which the
  parent forwarded as an ordinary result. Measured: six branches with one
  interrupted reported `[0, 1, None, 3, 4, 5]` and zero interrupts; it now
  reports `[0, 1, 3, 4, 5]` and one. `Scheduler.run` returns a third value,
  `root_interrupted`, and the interrupt record falls back to the run-level
  queue.

- **A generator's untargeted `Interrupt` cancelled its sibling yields.**
  `_pump` stamps `result.ctx` with the op's *dispatch* ctx, because the
  self-cancel guard looks that value up in `tasks_by_ctx`. Resolving
  `Interrupt.SELF` against the same value meant a top-level generator's
  bare `Interrupt()` still swept everything under `("main",)` — F2 again,
  harder to see because earlier yields had already completed. It resolves
  against the per-yield `item_ctx` instead. Measured: 3 of 5 sibling
  yields survived, now 5 of 5.

- **XML's document element no longer hides the fields under it.** XML must
  have exactly one root, so `<r><result>X</result></r>` parsed to `{"r":
  {"result": "X"}}` while the caller reasonably wrote
  `fields=["result: str"]` and got `None`. A lone dict root is now
  descended into when the path misses at the top — including for
  `parsing.py`'s own docstring example, which was wrong for exactly this
  reason. JSON and YAML are untouched: there a single top-level key is a
  key the author chose.

- **Repeated XML leaf siblings are kept.** `<item>a</item><item>b</item>`
  collapsed to `"b"` — the leaf branch reassigned where the nested branch
  built a list. Both now build a list.

### Added

- **`Heartbeat` — running an agent on a schedule.** A timer that calls
  `session.send()`, for agents nobody is talking to: a monitor that polls
  a queue, an agent that files a morning report. Three decisions are made
  explicitly rather than left to chance, because each is a way a scheduler
  fails *silently* — a stopped one, a skipping one and a backlogged one
  all look identical from outside:

  - **An overlapping beat is skipped and counted.** Queueing by default
    builds a backlog that never drains; cancelling the live turn destroys
    work mid-flight. `skipped` exists so 400 dropped beats do not read as
    400 successful ones.
  - **A failing beat does not stop the clock** — a scheduler that dies on
    its first exception is indistinguishable from one with nothing to do.
    A throwing `on_result` or `on_error` cannot stop it either.
  - **`stop()` lets the current beat finish.** Cancelling mid-`send()`
    leaves the conversation ending on an unanswered user turn, which the
    next beat would build on.

- **MCP client — `operonx.agents.mcp`.** Connect to a Model Context
  Protocol server and expose its tools as ordinary `@tool` ops, so the
  ReAct loop, the permission gate and the redactor treat them like local
  ones. Install with `operonx[mcp]`.

  ```python
  client, names = await connect_mcp(MCPServer(name="fs", command="npx", args=[...]))
  agent = build_react_agent(...)      # register BEFORE building
  ```

  Three MCP-specific hazards are handled rather than inherited:

  - **Namespacing.** Tools register as `server__tool`. A third-party
    server able to register `bash` would be a remote code-execution hole.
  - **Unannotated means gated.** MCP's `readOnlyHint` / `destructiveHint`
    are optional and server-supplied. Absent hints mean *unknown*, and
    unknown third-party code asks a human before it runs.
  - **In-band errors raise.** MCP reports failure as a flag on an
    otherwise ordinary result, so the content of a failed call reads
    exactly like an answer.

- **`SCRATCH.get()`, `.keys()`, `.items()`.** `ScratchAccessor` documented
  itself as "dict-like" and had none of them, so `SCRATCH.get("k")` — the
  first idiom anyone reaches for — raised `AttributeError` from inside an
  op body, where `BaseOp.run` records it into state rather than raising.
  A typo therefore surfaced as an op failure. `SCRATCH["k"]` already
  returned `None` for a missing key, so `get()` adds only the spelling.

  Outside a run `get()` returns its default rather than a `ScratchRef`:
  a ref is a *wiring* marker, and `get(key, default)` reads as a value
  lookup, so returning one would smuggle a marker where data is expected.
  `SCRATCH["k"]` keeps returning a `ScratchRef` at construction time.

### Breaking — `prompt=` no longer accepts a list; use `messages=`

`LLMOp` treated its prompt as a template and ran `format_map` over every
string in it. Correct for a template, destructive for a conversation: any
brace in any message became a template variable that did not exist, and
the run died on the **next** model call.

```python
messages = [{"role": "tool", "content": '{"city": "Hanoi"}'}]
LLMOp.of(resource="x", prompt=messages)
# PromptError: Missing template variable(s) '"city"'
```

It was not an edge case. Every JSON tool result, every file the agent read
containing braces, and the model's **own tool-call arguments** — which are
a JSON string in the history — hit it. An agent poisoned its next turn
simply by calling a tool.

The two inputs are now separate and mutually exclusive:

| | Accepts | Formatted? |
|---|---|---|
| `prompt=` | str, dict | yes, with the template variables |
| `messages=` | list | never |

Passing both raises; passing neither raises; passing `messages=` with
template variables raises, because they could only be ignored.

**Migration.** `prompt=[…]` → `messages=[…]`. If you relied on a
*templated* message list — the multimodal case, where an image URL arrived
as `{image_url}` — build the list in an upstream `@op` and pass the result:

```python
@op
def build_vision_prompt(query: str, image_url: str) -> dict:
    return {"messages": [
        {"role": "user", "content": [
            {"type": "text", "text": f"Describe: {query}"},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]},
    ]}

llm = LLMOp.of(resource="gpt-4o", messages=p["messages"])
```

`prompt={"system": …, "user": …}` is unchanged and still covers the
standard two-message templated call.

This deletes `_escape_braces` and `prepare_prompt` from
`operonx.agents.ops.model_ops` — a full recursive walk of the conversation
on every turn, doubling braces so `format_map` could undo them, plus a
second walk to undo it. Both walks are gone.

- **An unmatched brace raised a bare `ValueError`.** `"cost is {"` skipped
  the `KeyError` branch that produces `PromptError`, so it surfaced as a
  format-string error with no mention of the prompt — a stack trace from
  the formatter for someone typing `{` in a chat message. It is a
  `PromptError` now, and the message names `messages=` as the fix.

- **The trace showed nothing for a `messages=` call.** The trace
  normaliser read `inputs.get("prompt")` only, so multimodal blocks went
  unwrapped. It reads both.

### Migration — union schemas need `?`

`fields=[…]` entries are **required** by default. Any list where some
entries are legitimately absent on a given response must mark those
entries optional, or every call reports an error and exhausts
`max_retries`:

```python
_SMART_FIELDS = [
    "result: str",        # always present — leave required
    "has_cccd?: bool",    # only on document turns
    "chosen_date?: str",  # only on scheduling turns
]
```

This is not hypothetical: callbot's `ahamove_hr` extractor declares
twelve fields whose own comment says "non-compound states emit only
`intent`; compound states emit the relevant subset". Every one of its
calls would have failed. Single-field extractors — the common case — need
no change.

### Changed

- **Streamed `LLMOp` frames carry `final`.** The last frame of a stream
  repeats the whole accumulated `content` through the same channel as the
  per-token deltas, so a consumer joining frames emitted the answer
  twice; the only thing distinguishing it was the incidental presence of
  `finish_reason`. Deltas are now `final=False` and the closing frame
  `final=True`. Batch calls default to `True`. Consumers should join the
  deltas or read the final frame, never both.

- **A `Ref` nested inside a dict or list is now rejected at construction
  instead of silently degrading to a literal.** Operonx wires one param
  to one state cell holding one pull-ref, so `my_op(cfg={"a": src["x"]})`
  had no cell to put `src["x"]` in. Three things broke at once, none of
  them noisily: the op received the `Ref` object rather than the value,
  no dependency edge was created (so ordering was unguaranteed), and
  `GraphOp._validate`'s cross-graph scope check never saw it — a `Ref`
  to an op in another graph passed hermeticity validation. It now raises
  `TypeError` naming the param, the exact path, and the source var.

  Only the *value* side is scanned. `my_op(inputs={"a": src["x"]})` is
  the params mapping, not a container holding a ref, and is unaffected.

  Found while probing `InterruptOp(payload={"tool": …, "args": …})` for
  the agent work — a human approving a destructive tool call was shown
  `Ref` objects instead of the tool name and arguments. It was never an
  `InterruptOp` bug; every op behaved this way.

  Supporting the nested form properly (hoisting each buried ref into its
  own cell and reassembling the container at read time) is a larger
  change that also alters the `serialize()` wire format operonx-rs
  consumes. This release makes the broken form loud; it does not yet
  make it work.

## [1.2.0] - 2026-08-11

Removes the two backend-named ops. See
[MIGRATION.md](MIGRATION.md#migrating-to-operonx-120) for recipes.

### Removed (BREAKING)

- `OnnxOp` — write a bare `@op` around
  `operonx.providers._utils.onnx.load_onnx_session`. ONNX remains a
  backend for `EmbeddingOp` and `RerankOp` via `api_type: onnx`.
- `TritonOp` — write a bare `@op` around
  `operonx.providers.triton.TritonClient.get(url)`, which supplies the
  pooled gRPC client, dtype translation and output decoding.

Both named their *transport* rather than a semantic: the op name told
you the runtime instead of the intent, so every backend needed its own
op and callers had to know the transport to pick one.

> **Removal was brought forward.** The warnings shipped in 1.1.0 said
> "removed in 2.0.0". If you are pinned `operonx>=1.x` and use either op,
> upgrading to 1.2.0 **will** break you — pin `operonx<1.2.0` until you
> have migrated.

### Changed (BREAKING)

- `OpType` cleanup. Removed `for`, `while`, `stream` (superseded in 1.0.0
  by back-edge loops, generator ops and `Ref.parallel()`), `parser`
  (`ParserOp` went in 1.0.0), and `milvus`, `mongo`, `s3` (named
  backends, never had ops). Added `interrupt` and `emit`, which
  `InterruptOp` / `EmitOp` have set since 1.0.0 without the Literal
  listing them — the drift this ends. Only affects code reading the
  Literal directly.
- `ParserError` now reports `op_type="code"` rather than `"parser"`.
- `operonx.tools` → **`operonx.cli`**. The package holds console-script
  entry points (`operonx-pack`); `tools` now reads as *agent tools* and
  is being freed for `operonx.agents`. No shim — a shim would keep the
  name occupied, which is the whole point of the move. The
  `operonx-pack` **command is unchanged**; only `from operonx.tools.pack
  import …` breaks.

### Removed

- Dead `operonx` console script. `pyproject.toml` declared
  `operonx = "operonx.cli:main"` from the April 2026 Hush→Operon
  migration through 1.1.0, pointing at a scaffolding CLI that the same
  migration deleted. `operonx --help` raised `ModuleNotFoundError` in
  every published release. `operonx-pack` is unaffected and remains the
  only console script.

### Added

- `operonx/agents/` — package scaffold for the agent composition layer
  (tools, dispatch, ReAct loops, sub-agents), with
  `operonx/agents/CONTRIBUTING.md` defining the Footprint Ladder and the
  op-worthy bar that govern what may land there. No public surface yet;
  see [AGENT_EXTENSION_PLAN.md](AGENT_EXTENSION_PLAN.md).

## [1.1.0] - 2026-08-11

Retrieval release: a two-store RAG stack, plus the first step of the op
taxonomy cleanup. See [OP_TAXONOMY_REFACTOR_PLAN.md](OP_TAXONOMY_REFACTOR_PLAN.md).

### Added — retrieval

- `VectorSearchOp` — vector similarity search returning `ids`, `scores`,
  and `metadata`, index-aligned and best-match first. Backend-native
  filters, never translated.
- `DocFetchOp` — fetch records by primary key from a store of record.
  Returns rows **in the order of the ids given**, and reports ids that
  matched nothing in `missing` rather than silently returning a shorter
  list.
- `operonx.providers.vector_stores` — `BaseVectorStore` (with a
  per-backend `bound` hint), config, lazy-import factory, and the
  **FAISS** (no server, `bound="cpu"`), **pgvector**, and **Qdrant**
  backends.
- `operonx.providers.doc_stores` — `BaseDocStore` (order restoration
  lives in the base so no backend can reintroduce misalignment), config,
  factory, and the **Postgres** and **memory** backends.
- `reorder_by_ids` / `partition_by_ids` — the ordering helpers
  `DocFetchOp` uses internally, exported for custom fetch ops.
- `operonx.providers.triton` — `TritonClient` with a process-cached gRPC
  channel and dict-in/dict-out `infer()`, plus pure dtype/decode helpers.
- New extras: `operonx[faiss]`, `operonx[pgvector]`, `operonx[postgres]`,
  `operonx[qdrant]`.
- `OpType` gains `"vector-search"` and `"doc-fetch"` (additive only).
- Example [`ex16_rag_pipeline`](examples/python/ex16_rag_pipeline/) — the
  full pipeline, runnable with no servers.

### Design notes

- **The vector index is derived data.** It holds vectors, ids, and small
  *filterable* metadata — never document content, which lives in the
  store of record. This avoids the most common silent RAG bug: a document
  updated in your database but stale inside the index's payload. It also
  makes hydration its own trace span, where it usually costs more
  wall-clock than the search itself.
- **Filters are backend-native, with no portable DSL.** A DSL leaks, and
  a mistranslation is silent — a filter that fails to apply returns
  *more* rows, which in a multi-tenant system is a data leak rather than
  a warning. Every backend validates its own dialect and raises on
  shapes it does not recognise; nothing degrades to "no filter".
- **FAISS refuses filters** rather than post-filtering in Python, which
  would silently return fewer than `top_k` hits.

### Deprecated

- `OnnxOp` — removed in 2.0.0. Write a bare `@op` around
  `operonx.providers._utils.onnx.load_onnx_session`. ONNX remains
  available as a backend for `EmbeddingOp` and `RerankOp`.
- `TritonOp` — removed in 2.0.0. Use `VectorSearchOp` where it applies,
  or a bare `@op` around `operonx.providers.triton.TritonClient.get(url)`
  (~15 lines, same pooled client).

Both still work in 1.1.x and emit a `DeprecationWarning` naming the
replacement.

### Fixed

- `docs/guide/04-rag.md` documented a `resources.yaml` format that does
  not exist (nested `embeddings:` / `llms:` blocks instead of flat
  `embedding:<name>:` keys) — copying it produced a load error.
- Removed the stale `ask` export from `operonx.providers`; the helper was
  deleted in 1.0.0, so `from operonx.providers import ask` raised
  `AttributeError`.
- Provider tests are auto-marked `integration` by their conftest, which
  CI's `-m "not integration"` selector excludes. Mock-only suites now
  carry the `unit` marker the conftest honours — 83 previously
  never-executed tests now run on every PR.

## [1.0.0] - 2026-08-10

Milestone release: state observability, HITL primitives, LangGraph-style
back-edge loops, structured-output LLMOp, and cleanup of deprecated
surfaces. See [MIGRATION.md](MIGRATION.md) for the upgrade recipe.

### Added — Phase 1 (state)
- `PARENT.declare(**vars, reducers={...})` — shared cells with optional
  fan-in reducers. Replaces `PARENT.shared()`.
- `operonx.reducers` — `add_messages` (LangGraph-compatible id-upsert
  + `RemoveMessage` / `REMOVE_ALL_MESSAGES` sentinels), `dict_merge`,
  plus support for `operator.add` / `operator.or_` from stdlib.

### Added — Phase 2 (observability + HITL)
- `Checkpointer` protocol + `InMemoryCheckpointer` with per-step delta
  storage; `get_state(step)` folds, `get_updates(step)` returns delta,
  `list_steps()` enumerates. Zero overhead when no checkpointer bound.
- `MemoryState._write_cell` funnel + write-observer bus. All cell
  mutations (including `SCRATCH[k] = v`) flow through it.
- `@op(exclude=..., include=..., observe_max=...)` — filter observability
  at emission source. Polymorphic list-or-dict; mutual-exclusion of
  include/exclude. `ObserveBudgetExceeded` (inherits `BaseException`)
  as a circuit breaker for runaway generator ops.
- `InterruptOp` — HITL suspend/resume via `asyncio.Future`.
- `EmitOp` — fire-and-forget custom events.
- `engine.stream(mode="updates" | "values" | "frames" | "custom")` +
  `engine.invoke()` alias.

### Added — Phase 3 (loops)
- Build-time cycle rewrite: write a back-edge inside `@graph` and the
  Phase 3 pass compiles it into a hidden `_GraphLoop`. LangGraph-style
  agent loops without the visible loop wrapper.
- `@graph(strict_dag=True)` opt-out for fail-fast on accidental cycles.

### Added — LLMOp structured-output layer
- `LLMOp.of(fields=..., parser=..., validators=..., max_retries=...,
  retry_hint=True)` — inline parse + validate + error-guided semantic
  retry on the same resource (Instructor-style).
- `LLMRefusalError` and `ValidatorError` exception classes; refusal
  detected via `finish_reason` in `{content_filter, safety}` or
  non-empty `extras.refusal` (structural, not content-heuristic).
- `operonx.providers.parsing` — pure functions
  (`parse_json` / `parse_xml` / `parse_yaml`, `ExtractField`,
  `extract_value_by_path`, `convert_type`, `apply_validators`,
  `parse_and_extract`) for text-only parsing without an LLM call.

### Changed
- Fallback trigger narrowed: `fallback=[...]` fires only on refusals,
  content-filter blocks, or exhausted transport (SDK-side). Parse /
  validator failures NO LONGER trigger fallback — they use
  `max_retries` on the same resource.
- Transport-level retries (429 / 5xx / timeout) are delegated to the
  underlying SDK; LLMOp does not add its own transport-retry knob to
  avoid double-retry surprises.

### Removed (BREAKING)
- `PARENT.shared(**vars)` — use `PARENT.declare(**vars)`.
- `GraphOp.loop(name=..., until=..., **initial_state)` constructor —
  use a back-edge inside `@graph` and let the rewrite synthesize the
  loop. The `_GraphLoop` type still exists internally.
- `@graph(until=..., max_iterations=...)` decorator surface — replaced
  by (a) back-edge for control-flow loops or (b) `LLMOp(max_retries=N)`
  for LLM parse/validate retry.
- `ParserOp` class + `ParserType` alias — parsing lives inline in
  `LLMOp(fields=..., parser=..., validators=...)`; pure functions for
  standalone use are in `operonx.providers.parsing`.
- `ask()` helper (`operonx.providers.ops.ask`) — subsumed by
  `LLMOp.of(fields=..., max_retries=...)`.

### Fixed
- Shared-cell reducer degraded to LWW on nested-ctx writes (from
  Phase 3 loop iterations) — `_write_cell` now reads `old` via
  `Cell.__getitem__` which handles the shared→DEFAULT_CONTEXT mapping.

## [0.11.0] - 2026-07-30

Branch-graph ergonomics release. Two additive, non-breaking features
that eliminate a class of silent-deadlock bugs and a class of
target-named-twice boilerplate at every branch-fan-in site in real
callbot / agent-workflow graphs. Both compose cleanly with the existing
`if_/else_` primitive and require zero migration for existing code.

### Added
- **Auto-soften branch-merge edges** at `GraphOp.build()`. When two
  predecessors of a merge op trace back to a common `BranchOp` ancestor
  via disjoint first-hop children, the incoming edges are automatically
  flipped to `soft` — the runtime pattern users had to remember to write
  with `~` on every branch fan-in (e.g. `denoise >> ~picker`,
  `skip_stt >> ~picker`) is now inferred from graph shape. Missing the
  `~` used to cause a silent deadlock at runtime; the build-time pass
  kills that whole bug class. Escape hatches: `GraphOp(auto_soft=False)`
  per-graph, `graph.add_edge(src, dst, hard=True)` per-edge. See
  [`docs/design/AUTO_SOFT_BRANCH_MERGE.md`](docs/design/AUTO_SOFT_BRANCH_MERGE.md).

- **Inline `if_/else_` branch API** — branch declarations can now drop
  directly into `>>` chains with op-instance targets, and the framework
  auto-adds the `branch → target` condition edges. The old `route = if_(cond, "name")`
  standalone form still works for forward-reference and named-branch
  cases. Auto-name resolves via `auto_name()` LHS → `route_N` per-graph
  counter fallback (with a source-parser false-positive guard to prevent
  a nearby `m = _mk(...)` line from being incorrectly captured). Example:
  ```python
  # Before
  stt_route = if_(cond, asr).else_(skip_stt)
  START >> source >> stt_route
  stt_route >> asr >> denoise >> picker
  stt_route >> skip_stt >> picker

  # After
  START >> source >> if_(cond, asr).else_(skip_stt)
  asr >> denoise >> picker
  skip_stt >> picker
  ```
  Combined with auto-soften above, the callbot's branch/merge wiring
  block dropped by 18 lines with zero behavioural change. See
  [`docs/design/BRANCH_INLINE_API.md`](docs/design/BRANCH_INLINE_API.md).

- **`EdgeConfig.auto_soft` and `EdgeConfig.pinned_hard`** — Python-side
  debug fields on edges. Not serialized to Rust; useful for tooling and
  build-time log analysis.

### Docs
- `docs/design/AUTO_SOFT_BRANCH_MERGE.md` — full spec, algorithm,
  limitations, empirical validation on the callbot.
- `docs/design/BRANCH_INLINE_API.md` — full spec, name-resolution rules,
  backward-compat guarantees.
- `AGENT_EXTENSION_PLAN.md` — op-native design for extending operonx
  into a full agent framework, grounded in a deep inspection of
  hermes-agent, opencode, agent-harness, smolagents, openclaw.

### Notes
- Zero Rust runtime changes. `EdgeConfig.soft` is serialized verbatim
  after the auto-soften pass mutates it; auto-added branch edges look
  identical to user-authored ones in the exported graph JSON.
- Backward compatibility: all 971 pre-existing tests pass. All existing
  manual `~` marks continue to work (the pass skips already-soft edges).
  All string-target `if_(cond, "name")` call sites work unchanged.
- Callbot dev branch verifies end-to-end: all 5 manual `~` marks
  removed, all 4 branch declarations inlined, 204 tests still pass.

## [0.8.5] - 2026-05-31

Bug-fix release. Closes a SCRATCH-propagation bug in nested @graph
dispatch that broke every workflow using subgraphs with SCRATCH-backed
inputs (educa_reminder callbot, ahamove_hr, every agent that reads
`current_state` / `intent_retry_counts` / `last_agent_response` inside
its agent_turn subgraph).

### Fixed — Rust
- **Nested @graph dispatch now inherits parent's SCRATCH**
  (`core/ops/graph/task_scheduler.rs::run_collect`). Previously the
  child sub-scheduler was started with a fresh empty
  `Arc<Mutex<HashMap>>` so every `SCRATCH[key]` read inside the subgraph
  returned `Null`, breaking every educa_reminder-style agent whose
  state machine inputs are SCRATCH refs at the subgraph level. The fix
  threads the parent's SCRATCH Arc through `execute_op` →
  `run_collect`, matching Python's `child._scheduler.run` shared-Arc
  semantics. `run_collect` keeps a fresh-SCRATCH fallback for the
  legacy standalone-test callers that pass `None`.

## [0.8.4] - 2026-05-31

Bug-fix release. No API changes; behavioural parity with Python tightened
on conditional branch inputs.

### Fixed — Rust
- **Conditional-branch input refs now respect `default` fallback**
  (`core/ops/graph/task_scheduler.rs::resolve_inputs`). When a graph
  routes through a branch (`if_/else_`), downstream ops have refs that
  point at BOTH branches' outputs; only the taken branch fires, the
  other op's outputs are never set. Previously the resolver would error
  out the moment it hit a missing ref on the untaken branch, dropping
  the entire downstream chain. The resolver now falls back to `default`
  (or `Null` for non-required inputs) when the ref's source op produced
  no value at the runtime ctx — matching the Python parity behaviour
  the educa_reminder callbot graph relies on for `picker.audio_text`
  (asr branch) and every merge-style op (`merge_response`, `merge_turn`,
  `merge_intent`, `merge_overlap`, `merge_pending`).

## [0.8.3] - 2026-05-31

Rust-side Phase-1 sync release. Python crate stays at 0.8.2 — the
Rust crate version bumps to 0.8.3 to mark the merged sync work
shipped on top of the 0.8.2 parity baseline. No Python API changes.

### Added — Rust
- **Structured exception hierarchy** (`core::exceptions`) mirroring
  Python `OpError` / `ParserError` / `CodeError` / `BranchError` /
  `ConditionError` / `IterationError` / `PromptError` /
  `EmbeddingError` / `RerankError`, each carrying op_name + context +
  original_error. 21 new internal tests.
- **Ref evaluator gaps closed** — `Apply` / `Call` / `MatMul` /
  `RMatMul` variants + a real `GetAttr` separate from `GetItem`.
  AttributeError parity for missing keys. 11 new internal tests +
  shared spec fixture `core/refs/getattr_dict`.
- **Frame / Interrupt API parity** — typed `Interrupt` struct with
  canonical JSON shape (`__interrupt__: { ctx_to_cancel, reason }`),
  `ExecutionHandle::scratch()` + `ExecutionHandle::interrupts()`
  accessors backed by a shared `Arc<Mutex<HashMap>>` SCRATCH.
- **Sequential-edge cancel fix** — port of Python 0.8.1's
  `_sweep_ctx` fix that advances `seq_queues` on Interrupt cancel.
  Regression test included.
- **Event-stream tracing pipeline** (`core::tracing::{events,
  emitter, pipeline, processors, legacy, exporters::local_file}`) —
  full port of Python 0.8.0's tracing redesign. Replaces the legacy
  `collector` + `flush_worker` + `labels` modules (kept compiling
  during the migration). Processors: drop / redact / sample /
  truncate / group. Flush strategies: AtScheduledExit /
  FlushOnSize. JSON file exporter writes
  `~/.operonx/traces/<request_id>.json`.
- **LangfuseExporter** (`telemetry::exporters::langfuse`) — full
  port behind `langfuse` feature. trace-create + span-create batches,
  generation-create on LlmUsage events, Basic-auth Langfuse public
  ingestion endpoint, parent_observation_id walking via ctx tuple
  longest-prefix-first.
- **ParserOp** (`core::ops::transform::parser_op`) — port of
  Python's parse_json / parse_xml / parse_yaml + ExtractField path
  walker + @DEFAULT validators + convert_type coercion. quick-xml
  state machine for XML. Scheduler dispatch routes `OpType::Parser`.
  14 unit + 10 integration tests + 3 shared spec fixtures.
- **TritonOp gRPC client** (`providers::ops::triton`) — full port
  behind `triton` feature. tonic + vendored KServe v2 proto, pooled
  `Channel` per `TRITON_URL`, FP32 / FP64 / INT32 / INT64 / BYTES
  tensor codec, ResourceHub.get_config integration. End-to-end test
  with in-process tonic mock server.
- **OpenAI SSE streaming** (`providers::llms::openai::stream`) +
  `LLMOp` stream-mode wiring through `ExecutionHandle`. Match Python
  chunk schema.
- **Anthropic Messages API** (`providers::llms::anthropic`) — full
  port with SSE streaming, system-prompt split, stop_reason map,
  cache_read / cache_creation token surface.
- **Azure OpenAI** (`providers::llms::azure`) — reuses the OpenAI
  body+parser, URL = `<base>/openai/deployments/<model>/chat/
  completions?api-version=<v>`, api-key header auth.
- **TEI embedder** (`providers::embeddings::tei`) — POST `/embed`
  with `{inputs, truncate}` body.
- **TEI / vLLM / Pinecone rerankers** — POST `/rerank` family with
  Cohere-shape body, sorts desc + truncates, vendor-specific auth.
- **Keycloak token provider** (`providers::auth::keycloak`) — OIDC
  client_credentials grant, dot-path token extraction, background
  refresh task (abort-on-drop), cached lazy-fetch on first call.

### Test footprint
136 → 254 tests across the workspace (with `--features triton`).
13 → 16 shared spec fixtures consumed by both Python and Rust
runners. Parity contract maintained — every new Rust feature ships
with a fixture covering its user-visible behavior.

### Deferred
- Gemini LLM, OpenAI Batch coordinator, ONNX shared backend, and
  Langfuse prompt-manager remain Phase-5b stubs. None are callbot
  blockers; they error with a clear "not yet implemented" message.

## [0.7.1] - 2026-04-29

Follow-up to v0.7.0 — bug fixes + perf precompute work, all Python ↔
Rust parity preserved (22 / 22 bench patterns byte-equal). No public
API changes.

### Fixed
- **Rust LLM examples returned empty `{}`** (ex03 / ex04 / ex07 / ex08
  / ex09 / ex10 / ex12). `operonx::bootstrap()` didn't call
  `providers::registry::register_all()`, so the resource hub failed
  every `llm:gpt-4o-mini` lookup with `no factory registered for
  category 'llm'` before any HTTP request. Bootstrap now registers
  every built-in provider plugin idempotently, mirroring Python's
  "import triggers registration" pattern. Verified end-to-end against
  real OpenAI calls.
- **Generator ops collapsed to a single frame on Rust.** `is_generator:
  true` ops were treated as regular code ops; the `for_loop` /
  `map_op` scenarios in ex05, both `ex11.iteration` /
  `partial_failure`, ex14, and ex15 all returned `{}` because
  downstream per-item ops never dispatched. New `fan_out_value()`
  helper plus a switch in both the inline-sync fast-path and the
  spawn (io/cpu) path: generator ops now return `Value::Array` and
  the scheduler emits one Frame per element on a fresh `(parent_ctx,
  "yield_N")` sub-context. Empty array = zero frames (matches
  Python's skipped `yield`, used by ex15's `vad`).
- **Stale fixtures + pin** in the rust example bundles. `ex01`'s
  `inputs.json` and `main.rs` op params used `name` while the current
  Python factory takes `who`; `ex13` used `input` instead of `val`.
  Every `examples/rust/*/Cargo.toml` was pinned to `operonx =
  "0.6.2"` — semver-incompatible with the workspace's `0.7.x`, so
  `[patch.crates-io]` silently dropped through to the published 0.6.3
  wheel and the older `#[op]` macro errored on bare
  `::inventory::submit!`. Bumped to `0.7.1`.
- **`extras smoke (anthropic)` regression on PR #1.**
  `operonx/providers/llms/response.py` did a top-level `import
  aiohttp` that the anthropic extra (which ships `httpx`) didn't
  provide; deferred via `TYPE_CHECKING` + lazy import inside the demo
  helpers.
- **Anthropic + rerank integration tests no longer fail when their
  config is unavailable.** Anthropic `*_real` tests treat the
  `ci-dummy-…` key the providers conftest plants as "no key" and skip;
  the rerank `with_hub` test probes `hub.get("reranking:bge-m3-onnx")`
  up front and skips with the real reason instead of letting
  `_process()` swallow the model-dir-missing error.

### Changed
- **Rust scheduler — 5 precompute wins on the hot path.** All four
  pieces moved from per-frame work to `GraphScheduler::new` (per-engine
  build):
  1. `initial_ready_count` pre-converted from `BTreeMap` to `HashMap`
     — clones once per never-seen `ContextId` instead of converting.
  2. `RuntimeState::with_capacity(n)` — slot map pre-sized to
     `Σ (inputs + outputs)` across ops, eliminating the resize cycle.
  3. `seq_queues` / `seq_active` / `collect_bufs` pre-sized to the
     graph's edge counts.
  4. `edge_policies: HashMap<(src, dst), StreamPolicy>` cached at
     construction; `route_edge_async` does an O(1) lookup instead of
     re-walking `dst.inputs`.
  5. **Compiled ref pipeline.** Walks every `RefConfig` (op-input
     refs + branch case conditions, recursively into nested refs) and
     produces an enum-tagged `CompiledRef` / `CompiledOp` /
     `TransformKind` chain at construction. `__PARENT__` already
     substituted with the graph key. Per-op `Vec<InputSlot>` plan
     classifies each input as `Ref / Lit / Default / RequiredMissing
     / Null` up front. Runtime drops `transform.name.as_str()` matches
     for enum dispatch and replaces `for (var, param) in
     op_cfg.inputs` + per-input `match param.ref_config` with a tight
     slice walk.
- Net bench delta: scheduler-bound patterns (linear chains, branching,
  small parallel) ~5–12 % faster vs the v0.7.0 baseline; CPU-bound
  patterns (matrix chain) unchanged because they're dominated by
  matmul time. Numbers within run-to-run noise on the existing
  bench set; transform-heavy graphs (long ref chains) should see the
  bigger win from the compiled-ref dispatch.

### Added
- **`scripts/bench/parity.py` + `--probe` mode on the bench binary.**
  Runs every `<name>.graph.json` / `<name>.inputs.json` pair through
  both runtimes (Python `@graph` factory + Rust `operonx-bench
  --probe`) and diffs the output dicts key-by-key. Reports
  `PASS <name>` / `FAIL <name>` per pattern. Used to verify the
  precompute changes don't perturb output.

### Removed
- `published-smoke` CI workflow (was racy by design — fired on
  push-to-main before `Publish` had pushed the wheel; site-packages
  assertion picked up the source tree from CWD anyway). Regular
  tests + extras-smoke matrix already cover the surface.

## [0.7.0] - 2026-04-29

Major scheduler upgrades, a new packaged CLI, lazy providers for tier-1
lean imports, full docs depth pass.

### Added
- **`operonx-pack`** — packaged CLI (`pip install operonx` registers it)
  for serialising `@graph` factories to the JSON spec the Rust runtime
  loads. Pytest-style `module.path::symbol` positionals, optional
  `=customkey` to rename the bundle key, default-stdout / `-o PATH` for
  file output, `--no-bootstrap` for pure-compute graphs. Replaces the
  previous standalone `tools/dump-graph.py`.
- **`operonx.core.types.ChatMessage`** + `ChatRole` Literal — provider-
  neutral chat-message TypedDict. Landing pad for the v0.7+ LLMOp
  converter layer; today's providers still emit `openai.types.chat.*`
  for back-compat.
- **`scripts/bench/`** — Python ↔ Rust e2e bench: `generate.py` dumps 22
  shared `graph.json` patterns, `main.py` runs Python, `cargo run` runs
  Rust. Final headline: Rust wins every pattern. **3.2×** on linear,
  **1.5–1.7×** on fan-out and pure-noop nested @graph, **2.0–2.7×** on
  `if_()`-routed branching, **11–12×** on production-shape, **15–20×**
  under mixed CPU contention, **17–38×** on pure-compute matmul.
- **`examples/{python,rust}/exNN_*/`** — standalone project templates.
  Per-example `pyproject.toml` / `Cargo.toml`, single-file `main.py` /
  `main.rs`, per-example `.env.example` + `resources.yaml` where
  relevant. `examples/rust/.cargo/config.toml` patches `operonx` to
  the workspace path for in-repo development; users copying an example
  out of the repo pick up the registry version.
- Per-language indexes — `examples/python/README.md`,
  `examples/rust/README.md` — extras / feature mapping per example,
  cd-and-run command, runtime-status caveats per Rust example.
- `docs/guide/00b-patterns.md` — public Patterns reference page lifted
  out of CLAUDE.md (decorators, edges, refs, output mapping,
  iteration, `@graph.loop`, `if_()` routing, end-to-end composition).
- Mermaid diagrams across `docs/architecture/` — overview /
  execution-flow / state-model / streaming / rust-python pages each
  carry one diagram. Mkdocs wires the mermaid loader via
  `extra_javascript` plus a tiny init script that re-renders on
  Material's light/dark palette toggle.
### Changed
- **Rust scheduler — sync-op inline fast-path.** `OpBound::Sync` ops
  bypass `tokio::spawn` + semaphore + await; events go onto the queue
  via `try_send`. Per-op floor dropped from 44 µs to 15 µs.
- **Rust scheduler — nested `@graph` precompute + fast-path
  dispatch.** `GraphScheduler::new` recursively builds a child
  `GraphScheduler` for every nested `OpType::Graph` op at parent
  construction time (no more process-wide static cache). New
  `GraphScheduler::run_collect` runs the sub-scheduler inline in the
  caller's task with a tap-only `FrameSender` — no `tokio::spawn`, no
  `mpsc::channel(64)` allocation, no `pump_loop`, no UUID gen, no
  middleware. Mirrors Python's `child._scheduler.run(state, ctx)`
  shape. Pure-noop nested patterns are now **1.5×** Rust-faster (was
  parity); production-shape jumped from 7.8× to **11×**.
- **Rust scheduler — real `if_()` branch routing.** New ref-transform
  evaluator in `resolve_ref` covering `eq` / `ne` / `lt` / `le` / `gt`
  / `ge` / `contains` / `getitem` / `getattr` / boolean (`and_` /
  `or_` / `not_`) / arithmetic (`add` / `sub` / `mul` / `truediv` /
  `floordiv` / `mod` / `pow` and r-variants) / unary (`neg` / `pos` /
  `abs`). Truthiness matches Python. New `OpType::Branch` dispatch
  evaluates each case's condition Ref, picks the first truthy
  `target` (or `default`), emits `{"__branch_target__": "<name>"}`;
  the existing scheduler edge router fires only the matching
  `EdgeType::Condition` edge. `branching_*` is now 2.0–2.7× faster
  AND semantically correct (was firing every branch with soft-edge
  merge picking by coincidence).
- **`#[op]` macro hygiene.** `operonx` now re-exports `inventory`
  (`pub use ::inventory;`); `#[op]` and `#[resource]` macros emit
  `::operonx::inventory::submit!` instead of bare `::inventory::`.
  Consumer crates no longer need `inventory = "0.3"` as a direct dep.
- **Lazy provider exports.** `operonx/providers/__init__.py` is now
  fully `_LAZY_BACKENDS` (configs + factories + base classes + ops
  + heavy backends). The eager `from operonx.providers.auth/.../...
  import …` lines are gone. `import operonx.providers` on a tier-1
  install no longer pulls `httpx` / `openai` / `numpy`.
  `auth/factory.py` defers the `keycloak.py` import (which pulls
  `httpx`) inside `create_auth()` with a typed missing-dep
  `ImportError`.
- **`__version__` source of truth.** `operonx/__init__.py` reads
  `importlib.metadata.version("operonx")` with a
  `PackageNotFoundError` fallback to `"0.0.0+unknown"`. The
  `pyproject.toml` `version` is now the single source of truth.
- **API docs rendering.** mkdocstrings options switched to richer
  rendering: `docstring_section_style: table`,
  `members_order: source`, `group_by_category: true`,
  `show_category_heading: true`, `show_root_full_path: false`,
  `show_symbol_type_heading: true` /
  `show_symbol_type_toc: true`. Each provider op now surfaces its
  `Op.of()` classmethod; `Operon` shows all public methods
  (`run` / `start` / `use` / `batch` / etc.); state markers
  (`START` / `END` / `PARENT` / `PENDING`) are documented in a
  dedicated table.
- **Outdated runtime-parity caveats** in `examples/README.md` —
  nested `@graph` moved to "recently closed"; `if_()` bullet now
  reflects the partial-deserialise + every-branch-fires reality
  pre-this-release (now superseded by real branch routing above).

### Fixed
- `operonx/__init__.py:51` no longer hardcodes `0.6.1` — the
  long-standing drift from `pyproject.toml` is gone.
- `examples/python/{ex07,ex12}/resources.yaml` — added
  `dimensions: 1536` so the OpenAI-flavoured embedding config passes
  VLLMEmbedding's runtime validation at serialise time.
- `examples/rust/ex07_embeddings_and_rag/src/main.rs` — handles a
  missing `rerank` bundle entry gracefully (no longer panics on
  `.expect`).
- `examples/rust/ex09_agent_workflow/src/main.rs` — refactored to
  load the single `agent` graph once and run it against three
  scenario inputs.
- `docs/api/providers.md` — fixed a stale mkdocstrings reference
  (`operonx.providers.{chat,ask}` → `operonx.providers.ops.{chat,ask}`)
  surfaced by the lazy-providers refactor.

### Removed
- `tools/dump-graph.py` — replaced by `operonx-pack`. The `tools/`
  directory is gone.
- `cpu_chain_*` patterns and the `bench_hash` op from `scripts/bench/`
  — `hashlib.sha256` is OpenSSL C and Rust `sha2` is pure Rust, so
  hash-chain benches measured the hash library, not the engine.
  `matrix_chain_*` (naive O(n³) mat-mul, no library shortcut on
  either side) covers CPU-chain stress fairly. Same swap for
  `cpu_contention_*` (heavy branches now use `bench_matrix(30)`
  instead of `bench_hash`).

## [0.6.3]

Unreleased — folded into 0.7.0 above.

## [0.6.2] - 2026-04-28

### Fixed
- Publish workflow: added a `force` input on `workflow_dispatch` so a release
  can be re-run when a version-bump commit and a follow-up commit land in the
  same push (the diff-based detector otherwise sees the version as unchanged
  at `HEAD~1` and skips both publish jobs). Recovery path:
  `gh workflow run publish.yaml -f force=true`.
- README badges pinned to `?branch=main` so the shields endpoint resolves
  correctly; added a Docs badge linking to the published GitHub Pages site.

## [0.6.1] - 2026-04-28

### Added
- Repository readiness: pre-commit hooks (ruff + cargo fmt + advisory clippy;
  `-D warnings` flips on once the ~25 outstanding port-era lint debts clear),
  codecov configuration, CHANGELOG, CODE_OF_CONDUCT, public-facing docs site (mkdocs
  Material with mkdocstrings, full guide + architecture + API reference).
- `[standard]` extra — recommended production install (OpenAI + Langfuse + OTEL + serve).
- `[all]` extra now includes Anthropic, Gemini, Bedrock, ONNX, Langfuse, OTEL, serve
  (was previously missing the LLM provider extras).
- `[docs]` extra (mkdocs + mkdocs-material + mkdocstrings) for local doc development.
- `extras-smoke` CI matrix verifies each `pip install operonx[X]` works in a fresh venv.

### Changed
- All optional providers are now lazy-loaded via module-level `__getattr__`.
  Installing only `operonx[anthropic]` no longer requires numpy / onnxruntime / torch.
- Tests under `tests/internal/providers/` are auto-marked `integration` and skipped
  unless API credentials are configured.
- README, CONTRIBUTING, SECURITY, and CLAUDE.md rewritten for the single-package layout.
- `[project.urls]` in pyproject.toml fixed to point at the renamed Operonx repo.
- `env.example` corrected: stale `OPERON_TRACES_DB` replaced with `OPERON_TRACES_DIR`
  (the env var the local tracer actually reads), and the `.env` loading note updated to
  reflect the explicit `operonx.bootstrap()` model.

### Fixed
- Provider extras no longer fail at import time when their non-shared dependencies
  are missing — error surfaces only on actual backend instantiation.
- Removed leftover `_is_hush_builder` flags, `hush_current_*` ContextVar names, and
  `test_hush_*` test names from the Hush-ai migration (now `_is_operonx_builder`,
  `operonx_current_*`, `test_operon_*`).
- Stale `chain` references in CLAUDE.md, README, and docs replaced with the actual
  helper name `chat` (renamed during the original migration but missed in user-facing
  docs).

## [0.6.0] - 2026-04-26

### Added
- `operonx.bootstrap()` — explicit, idempotent setup for `.env` + `resources.yaml`.
  Replaces implicit auto-load behaviour from earlier versions.
- `ResourceHub.auto()` classmethod — discover and install a hub from CWD.
- Disambiguated error model:
  - `ResourceHubWarning` when `resources.yaml` is absent or `${VAR}` interpolations
    can't be resolved at startup.
  - `EnvVarUnsetError` (subclass of `RuntimeError`) at resolve time, naming the
    variable, source path, and `.env` paths searched.
  - `RuntimeError("ResourceHub not initialized. ...")` at engine init when a graph
    references a resource without a hub installed.
- Rust mirror of the Resource Hub refactor (`OperonError::EnvVarUnset` typed
  variant, `bootstrap_state` module, `tracing::warn!` for missing `resources.yaml`).
- Single-package Python layout (`operonx`) and single-crate Rust layout (`operonx`).
  Migrated from the previous Hush-ai four-package / six-crate split.

### Changed
- `Operon(graph)` no longer auto-loads `.env` or `resources.yaml`. It is a pure
  orchestrator. Pure-compute graphs work hub-free; provider graphs require an
  explicit `bootstrap()` (or `ResourceHub.set_instance(...)`) before engine init.
- `ResourceHub.set_instance(hub)` is authoritative — `bootstrap()` and `auto()`
  respect a pre-installed hub and are idempotent.
- Repository renamed from `Operon` to `Operonx` (PyPI/crates.io name conflict
  with an unrelated project under the shorter name).

### Removed
- Implicit `.env` / `resources.yaml` loading from `Operon.__init__`.
- `Operon(graph, resources=...)` keyword argument — use `bootstrap(resources=...)`
  before constructing the engine.

[Unreleased]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.17.4...HEAD
[1.17.5]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.17.4...v1.17.5
[1.17.4]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.17.3...v1.17.4
[1.17.3]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.17.2...v1.17.3
[1.17.2]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.17.1...v1.17.2
[1.17.1]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.17.0...v1.17.1
[1.17.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.16.0...v1.17.0
[1.16.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.15.0...v1.16.0
[1.15.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.14.0...v1.15.0
[1.14.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.13.0...v1.14.0
[1.13.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.12.2...v1.13.0
[1.12.2]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.12.1...v1.12.2
[1.12.1]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.12.0...v1.12.1
[1.12.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.11.1...v1.12.0
[1.11.1]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.11.0...v1.11.1
[1.11.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.10.3...v1.11.0
[1.10.3]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.10.2...v1.10.3
[1.10.2]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.10.1...v1.10.2
[1.10.1]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.10.0...v1.10.1
[1.10.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.9.0...v1.10.0
[1.9.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.8.1...v1.9.0
[1.8.1]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.8.0...v1.8.1
[1.8.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.7.3...v1.8.0
[1.7.3]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.7.2...v1.7.3
[1.7.2]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.7.1...v1.7.2
[1.7.1]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.5.2...v1.7.0
[1.5.2]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.5.0...v1.5.2
[1.5.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.3.1...v1.4.0
[1.3.1]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.7.0...v1.0.0
[0.7.0]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.6.2...v0.7.0
[0.6.2]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/batman1m2001-cyber/Operonx/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/batman1m2001-cyber/Operonx/releases/tag/v0.6.0
