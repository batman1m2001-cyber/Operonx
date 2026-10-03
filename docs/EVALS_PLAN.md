# Evals — experiments with an identity, repeats, error bars and a gate

Status: **E0 committed 2026-10-04; E1 built on `feat/evals-e1`; E2–E3 on `feat/evals-e2`.**
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
| D16 | Baseline (E1) | `"latest"` (the eval's last finished run in its `record_dir`, fixed when this run starts) or a run id there. `"main"` and `"git:<ref>"` need the ScoreStore and raise a clear error until E3 | runs exist locally today; nothing invented |
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

## 7. Log

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
