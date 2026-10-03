# Evals — experiments with an identity, repeats, error bars and a gate

Status: **E0 (this plan) committed 2026-10-04; E1 in progress on `feat/evals-e1`.**
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
| D12 | Paired comparison | On the case-id intersection minus cases whose `case_hash` changed. Binary unclustered metric: exact McNemar (two-sided). Otherwise: paired bootstrap (cases, or whole clusters), p from the percentile distribution. CI: the paired bootstrap percentile interval, `B = 2000`, seeded from sha256 of the two run ids. Gated metrics are Holm-adjusted; the other checks are reported with Benjamini–Hochberg flags as exploratory | T4 §8.2, §8.4 |
| D13 | Bootstrap engine | Resamples the *distinct* units with a multinomial draw (sequential exact binomials) instead of n draws per replicate. Same distribution as resampling cases; O(distinct values) per replicate, so a 300-case gate costs milliseconds, not 0.17 s per metric (measured: 2000 × 300 `random.choices` = 0.17 s) | the A/A and power simulations need 1000 gates each |
| D14 | Gate | `Gate(threshold, baseline, tolerance, metrics, must_pass_tag, max_error_rate, alpha, strict, bootstrap)`. Verdicts: `pass`, `failed` (an absolute threshold missed — 1.9.0 semantics), `regressed`, `inconclusive`, `error` (infra). Per gated metric vs baseline: REGRESSED when `diff < −tolerance` and Holm-adjusted `p < alpha`; PASS when `ci_lo ≥ −tolerance`; INCONCLUSIVE otherwise. Precedence: error > failed/regressed > inconclusive > pass | T4 §8.3 |
| D15 | Tolerance default | None: a `Gate` with a `baseline` must say its `tolerance` (a number, or a dict per metric). A zero default would call almost every A/A comparison inconclusive; any other number is a guess. `calibrate` (E4) measures it | T4 §8.3 "never a guess" |
| D16 | Baseline (E1) | `"latest"` (the eval's last finished run in its `record_dir`, fixed when this run starts) or a run id there. `"main"` and `"git:<ref>"` need the ScoreStore and raise a clear error until E3 | runs exist locally today; nothing invented |
| D17 | Must-pass tier | Cases tagged `must_pass_tag` (`"critical"` by convention). With a baseline: one that passed every repeat there and fails every repeat now → `regressed`. Without one: one that fails every repeat → `failed`. No statistics | T4 §8.3; "already failing" needs a baseline to be known |
| D18 | Infra | Error rate (items that failed or timed out, over trials) above `max_error_rate` (default 0.05), or a run that did not finish cleanly (source error, stopped) → `error`, exit 3 | "the endpoint was down" ≠ "the prompt got worse" |
| D19 | Exit codes | `run.json["eval"]["gate"]["exit_code"]`: 0 pass, 1 failed/regressed, 2 inconclusive under `strict`, 3 error; `operonx run` returns it. Inconclusive without `strict` exits 0 and the reasons say why | T4 §8.3 |
| D20 | Defaults unchanged | No `gate=` → the gate block reports what 1.9.0 decided (`pass`/`failed`, exit 0/1) and the run status is computed exactly as before; infra, must-pass and baseline only act through a `Gate` | an errored case stays exit 1, not 3, for existing evals |
| D21 | Manifest | `[[job]]` evals also read `repeats`, `cluster` and a `[job.gate]` table with the `Gate` fields | declared evals get the same surface |

Out of E1 (later phases, not stubs): ScoreStore, the `operonx eval` CLI and `--strict`
flag, reports, TraceView, judges as graphs, Studio. `Gate(strict=True)` is the library
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
  equation); McNemar b=8, c=1 → 0.0390625 (= 20/512 by hand); pass^3 with 4/5 → 0.4 (and
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
- `operonx/guide/06-evals.md`: an eval with repeats and a gate, run in `tests/guide/`.
