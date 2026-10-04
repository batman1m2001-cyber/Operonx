# Evals — experiments with an identity, repeats, error bars and a gate

Status: **E0 committed 2026-10-04; E1 built on `feat/evals-e1`; E2–E3 on `feat/evals-e2`; E4 on `feat/evals-e4`.**
Source: `docs/roadmap/ROADMAP.md` §3 and the full design in `docs/roadmap/track4_eval.md`
(cited below as T4 §n). This file is the working plan: it keeps what T4 decided, resolves
what T4 left to the implementer, and says what each phase ships and how it is tested.

## 1. Where we start (1.14.0, read not assumed)

`operonx/app/evals.py` (526 lines) is the whole feature: `Dataset` (JSONL, ids from the row
or `sha1(input)[:12]`), `verdict_of`, five built-in evaluators plus `llm_judge`, and
`Eval(Job)`, whose `judge()` runs evaluators per case and whose `summarize()` writes
`run.json["eval"]` and fails the run when a case fails or `pass_rate < threshold`.
`operonx run <eval>` exits 1 on a failed run. `tests/internal/app/test_evals.py` has 11
tests. Missing, and what this plan adds: no experiment identity (T4 G3), no repeats (G4),
no uncertainty (G5), one gate state.

Measured before any change (`scripts/bench_eval_overhead.py`, 300 cases, concurrency 4,
median of 9 rounds, one `exact` check, no judge): a plain job **482–499 µs/case**, the eval
**627–657 µs/case**, so the eval's own bookkeeping is **+145 to +158 µs/case** (two runs).
E1's gate: no measurable change to that at `repeats=1`.

## 2. Phases

| Phase | Ships | Tests | Gate before the next |
|---|---|---|---|
| **E0** | this plan | — | committed before code |
| **E1** | `evals` package, fingerprint, `repeats`, `stats.py`, `Gate` (3 states + must-pass + infra), exit codes, guide page | §4 | A/A ≤ 5 % REGRESSED; −10 pt on 300 cases ≥ 80 % REGRESSED; the 11 existing tests unchanged; overhead measured |
| E2 | `TraceView`, trajectory / tool / op-output / budget evaluators, `rescore` | T4 §18 P2 | `from_trace(live) == from_rows(stored)` |
| E3 | `ScoreStore` (files+SQLite, ClickHouse v3) | T4 §18 P3 | host A's experiment visible on host B |
| E4 | `operonx eval` CLI, md/json/junit reports, pytest plugin, `calibrate`, `power` | T4 §18 P4 | exit-code matrix through the CLI |
| E5–E8 | judges as traced graphs, Studio, online eval, conversations | T4 §18 P5–P8 | as T4 |

The roadmap's E0 measurement ("callbot QC cases 3× on one sha → flip rate") runs on the
callbot side (`refactor/operonx-studio`), not in this repo. It does not block E1: E1's
defaults are chosen so nothing changes until a user opts in (§3, D3), and the measured flip
rate becomes the *recommended* `repeats`/`tolerance` in the E4 `calibrate` docs.

## 3. Decisions (E1)

| # | Question | Decision | Why |
|---|---|---|---|
| D1 | Package layout | `operonx/app/evals/`: `dataset.py`, `evaluators.py` (verdicts, built-ins, `llm_judge`), `job.py` (`Eval`), `fingerprint.py`, `stats.py`, `gate.py`; `__init__` re-exports every 1.14.0 name (`__all__` plus `CASE_KEYS`, `case_id`) | T4 §16; every `from operonx.app.evals import …` keeps working |
| D2 | Fingerprint | Stored at `run.json["eval"]["fingerprint"]`: `code_version` + `version_dirty` (`origin.code_version` at the eval's root: the manifest's directory, else the cwd), `graph_hash`, `config_hash`, `dataset_version`, `evaluators` (name → version) + `evaluators_hash`, `operonx_version`. Computed once per run, at summary time — never per case | T4 §5.2 |
| D3 | `graph_hash` | sha256 (12 hex) of the canonical JSON of `GraphOp.serialize()` with resolved resource configs dropped (they are `config_hash`'s), the root's name made relative (the root graph is named after whatever variable held the engine), and each op's `python_callable` replaced by a digest of its source. A graph that cannot serialize (a synthetic loop) records `graph_hash: null` and `graph_hash_error` with the reason | Topology, literals and inline prompts — and op bodies, so a dirty tree still shows the change |
| D4 | `config_hash` | sha256 of the resolved resource configs the graph's ops carry (`resource_config`, `resource_configs`, `fallback_configs` in `serialize()`), with secret-named keys dropped (`*_key`, `*_secret`, `*_password`, `token`, `password`, `authorization`, `private_key`, …) and URLs reduced to scheme://host/path (no user, password or query). A rotated key does not change it; a model or temperature change does | T4 §5.2 "secrets scrubbed" |
| D5 | `dataset_version` / `case_hash` | sha256 of the canonical JSON of the cases sorted by id; `case_hash` = sha256 of `{input, expected}` per case, on every item's verdict | T4 §5.1 |
| D6 | Evaluator version | an evaluator's `version` attribute when set, else sha256 of its source plus the values its closure holds (so `contains("a")` ≠ `contains("b")` and an `llm_judge` rubric edit is a new version); an `@op` is versioned by its body | T4 §7.1 |
| D7 | `repeats=N` | Each case runs N times as N job items. Item key: the case id when `N == 1` (unchanged), `"<id>#<r>"` for r = 0…N−1 otherwise; the verdict carries `case`, `repeat`, `case_hash`. Items are yielded repeat-major (every case once, then again) so a time-correlated outage spreads over cases | resume and traces work per trial with no new runner |
| D8 | Counts under repeats | `cases` = distinct cases; `trials` = items; `passed`/`failed`/`errored` count trials; `pass_rate` = mean over cases of the case's pass share (= `passed/trials` when every case ran N times). At `repeats=1` every number is what 1.14.0 wrote | one unit per field, no field changes meaning at N = 1 |
| D9 | Flakiness | Per case: `stable_pass` (every repeat passed), `stable_fail` (none), `flaky` (some); `reliability` block with the three counts, the flaky case ids, and `pass^k` for k = 1…N (unbiased `C(c,k)/C(n,k)` averaged over cases) | T4 §8.1, τ-bench |
| D10 | Metrics | `metrics[name] = {n, mean, se, ci_lo, ci_hi, method}` for `pass` (every check) and each check, on per-case pass shares. Method: `wilson` (binary, unclustered), `clt` (repeats > 1), `clustered` (cases carry a cluster). Numeric scores stay on verdicts; scores as metrics come with the ScoreStore (E3) | T4 §8.1 |
| D11 | Clusters | `Eval(cluster="field")` names the case field; without it a case's own `cluster` key is used; a case with neither is its own cluster | T4 §5.1 |
| D12 | Paired comparison | On the case-id intersection minus cases whose `case_hash` changed. Binary unclustered metric: exact McNemar (two-sided) and the Newcombe (1998, method 10) interval from the 2×2 table. Otherwise (shares over repeats, clustered cases): paired bootstrap over cases or whole clusters, `B = 2000`, percentile CI and p, seeded from sha256 of the baseline id, the metric and the paired data (so a re-read of the same records reproduces the numbers); under 30 resampled units the report warns. Gated metrics are Holm-adjusted; the other checks are reported with Benjamini–Hochberg q-values as exploratory. *Changed from T4 (bootstrap CI everywhere):* measured, the bootstrap CI of 3 identical binary cases is [0, 0] and of 1 flip in 10 is [−0.3, 0.0] — it cannot see what it never resampled, so a 3-case eval would "rule out" any drop. Newcombe gives ±0.56 for the first; its coverage measured by simulation is 94–94.7% at n = 20, 60, 200 | T4 §8.2, §8.4 |
| D13 | Bootstrap engine | Resamples the *distinct* units with a multinomial draw (sequential exact binomials) when there are few of them (≤ n/4), else draws the n units directly — the same distribution either way, checked against `random.choices` in the tests. Paired differences of 0/1 or of shares over a few repeats have a handful of distinct values, so a 300-case comparison costs milliseconds instead of 0.17 s (measured: 2000 × 300 `random.choices`) | the A/A and power simulations need 1000 gates each |
| D14 | Gate | `Gate(threshold, baseline, tolerance, metrics, must_pass_tag, max_error_rate, alpha, strict, bootstrap)`. Verdicts: `pass`, `failed` (an absolute threshold missed — 1.9.0 semantics), `regressed`, `inconclusive`, `error` (infra). Per gated metric vs baseline: REGRESSED when `diff < −tolerance` and Holm-adjusted `p < alpha`; PASS when `ci_lo ≥ −tolerance`; INCONCLUSIVE otherwise. Precedence: error > failed/regressed > inconclusive > pass | T4 §8.3 |
| D15 | Tolerance default | None: a `Gate` with a `baseline` must say its `tolerance` (a number, or a dict per metric). A zero default would call almost every A/A comparison inconclusive; any other number is a guess. `calibrate` (E4) measures it | T4 §8.3 "never a guess" |
| D16 | Baseline (E1) | `"latest"` (the eval's last finished run in its `record_dir`, fixed when this run starts) or a run id there. `"main"` and `"git:<ref>"` need the ScoreStore and raise a clear error until then (E4: D40) | runs exist locally today; nothing invented |
| D17 | Must-pass tier | Cases tagged `must_pass_tag` (`"critical"` by convention). With a baseline: one that passed every repeat there and fails every repeat now → `regressed`. Without one: one that fails every repeat → `failed`. No statistics | T4 §8.3; "already failing" needs a baseline to be known |
| D18 | Infra | Error rate (items that failed or timed out, over trials) above `max_error_rate` (default 0.05), or a run that did not finish cleanly (source error, stopped) → `error`, exit 3 | "the endpoint was down" ≠ "the prompt got worse" |
| D19 | Exit codes | `run.json["eval"]["gate"]["exit_code"]`: 0 pass, 1 failed/regressed, 2 inconclusive under `strict`, 3 error; `operonx run` returns it. Inconclusive without `strict` exits 0 and the reasons say why | T4 §8.3 |
| D20 | Defaults unchanged | No `gate=` → the gate block reports what 1.9.0 decided (`pass`/`failed`, exit 0/1) and the run status is computed exactly as before; infra, must-pass and baseline only act through a `Gate` | an errored case stays exit 1, not 3, for existing evals |
| D21 | Manifest | `[[job]]` evals also read `repeats`, `cluster` and a `[job.gate]` table with the `Gate` fields | declared evals get the same surface |

Out of E1 (later phases, not stubs): ScoreStore, the `operonx eval` CLI and `--strict`
flag, reports, TraceView, judges as graphs, Studio (E2–E3 decisions: §5). `Gate(strict=True)` is the library
form of `--strict`.

## 4. E1 tests

All in `tests/internal/app/evals/` unless noted. Every numeric expectation is checked
against a second computation in the test (hand arithmetic, an exact enumeration, or a
different algorithm), never only against what the code printed.

- `test_fingerprint.py`: same code → same fingerprint across two processes; a changed
  literal prompt → `graph_hash` changes, `config_hash` not; a changed model → `config_hash`
  changes; a changed API key → nothing changes, and the key is in no hashed payload; a
  dirty tree flips `version_dirty`; an edited case changes `dataset_version` and that
  case's `case_hash` only; an edited evaluator rubric changes `evaluators_hash`.
- `test_repeats.py`: N items per case with keys `id#r`; the verdict's `case`/`repeat`;
  stable-pass / stable-fail / flaky from a deterministic flaky op; `pass^k`; `repeats=1`
  records are what 1.14.0 wrote.
- `test_stats.py`: Wilson 45/50 → [0.786, 0.957] (and a bisection solve of the score
  equation); McNemar b=8, c=1 → 0.0390625 (= 20/512 by hand, and by enumerating sign
  patterns); Newcombe by hand and by simulated coverage; pass^3 with 4/5 → 0.4 (and
  by enumerating subsets); the seeded bootstrap is deterministic and its SE matches the
  analytic SE; the multinomial resampler matches brute-force case resampling in
  distribution; clustered SE ≥ naive under intra-cluster correlation (and a hand-computed
  4-case example); Holm and BH against hand-worked tables.
- `test_gate.py`: A/A — 1000 synthetic paired runs with no effect → REGRESSED ≤ 5 %; power —
  a true −10 pt drop on 300 cases → REGRESSED ≥ 80 %, and within a few points of the normal
  approximation of T4 §8.5; the fractional (repeats) path holds the same A/A bound; each
  verdict and exit code; must-pass; infra; `threshold` alone reproduces 1.9.0; an eval
  against its `latest` baseline end to end.
- `tests/internal/app/test_evals.py`: unchanged, green.
- `operonx/guide/07-evals.md`: an eval with repeats and a gate, run in `tests/guide/`.

## 5. Decisions (E2, E3)

Branch `feat/evals-e2`, stacked on `feat/evals-e1`. Same rules as E1: what T4 decided is
kept; what it left open is decided here before the code.

| # | Question | Decision | Why |
|---|---|---|---|
| D22 | Getting the trace to evaluators | `ItemResult.trace`: a transient field (never in `as_dict()`, never on disk) that `runner._attempt` sets to the settled live trace of every item that ran — failed ones included; a timed-out item has none. The runner clears it after the job's `judge` hook, so a run holds at most `concurrency` traces | T4 §7.2 plumbing; nothing new is kept once the case is judged |
| D23 | Who pays for the trace | `Eval` reads each evaluator's signature once, at construction. A `trace` view is built per case only when some evaluator names `trace` or takes `**kwargs`, and the view builds its rows on first read. An eval whose evaluators do not ask pays nothing (measured, §7) | T4 P2 gate: "evaluators that don't request `trace` add ~0 ms" |
| D24 | `TraceView` rows | The row every run store keeps (`rows_of_trace`). `from_trace` builds them with a plain `Consumer` and the JSON round trip the files and sqlite stores apply (`default=str`), so a live view equals a stored one value for value; `from_rows(rows, meta)`, `from_record(RunRecord)`, `from_store(store, trace_id)` read stored ones. Executions are ordered by start time, ties in stored order. Totals (`duration_ms`, `cost_usd`, `unpriced`, `tokens_in/out`, `llm_calls`, `errors`) are `summarize()` over the same rows, so a view's numbers are the run store's numbers. A value above a store's media threshold is a reference in the stored view and bytes in the live one: by design, and the golden test uses a graph without media | T4 §7.2: "offline and online evaluators read exactly the same shape" |
| D25 | Helpers | `ops(name, *, type, under, status)`, `first`, `last`, `path(*, types, collapse)`, `llm_calls()`, `tool_calls()`, `errors()`. `path()` leaves out routing and containers (`branch`, `graph` executions) unless `types` names them; `under` matches any enclosing subgraph name (the root's name is the engine variable's, so it is never matched). An LLM call is an execution whose outputs carry `cost_usd` — the rule `summarize` counts `llm_calls` by (a streaming LLM's token frames do not carry it). `tool_calls()` reads every LLM call's `tool_calls` in order, flat (`name`/`args`) or OpenAI (`function.name`/`function.arguments`, JSON text) shape, and attaches the tool message a dispatch op returned for the same call id. `turns()` (conversations) stays with E8 | T4 §7.2 minus `turns` |
| D26 | Trajectory modes | `trajectory.ops(reference, mode)` over `trace.path()`, `trajectory.tool_calls(reference, mode, args)` over `trace.tool_calls()`. AgentEvals' four modes: `strict` same calls in the same order; `unordered` same calls, any order; `subset` every actual call matches a distinct reference call (nothing beyond the reference); `superset` every reference call matches a distinct actual call (at least the reference). Matching is a maximum bipartite matching, so a loose reference entry cannot be used up by the wrong call. `args`: `exact` equal dicts, `subset` every argument the reference gives is in the call with an equal value (extra arguments allowed), `ignore` names only. Score: the share of reference calls matched (`subset`: of actual calls). The reference is the argument, else the case's `trajectory.ops` / `trajectory.tool_calls`; neither is an error on the case, never a silent pass | T4 §7.3 |
| D27 | `op_output`, `budget` | `trajectory.op_output(op, check, at="last")` runs any evaluator with `output` = that op's outputs (`at="first"` for the first execution) and puts the op's `op_id` on the verdict (`op`, the blame); an op that never ran fails. `budget(ms, cost_usd, tokens, llm_calls)`: limits inclusive, over the view's totals (`ms` is the graph run's duration); a cost limit over a run with unpriced calls fails, since the cost is unknown | T4 §7.3; "unpriced is unknown, not free" is the run store's rule |
| D28 | Concurrency per case | Sync evaluators run inline (no task); the awaitables of async ones are gathered. Check order on the verdict is evaluator order; a check's `ms` is its own start to finish | T4 §7.1 G7, without a task per check for microsecond checks |
| D29 | `rescore` | `rescore(run, evaluators, *, store=None, dataset=None, scores=None)` re-judges a recorded eval run without running its graph; `Eval.rescore(run_id, evaluators=None, …)` uses the eval's own evaluators and dataset. Per item: the case row from the dataset when its `case_hash` still matches the record (else the item errors: the case changed), the recorded output (an item whose output was clipped in the record — now marked `output_clipped` — errors), `outputs` from `sent`, and `trace` from `store.get_run(trace_id)` only when an evaluator asks. Items that did not run cleanly keep their recorded verdict. A judge (`llm_judge`, marked `eval_kind = "judge"`) is refused: rescoring re-runs deterministic evaluators. The result (`Rescored`: per-item verdicts and the summary numbers) writes no job record; with `scores=` its scores go to the ScoreStore with rescore ids (D31) | T4 P2; "Inspect `score`", LangSmith backtesting |
| D30 | ScoreStore contract | `operonx/telemetry/scores/`: `put_experiment` (upsert), `put_items`, `list_experiments(where, limit, cursor)`, `get_experiment` (with items), `put_scores` (idempotent by `score_id`), `scores(where, limit)`, `score_series(where, bucket_s)`, `cache_get`/`cache_put` (the judge cache E5 uses), `close`. Synchronous, like `RunStore`. Data: `Experiment`, `ExperimentItem`, `Score`, `ExperimentRecord`, `ExperimentFilter`, `ScoreFilter`, `ExperimentPage`, `Bucket` | T4 §6.1, all of it, so E5 and E7 need no second contract |
| D31 | Score ids | `Score` fills `score_id` from its own fields: `pair` target → sha(experiment, pair experiment, case, score name); `human` source → sha(target ids, score name, author); `item` target → sha(experiment, case, repeat, score name); `trace`/`op`/`session` targets (online, rescore) → sha(trace, op, session, score name, evaluator version). Each target checks the ids it needs and says which is missing. An offline score's `created_at` is its experiment's start, so the same verdict published twice is the same row | T4 §6.3 |
| D32 | Files + SQLite (default backend) | `files`: JSONL is the truth — `experiments.jsonl`, `items/<experiment>.jsonl`, `scores/YYYY-MM.jsonl` (by `created_at`) under `<runs root>/scores` — and `.index.sqlite` beside them is the index every read uses. Each line carries `written_at`; the index keeps, per id, the row written last, whatever order lines are read in. `refresh()` reads what other processes appended since the offset it recorded per file. The judge cache lives in the index only (losing it costs money, not truth). `sqlite`: the same index alone, one file. Own index file, not the run store's: each is rebuilt from its own files | T4 §6.2, with the experiment in the store as well as in the job record, so one contract answers every backend |
| D33 | ClickHouse v3 | T4 §6.3's four tables appended to the run store's `MIGRATIONS` as version 3: one chain, one `schema_version`, one database. `ClickHouseScoreStore` connects and migrates through the same code as the run store, so whichever opens first brings the database to v3, and a user granted only tables in an existing database never runs `CREATE DATABASE`. Experiments and items read `FINAL`; scores are deduplicated on read by `score_id`, latest `written_at` (`LIMIT 1 BY`), because their ORDER BY holds `created_at` and a re-written score (a human edit) can sit in another partition, which no merge collapses. Online scores (those with a `rule`) expire after `online_ttl_days` (365); eval and human scores never do | T4 §6.3, plus the read-side dedupe its ORDER BY needs |
| D34 | Eval → ScoreStore | `Eval(scores=…)`: a ScoreStore, a `"score_store:<name>"` key, or a spec mapping; `[[job]]` evals read `scores`. Unset (the default) writes nothing — 1.14.0's behaviour and cost. Writes go through a `BackgroundWriter`: the experiment row when the run starts (`running`), each item and its check scores as the case is judged, the final experiment from the finished record. The run then waits for the writes up to `scores_timeout` (10 s); what could not be written is counted and logged. The job record is written first and always, so an outage loses no verdict, and `publish(run, store)` sends a recorded experiment again (or for the first time) through the same converters — the live path and `publish` produce the same rows | T4 §6.3 "the local job record stays the fallback truth" |
| D35 | Item cost | A verdict carries the case run's own `cost_usd` (`None` when nothing was priced, absent with no LLM call) and `tokens_in`/`tokens_out`, read from the trace nodes; the experiment's `cost_usd` and `p95_ms` come from the items | T4 §5.1 `ExperimentItem` / `Experiment` columns |

Out of E2–E3 (later phases, not stubs): `turns()`, `no_errors`/`max_steps`, the `@evaluator`
decorator and `Verdict` class, judges as traced graphs and the judge cache's use (E5),
`open_score_store(project)` resolution from `[tracing]` and `[evals]` (E4), Studio reads (E6),
`score_series` consumers and online rules (E7).

## 6. E2–E3 tests

- `tests/internal/app/evals/test_traceview.py`: **golden** — one graph with a subgraph, a
  branch and a real `LLMOp` against a local stand-in model (tool calls, usage, prices);
  `from_trace(live) == from_rows(store.get_run(id).nodes, meta)` for the files and sqlite
  stores; helpers and totals against hand counts and against the store's `RunSummary`.
- `test_trajectory.py`: table tests of the four modes on ops and tool calls (each mode's
  pass and fail rows, duplicates, the matching case greedy gets wrong), args exact /
  subset / ignore, missing reference, `op_output` blame, `budget` limits and unknown cost.
- `test_judging.py`: an evaluator asking for `trace` gets one, one that does not costs
  nothing (no view built), `**kwargs` gets a lazy one; the record never holds the trace;
  async evaluators run concurrently (wall time), check order kept.
- `test_rescore.py`: identical verdicts from the record (`ms` aside); no graph run (a
  counter, and no new trace in the store); a changed case and a clipped output error;
  a judge is refused; trace evaluators read the stored run.
- `tests/internal/telemetry/test_score_store.py`: the contract over `files`, `sqlite` and
  (live) `clickhouse`; idempotent ids (a re-put collapses, a human edit replaces);
  files refresh picks up another writer's lines; the v3 migration offline (fake client:
  a v2 database gets only the four tables, no `CREATE DATABASE`) and live (a v2 database
  with a run in it upgrades, the run stays).
- `tests/internal/app/evals/test_eval_scores.py`: an eval writes its experiment, items and
  scores; the rows equal `publish(record)`; a store that raises on every write loses no
  verdict (record intact, gate unchanged, drops counted) and `publish` afterwards fills it.
- Guide: `07-evals.md` gains trajectory, budget, rescore and a score store.
- Measured: evaluator overhead with and without `trace` (`scripts/bench_eval_overhead.py`);
  50 cases × 3 async fake judges, sequential vs gathered.

## 7. Decisions (E4)

Branch `feat/evals-e4`, stacked on `feat/evals-e2`. Same rules: what T4 decided is kept;
what it left open is decided here before the code.

| # | Question | Decision | Why |
|---|---|---|---|
| D36 | CLI shape | `operonx eval` is a delegated subcommand (`operonx/cli/eval.py`, its own `main(argv)`, one parser with a subparser per command): `run`, `compare`, `report`, `rescore`, `calibrate`, `power`, `list`, `dataset validate\|stats\|diff`. Every command takes `-f/--manifest`; an eval is named as `operonx run` names a job (a declared name, or `module:attr`). `align`, `online`, `import`, `migrate-reviews`, `dataset from-runs` and `--no-cache` arrive with what they drive (judges E5, online and queues E7) | T4 §12; one parser per command (`cli/main.py`) |
| D37 | Exit codes | `run`: the gate's — 0 pass (and inconclusive), 1 failed or regressed, 2 inconclusive under `--strict`, 3 an infrastructure error. `compare` with a `--tolerance`: the same, from its gated metrics; without one it only reports, 0. A command that cannot run as asked (unknown eval, bad flag, no baseline at the merge-base, a store that cannot be opened) exits 2 with `error: …` on stderr — argparse's convention and `operonx run`'s already. Under `--strict` both 2s mean "not shown to be good, and a retry will not help"; the message says which | T4 §8.3; 3 stays "retry the job" |
| D38 | Which score store a project uses | `project_score_store(root)` (`operonx/telemetry/scores/project.py`), read from the project's files like `project_stores`, never by importing its code: (1) `[evals] scores = "score_store:<n>"` in `operonx.toml` → that `resources.yaml` entry; (2) else the first `trace_clickhouse:` sink (or `run_store:` with `backend = "clickhouse"`) in the project-wide `[tracing] sinks` → a ClickHouse score store on the same connection (the runs' database, schema v3); (3) else `files` under `<runs root>/scores`, where `local` keeps runs. ClickHouse wins over `local` when both are listed: an experiment is compared across machines (an MR's CI against main's), and a project that traces to ClickHouse has said where shared data lives. `[evals]` has one key, `scores`; any other key raises naming it (a typo must not quietly mean "local") | T4 §6.1, with the T4 key `store` spelled `scores` like `[[job]] scores` and `Eval(scores=)` |
| D39 | CLI runs are stored | `operonx eval run` and `calibrate` write each experiment to the project's score store unless the eval sets its own `scores` or `--no-store` is given. A store that cannot be opened (an unset `${CLICKHOUSE_HOST}`) is an error naming the variable, not a silent skip. The library `Eval` keeps D34 (unset writes nothing) | an MR can only find main's experiment if main's run was written somewhere shared |
| D40 | `baseline="main"` / `"git:<ref>"` | `"main"` is `"git:origin/main"` (a repo whose default branch has another name says `git:origin/<branch>`). The baseline is the experiment of `git merge-base HEAD <ref>` (12 hex, as `code_version`), asked at the eval's root, read from the eval's `scores` store, else the project's. Candidates: this eval's experiments at that commit that finished (`ended_at` set), are not `error`, and were run on a clean tree (`version_dirty` false: a dirty run is not that commit's code). The one with this run's `dataset_version` and `evaluators_hash` wins, newest first; else the newest candidate, and the gate's existing warnings say what differs and that it compared the shared, unchanged cases. None → `ValueError` before the record opens (no run, no cost) naming the ref, the sha, the store and the fix (run the eval on main: a scheduled main pipeline keeps baselines warm). A merge-base git cannot compute (a shallow clone, an unfetched ref) is the same error with `git fetch` advice. The comparison records `baseline_ref` (`git:origin/main @ <sha>`) | T4 §14 |
| D41 | One experiment, two places | `operonx.app.evals.experiments.load_experiment(ref, store=, record_dirs=)`: a record directory, or an id looked up in the record dirs first (they hold expected values and full verdicts), then the store. From the store, a trial is an item plus its item scores (`passed` per check). Either way the result (`ExperimentData`: id, eval, summary in `run.json["eval"]`'s shape, trials, items) feeds the same reports, `compare`, `calibrate` and `power` | experiments run in CI exist only in the store; local ones in both |
| D42 | Reports | `report.py`: **markdown** (an MR comment: the verdict and exit code first, reasons and warnings, a metric table with CIs, the comparison table — baseline, this run, diff, CI, p, verdict — flips by class, up to 10 failing cases with their failed checks and output snippet, flaky cases, cost and latency); **json** (`ExperimentData.as_dict()`); **JUnit XML**: one `<testsuite name="eval:<name>">`; a `gate` testcase first (`<failure>` for failed/regressed, `<error>` for error, `<skipped>` for inconclusive, a failure under strict); then one testcase per case × check (`classname` `<eval>.<case>`, `name` the check), a `<failure>` unless every repeat passed (`k/n repeats passed`); a case whose trials errored gets a `run` testcase with `<error>`; an eval with no checks gets one `pass` testcase per case. Validated against the Jenkins xunit `junit-10.xsd` (vendored in the tests, MIT) — the shape GitLab's JUnit widget reads; GitHub has no native widget and renders it through a JUnit report action, which the CI docs show | T4 §14 |
| D43 | `compare A B` | Any two experiments (`load_experiment`), compared by the gate's `compare_runs` with A as the baseline. `--tolerance` optional: without it every metric gets its diff, CI, p (Holm for `--metrics`, BH for the rest) and no verdict; `compare_runs` now leaves a gated metric without a tolerance unjudged instead of raising | exploration needs no gate; a decision does |
| D44 | `calibrate` | Runs the eval `--runs k` times on this commit (default 3) — or reads `--experiments a,b,c` — and measures the A/A noise: per metric, the run-to-run SD of the means; per case, whether it flipped; the flaky share; the flip rate (P(two trials of a case disagree), `2·c(m−c)/(m(m−1))` averaged). Model: each case passes with its own p_i = c_i/m_i over its k·r trials (a case that never flipped is simulated as deterministic — more runs see rarer flakes). The tolerance is measured **through the gate**: 200 seeded synthetic A/A pairs drawn from the p_i (same clusters) are compared by `compare_runs`, and a gated metric passes exactly when `ci_lo ≥ −tolerance`, so the tolerance 95% of A/A runs pass at is the 95th percentile of `−ci_lo` (rounded up to 0.1 pt). Tabled for r = 1, 2, 3, 5 and the configured repeats. *Changed while building:* the closed form first written here, `(z_{1−α/2}+z_{0.95})·√(2·mean v_i/(n·r))`, counts only the flakes; the gate's interval also carries the case-sampling width (40 cases that always pass cannot rule out an 8.8-point drop — `newcombe_paired(40,0,0,0)`), so the formula promised tolerances the gate never passes. Recommended: the fewest repeats whose tolerance ≤ the target (`--tolerance`, else the eval's gate tolerance), with the A/A pass share at the target per row; none → "too noisy to gate at this size", and `power` says how many cases. Printed, and with `--out` written as `calibration.json`; not read back by a `tolerance="calibrated"` — the number goes into `Gate(tolerance=)`, where the MR reviews it | T4 §8.5, "never a guess" |
| D45 | `power` | `n ≈ (z_{1−α/2}·√p_d + z_{power}·√(p_d − δ²))² / δ²` (T4 §8.5) for a paired binary metric, and the inverse — the smallest drop the dataset's n detects (bisection). p_d: `--discordance`, else measured from the eval's two newest finished experiments as the share of shared cases whose `pass` differs (mean \|s_B − s_A\| for shares over repeats). Fewer than two → an error asking for `--discordance` or a second run. Checked in the tests by simulating McNemar at the computed n | T4 §8.5 |
| D46 | pytest plugin | `operonx/app/evals/pytest_plugin.py`, never in `pytest11`; on with `-p operonx.app.evals.pytest_plugin` or `pytest_plugins = [...]`. The **session is one experiment** (name `--operonx-eval-name`, default `pytest`; record under `--operonx-eval-dir`, default `<rootdir>/evals`; variant `pytest`), opened by the first test that uses it. `run_case(graph, case, evaluators=…, item_input=…, inputs=…)` (async fixture-returned callable) runs the graph once, traced with `origin=eval`, through the job runner's own per-item path, judges the case and returns a `CaseRun` (`output`, `outputs`, `trace`, `checks`, `passed`, `why`, sync `check(ev)` for one more check). One test is one item keyed by its node id; a second `run_case` in one test raises (parametrize instead). **The verdict is the test's outcome**: a test whose body passed but whose checks failed is reported failed with `why`; a test whose body failed records an `assert` check with the message. At session end the record is finished with the eval's own `summarize` (the gate-less rule, or `--operonx-eval-baseline`/`--operonx-eval-tolerance`/`--operonx-eval-strict`), the terminal summary prints the verdict and the record's path, `--operonx-eval-report md,json,junit` with `--operonx-eval-out` writes the reports, a non-zero gate makes a passing session exit 1, and `--operonx-eval-store` writes to the project's score store. `cases(dataset, split=, tags=)` gives `pytest.param`s with the case ids. T4's `@pytest.mark.operonx_eval` (gating a declared eval) is not added: a plain test calling `Eval(...).run_sync()` does it, and `operonx eval run` is CI's | T4 §13 |
| D47 | Dataset selection and checks | `Dataset.select(split=, tags=, ids=, sample=)` returns a dataset view: `split` matches the case field, `tags` any of them, `ids` exactly (an unknown id is an error), `sample=N` the N cases with the smallest `sha256(id)` (stable across runs and machines). The selection is the experiment's dataset, so its `dataset_version` is of the selected cases; `run.json["eval"]` records the selection. `Dataset.problems()` lists what `validate` reports (a line that is not JSON, a duplicate id, `tags` not a list of strings, `split`/`cluster` not a string, a `trajectory` that is not `{ops: [...], tool_calls: [...]}`), each with its line; `operonx eval dataset diff <name> [--against REF]` compares case by case with the file at a git ref (added, removed, changed `case_hash`) | T4 §12 `--split/--tag/--cases/--sample`, `dataset validate/diff/stats` |
| D48 | Variant and split on the experiment | `Eval(variant="…")` and the selection's `split` land in `run.json["eval"]` and on the `Experiment` row (`variant`, `split`) | T4 §5.1 |

## 8. E4 tests

- `tests/internal/telemetry/test_project_score_store.py`: each resolution rule and its
  precedence, `${VAR}` from `.env`, an unknown `[evals]` key, an unresolvable sink.
- `tests/internal/app/evals/test_baseline_git.py`: a real git repo with main and a branch;
  main's experiment is found through the files store at the merge-base; dirty, errored and
  other evals' experiments are not; dataset-version preference; none → the error, raised
  before any case runs (a counter); a shallow/unknown ref → the git error.
- `test_reports.py`: markdown sections against a hand-built experiment; JSON round trip;
  JUnit validated with `xmlschema` against `junit-10.xsd`, counts checked by hand, flaky,
  errored, no-check and gate testcases; the same report from a record and from the store.
- `test_calibrate.py`: a deterministic eval's tolerance is exactly the Newcombe interval's
  lower end; the flip rate by hand; the suggested tolerance passes ≥ 92% of fresh A/A pairs
  (another seed) judged by the gate itself, and half of it < 80%; the recommendation and
  "too noisy"; `power` gives 312 for p_d = 0.10, δ = 0.05 (and the hand formula), McNemar at
  that n detects the drop 74–84% of the time (simulation); the inverse round-trips;
  `compare` with and without a tolerance against McNemar and Newcombe by hand.
- `test_dataset_select.py`: selection rules, versions of selections, problems, diff.
- `tests/internal/cli/test_eval_cli.py`: the exit-code matrix through `operonx eval run` —
  0 pass, 1 failed, 1 regressed (a baseline), 2 inconclusive under `--strict`, 3 error (a
  graph that raises), 2 usage — plus `compare`, `report`, `rescore`, `calibrate`, `power`,
  `list`, `dataset`, reports written by `--report`, the experiment in the store, the
  `--baseline main` path end to end in a git repo.
- `tests/internal/app/evals/test_pytest_plugin.py` (`pytester`): not loaded without `-p`;
  session = one experiment with one item per test; parametrised cases; a failing check fails
  its test; a failing assert is recorded; reports written; gate exit status; second
  `run_case` refused.
- Guide: `07-evals.md` gains the CLI (`bash run` snippets), reports and the pytest plugin;
  `docs/guide/13-evals.md` gains the CLI, CI for GitLab and GitHub, and the plugin.

## 9. Log

**E1 built, 2026-10-04** (`feat/evals-e1`).

- Gate simulations (`test_gate.py`, Beta(4, 1) case difficulty, tolerance 2 pts): A/A over
  1000 paired 300-case runs → 2.8% `regressed` (exact rate under the model 1.9%); a true
  −10 pt drop on 300 cases → 82.1% `regressed` (exact 83.6%); A/A with `repeats=3` on the
  bootstrap path → 2.75% `regressed` (and 83% `inconclusive`: a 2-pt tolerance on 100 cases
  is below the noise, which is what `calibrate` (E4) is for).
- Overhead (`scripts/bench_eval_overhead.py`, CPU time, 300 and 1200 cases, interleaved
  runs of the old and new code on the same machine): per case over a plain job, before
  **+151 / +163 µs**, after **+153 / +159 µs** (with a `Gate`: +144 / +148) — no measurable
  change. Per run: about +5 to +15 ms (serialize, source and dataset hashes, the summary
  statistics); git (~30 ms) is asked once per process and root, in the background.
- Found on the way: `ItemResult.as_dict()` deep-copied every verdict (`asdict`), most of
  the per-item record cost; the extra verdict fields made that visible (+60 µs/case) until
  it became a shallow copy.

**E2 built, 2026-10-04** (`feat/evals-e2`).

- Golden: `from_trace(live) == from_rows(store.get_run(id).nodes)` on the files and sqlite
  stores, for a graph with a subgraph, a branch and a real `LLMOp` against a local stand-in
  model with a tool call and a price (`test_traceview.py`).
- Overhead (`scripts/bench_eval_overhead.py`, CPU time, interleaved runs of E1 `2ddd9b0`
  and E2 on one machine): eval over a plain job **+168 / +138 µs/case** before, **+137 /
  +146** after — no measurable change; reading the signatures once (`prepare`) pays for the
  `trace` check. An eval with one check that reads the trace (a one-op graph's path):
  **+265 / +285 µs/case** over the job, so building a view costs ~120–150 µs on this graph.
- Concurrency: 50 cases × 3 async fake judges (50 ms each), concurrency 4, median of 5
  wall times: **2.06 s → 0.74 s**.
- Found on the way: `Ref.apply(fn)` made `GraphOp.serialize()` raise (an operonx-rs rule),
  so the fingerprint of any graph using it crashed; the callable now serializes as
  `{"python_callable": fn}` and is hashed by name and source.
- The rescore module is `rescoring.py`: `operonx.app.evals.rescore` is the function, and a
  module of the same name would have been shadowed by it.

**E3 built, 2026-10-04** (`feat/evals-e2`).

- Contract suite (`tests/internal/telemetry/test_score_store.py`) over `files`, `sqlite`
  and a throwaway local ClickHouse 26.9 container (`OPERONX_TEST_CLICKHOUSE`, never the team
  server): all pass, as do the run-store contract and the ClickHouse run-store tests on the
  same container after the connection moved into `ClickHouseConnection`.
- Migration: a v2 database gets exactly `experiments`, `experiment_items`, `scores`,
  `judge_cache` and no `CREATE DATABASE` (fake client); live, a v2 database holding a run
  upgrades to v3, keeps the run, and an experiment written through one client reads back
  through a second (host A → host B).
- Outage: a store that fails every write — the record holds every verdict, the gate
  decides as without a store, the run waits `scores_timeout` (1 s in the test) and logs
  the loss with the record's path; `publish` fills the store afterwards with the rows the
  live path would have written (`test_eval_scores.py`).
- Overhead with no `scores=` (interleaved E2 `81608af` vs E3, `bench_eval_overhead.py`):
  eval over job +159 / +152 µs/case before, +139 / +152 after — the per-item cost read
  (`_run_cost`) is not measurable.
- Found on the way: `clickhouse-connect` returns `''` for a `String` column written as
  `''`, so a trace score's empty `case_id` read back as `''`; score ids that a target does
  not need are `Nullable` in the table and written as `NULL`.

**E4 built, 2026-10-04** (`feat/evals-e4`).

- Exit-code matrix through `operonx eval run` (`tests/internal/cli/test_eval_cli.py`): 0
  pass, 1 failed, 1 regressed, 2 inconclusive under `--strict` (0 without), 3 every case
  raising, 2 for an unknown eval / a tolerance without a baseline / an unknown report format
  / an unopenable store / no experiment at the merge-base (nothing runs, no record opens).
  `--baseline git:main` end to end in a real git repo: refused before main stored an
  experiment, `regressed` after, the comparison naming `git:main @ <sha>`.
- JUnit: every report in the tests validates against Jenkins xunit `junit-10.xsd`
  (`xmlschema`, a dev dependency). A record and its rows in the files store give byte-identical
  Markdown and JUnit.
- `calibrate`, checked through the gate (`test_calibrate.py`): 300 cases, 30 flaky at
  p = 0.6, three runs → suggested tolerance 4.4 pts; 300 fresh A/A pairs (another seed)
  judged by `compare_runs` pass 96.3% of the time at it and 43.7% at half of it. A
  deterministic eval's tolerance is exactly `−newcombe_paired(…, 0, 0, …)[0]` (40 always-
  passing cases: 8.8 pts; the guide's 3 cases: 56.2 pts). Cost per table row at 300 cases and
  200 simulations: ~0.6 s for one repeat (McNemar), 7–11 s for 2–5 repeats (the bootstrap,
  B = 2000).
- `power`: p_d = 0.10, δ = 0.05 → 311.6 → 312 cases (T4's number, and by hand); exact
  McNemar at n = 312 detects the drop in 77% of 3000 simulated runs — the normal
  approximation is a little optimistic, as expected of an exact test.
- Overhead (`scripts/bench_eval_overhead.py`, CPU time, interleaved E3 `1cbd46e` vs E4 on a
  shared machine): eval over job **+145 / +147 / +91 µs/case** before, **+137 / +155 / +112**
  after — no measurable change (the verdict refactor into `recorded_verdict`, `p95_ms` and
  `cost_usd` in the summary).
- Found on the way: the calibrate formula first written in D44 counted only the flakes and
  promised tolerances the gate never passes (see D44); the run summary had no `p95_ms` or
  `cost_usd`, so a report read from a record and from the store differed until
  `numbers()` computed them; an unknown `--cases` id surfaced as a source error and exit 3
  (infrastructure) until the CLI checks the selection before the run.
