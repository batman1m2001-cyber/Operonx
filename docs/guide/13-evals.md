# Evals

An eval answers one question about a graph: on these cases, does it do
what it should? It is a dataset of cases, the graph under test, and
**evaluators** that judge each case's output. It runs as a
[job](10-jobs.md) — no new runtime — so it resumes, runs concurrently,
records every case, and fails `operonx run` when it should, which is what
CI needs.

```python
from operonx.app import Eval
from operonx.app.evals import contains, llm_judge

replies = Eval(
    "replies",
    graph="bot:reply_flow",
    dataset="dataset:replies",                 # datasets/replies.jsonl
    evaluators=[contains(), llm_judge("llm:judge", "Is the reply polite and correct?")],
    threshold=0.9,
)
run = await replies.run()
print(run.meta["eval"]["pass_rate"])
```

## The dataset

A JSONL file, one case per line:

```json
{"id": "c1", "input": {"question": "When is class?"}, "expected": "19:00", "tags": ["schedule"]}
```

`input` is the item the graph receives; `expected` is optional (an
evaluator that needs it says so). A line without `input` is itself the
input, so a job's data file is already a dataset of cases with no
expectations. `"dataset:name"` names `datasets/name.jsonl` — under the
project in the manifest, under the working directory in Python; a path
works too. A case saved from a recorded run carries
`"from": {"run": "<run id>"}`, so a failure leads back to where the case
came from.

A case may also say its `split` (`"smoke"`, `"test"`…), its `cluster`
(cases that move together) and a reference `trajectory`.
`Dataset(path).select(split=…, tags=[…], ids=[…], sample=N)` chooses
cases — `sample` is the same N on every machine — and an eval over a
selection is an experiment over those cases: its `dataset_version` is
theirs. `operonx eval dataset validate` checks a file line by line
(JSON, duplicate ids, field types); `diff --against main` says which
cases were added, removed or changed.

`Dataset(path).update("c1", {"expected": "19:30", "tags": ["schedule"]})`
edits one case in place — `expected`, `tags`, `split`, `cluster`,
`trajectory`, `note` or `status`; `None` removes a key — rewriting its
line and leaving every other line as it was, so the merge request shows
the edit and nothing else. An input is not edited: a different input is
a different case. `{"status": "archived"}` keeps a case in the file, so
its history across experiments stays readable, and takes it out of every
run (and of `dataset_version`); `all_rows()` still lists it.

## Evaluators

An evaluator is a function — plain, async, or an `@op` (called for its
body) — that takes any of `input`, `output`, `expected`, `row`,
`outputs` and `trace` by name, and returns a verdict:

- `True` / `False`;
- a score in [0, 1], which passes at 0.5;
- or `{"passed": …, "score": …, "reason": …}`.

`output` is what the case produced: the one item the graph sent (or its
result, for a graph with no doors), a list when it sent several, `None`
when it sent nothing. An evaluator that raises fails its check, with the
error on the verdict. A case's async evaluators run at the same time.

```python
def short_enough(output):
    return len(output or "") <= 280

async def names_the_time(output, expected):
    return {"passed": expected in output, "reason": f"looked for {expected!r}"}
```

The built-ins, each a factory:

| Helper | Passes when |
|---|---|
| `exact(field=None)` | the output (or its dotted `field`) equals `expected` |
| `contains(*needles, field=None, case=False)` | the output text contains every needle — the given ones, else `expected` (a string or a list) |
| `fuzzy(threshold=0.8, field=None)` | the output text's similarity to `expected` reaches `threshold` |
| `json_match(keys=None)` | the output object agrees with `expected` on `keys` (every key `expected` has, when none are named) |
| `judge(llm, rubric, name=…)` | a model says `PASS` for one criterion (its reason is kept) — see [Judges](#judges) |
| `llm_judge(resource, rubric)` | the 1.9.0 judge: JSON `{passed, score, reason}`; now a traced, cached `Judge` |

## Judges

`judge("llm:judge", rubric, name="polite")` is an evaluator that asks a
model whether the output meets **one** criterion and answers `PASS` or
`FAIL` with its reason (`labels=`/`pass_labels=` make it categorical; a
rubric ending `.md`/`.txt` is read from that file and names the judge). It
is an operonx graph — render the prompt → `LLMOp` (reason, then verdict)
→ decide — so:

- **Traced on its own.** Each call is its own run in the eval's trace
  sinks with `origin=eval`, `role=judge`, `judged_trace` (the case's run),
  `job_run` and `case`; the verdict's `judge_trace_id` is that run. The
  case's run holds only the system's calls, so the report shows the
  system's cost and the judges' apart, and a **Judges** table per judge
  (version, model, alignment, calls, cache hits, cost).
- **Versioned.** The rubric, examples, labels, temperature, the model the
  `llm:` resource resolves to (secrets dropped) and the judge's code.
  Changing any of them changes `evaluators_hash`.
- **Cached** in the eval's score store, keyed by the version and what the
  judge was shown: re-judging an unchanged output makes no call
  (`cached: true`, cost 0). `Eval(judge_cache=False)` or `operonx eval run
  --no-cache` asks again. `Eval(judge_concurrency=8)` bounds judge runs in
  flight.
- **Any `@graph`** passed as an evaluator is a judge the same way: its
  inputs are named `input`, `output`, `expected`, `row`, `outputs` or
  `trace_summary` (the case's run as text); it returns `{passed, reason}`.
  Judges are never rescored.
- **Self-preference.** A judge whose model the system also uses (the
  fingerprint's `models`) is warned about.

### Alignment with people

A judge that gates an eval is reported as **`UNVALIDATED JUDGE`** until
it has an alignment record for its current version with Cohen's κ ≥ 0.6.
Human labels are scores with `source="human"` on the runs (or items) the
judge scored; `operonx eval align judge:polite` (or `align` +
`record_alignment` in `operonx.app.evals.align`) pairs them and reports κ,
TPR (PASS when people say PASS), TNR (FAIL when people say FAIL),
accuracy, the 2×2 table and the disagreements, and records it on the
judge. It exits 0 aligned, 1 not, 2 when nothing could be paired. The
warning never changes the verdict.

### Pairwise

`pairwise("llm:judge", rubric, name="helpful")` asks which of two
experiments' answers is better, in both orders as two parallel branches
of one judge run. Orders that disagree make the case a `tie` marked
`inconsistent`, and the swap-inconsistency rate is reported per judge.
`await compare_pairwise(a, b, [judge])` or `operonx eval compare A B
--pairwise judges:helpful`; the preferences are stored as `pair` scores.

## Checking the run: `trace`

An evaluator that names `trace` gets a `TraceView` of the case's own run
— the rows a run store keeps, so the same evaluator reads a live run
(in an eval) and a stored one (in a rescore) identically:

| Member | What |
|---|---|
| `rows` | every execution, in start order: `OpRow(op_id, op_name, op_full_name, op_type, ctx, inputs, outputs, status, error, start, duration_ms)` |
| `ops(name, type=, under=, status=)`, `first(name)`, `last(name)` | executions by op name (or a dotted tail of the full name), type, enclosing subgraph, status |
| `path(types=None, collapse=False)` | op names in order, branch routing left out |
| `llm_calls()` | executions that report `cost_usd` — LLM calls, as the run store counts them |
| `tool_calls()` | `ToolCall(name, args, id, op_id, result, status)` for every tool call the LLM calls made, with the tool message an op returned for it |
| `errors()` | executions that did not end `ok` |
| `duration_ms`, `cost_usd`, `unpriced`, `tokens_in`, `tokens_out`, `tokens` | the run store's totals over the same rows |

Built on it:

| Helper | Passes when |
|---|---|
| `trajectory.ops(reference=None, mode="strict", types=None)` | the op path matches the reference (else the case's `trajectory.ops`) |
| `trajectory.tool_calls(reference=None, mode="strict", args="exact")` | the tool calls match (else the case's `trajectory.tool_calls`) |
| `trajectory.op_output(op, check, at="last")` | `check` passes on that op's outputs; the verdict's `op` is its `op_id` |
| `budget(ms=, cost_usd=, tokens=, llm_calls=)` | the run stays within every limit given (inclusive) |

Modes are AgentEvals': `strict` (same steps, same order), `unordered`
(same steps, any order), `subset` (nothing beyond the reference),
`superset` (at least the reference); repeats count. Tool arguments match
`exact`, `subset` (the reference's arguments are in the call, extras
allowed) or `ignore`. A case with no reference fails with an error rather
than passing. A `budget` with a cost limit fails when a call reported no
price: that cost is unknown, not zero.

An eval whose evaluators do not take `trace` pays nothing for it.

## Rescoring a recorded run

```python
again = await ev.rescore(run.run_id, [trajectory.ops(["classify", "plan"])], store=runs)
again.summary["pass_rate"], again.verdicts["order"]
```

`rescore` judges a recorded run again without running the graph: the
recorded outputs, the dataset's cases, and — for checks that read
`trace` — the runs in `store`, the run store the eval traced into. A case
edited since the run (its `case_hash` changed), an output the record
holds only clipped (`output_clipped`), or a run the store no longer has
is an error on that item. Judges are not rescored: `Eval.rescore` leaves
them out (and names them in `skipped`); `rescore(run, [llm_judge(…)])`
refuses.

## Passing and failing

Without a `threshold`, the run fails when any case fails. With one, it
fails when the pass rate is under it. `run.meta["eval"]` — and the run's
`run.json` — hold the numbers: cases, passed, failed, errored,
`pass_rate`, each check's own pass count, the median case time, and what
the judge cost. Each case's item record carries its verdict, with the
`case`, its `repeat` and its `case_hash` (input + expected).

## Experiments in a score store

```python
ev = Eval("replies", graph=..., dataset="dataset:replies", evaluators=[...],
          scores="score_store:team")        # or a ScoreStore, or {"backend": "files"}
```

With `scores=`, an eval writes to a `ScoreStore`
(`operonx.telemetry.scores`) as it runs: the **experiment** (a `running`
row at the start, the finished one at the end — status, fingerprint
columns, metrics, gate), one **item** per case × repeat (trace id,
status, time, the case run's own cost and tokens, passed, output) and one
**score** per check per item. A score is the one row type for every
judgement — code checks, judges, humans, online rules, pairwise — with a
`target` (`item`, `trace`, `op`, `session`, `pair`) and a `score_id`
derived from what it judges, so the same verdict written twice is one row
and a human's edit replaces their earlier value.

| Backend | Where |
|---|---|
| `files` (default) | JSONL under `<runs root>/scores` (`experiments.jsonl`, `items/<experiment>.jsonl`, `scores/YYYY-MM.jsonl`), an SQLite index beside them |
| `sqlite` | one file |
| `clickhouse` | the runs' database: schema version 3 of the same migration chain, so no new server, database or grant |

```yaml
# resources.yaml
score_store:
  team:
    backend: clickhouse
    host: ${CLICKHOUSE_HOST}
    user: ${CLICKHOUSE_USER}
    password: ${CLICKHOUSE_PASSWORD}
    database: operonx
```

The writes go through a background writer. The job record is written
first and always holds every verdict; a store that is slow or down costs
the run at most `scores_timeout` (10 s) at its end, and what it did not
take is logged. `publish(run, store)` sends a recorded run — again, or
for the first time — through the same converters, so it writes exactly
what the live run would have. `rescore(..., scores=store)` writes its
scores beside the experiment's own (keyed by trace and evaluator
version, never over them).

Reading: `store.list_experiments(ExperimentFilter(eval="replies"))`,
`store.get_experiment(run_id)` (with its items), `store.scores(ScoreFilter(...))`,
`store.score_series(where, bucket_s)`. Eval and human scores are kept
forever; online scores (with a `rule`) for `online_ttl_days` (365).

## A run is an experiment

Each run also records:

- **`fingerprint`** — what produced the numbers: the git commit at the
  project root and whether the tree was dirty (`code_version`,
  `version_dirty`), `graph_hash` (topology, literal params, inline
  prompts, each op's source), `config_hash` (the resolved `llm:` and other
  resources the graph uses, with keys, tokens and passwords left out),
  `dataset_version`, each evaluator's version and `evaluators_hash`, and
  `operonx_version`. Two runs with the same `dataset_version` and
  `evaluators_hash` are directly comparable; everything else that differs
  is what the comparison measures.
- **`metrics`** — `pass` (every check passed) and each check, as a mean
  with a 95% interval: Wilson for one 0/1 trial per case, the CLT for
  shares over repeats, a clustered standard error when cases share a
  scenario (`Eval(cluster="scenario")` names the case field).
- **`gate`** — the verdict and the exit code `operonx run` returns.

## Repeats: flaky is not regressed

`repeats=3` runs every case three times, as three items keyed
`<id>#0`, `<id>#1`, `<id>#2`. `reliability` says which cases are
`stable_pass`, `stable_fail` or `flaky`, and gives pass^k — the chance
that k runs of a case all pass (4 passes of 5 give pass^3 = 0.4). With
repeats, `passed` and `failed` count trials and `pass_rate` is the mean of
each case's pass share.

## The gate

```python
from operonx.app.evals import Eval, Gate, exact

replies = Eval(
    "replies",
    graph="bot:reply_flow",
    dataset="dataset:replies",
    evaluators=[exact("intent")],
    repeats=3,
    gate=Gate(
        threshold=0.9,           # the 1.9.0 floor, now per metric if you like
        baseline="latest",       # this eval's last finished run, a run id,
                                 # or "main" / "git:<ref>": the merge-base's experiment
        tolerance=0.03,          # a drop over 3 points matters (required with a baseline)
        must_pass_tag="critical",
        max_error_rate=0.05,
    ),
)
```

Against the baseline the comparison is paired (the cases both runs share,
unchanged): a 0/1 check gets the exact McNemar test and Newcombe's paired
interval, shares and clustered cases a seeded paired bootstrap. Gated
metrics (`pass` unless `Gate(metrics=[…])`) are Holm-adjusted; every other
check is reported with Benjamini–Hochberg q-values as exploratory.

| Verdict | When | Exit |
|---|---|---|
| `pass` | the interval rules out a drop larger than `tolerance` | 0 |
| `inconclusive` | it cannot (too few cases, too noisy) | 0; 2 with `Gate(strict=True)` |
| `regressed` | the drop is larger than `tolerance` and significant, or a `critical` case that passed every repeat in the baseline fails every repeat now | 1 |
| `failed` | a threshold missed, or a `critical` case fails with no baseline | 1 |
| `error` | over `max_error_rate` of trials errored, or the run stopped before every case | 3 |

Without a `gate`, nothing changes from 1.9.0: `threshold` (or "any case
failed") decides, and the exit code is 0 or 1. Exit 3 exists so CI can
tell "the endpoint was down" from "the prompt got worse".

## In the application

An eval is declared in Python, in `Application(jobs=[...])` beside the
other jobs — `repeats`, `cluster` and the `gate` are its arguments:

```python
# app/main.py
from operonx.app import Application, Eval
from operonx.app.evals import Gate

from bot import reply_flow
from checks import names_the_time, polite

APP = Application(
    "bot",
    jobs=[
        Eval(
            "replies",
            graph=reply_flow,
            dataset="dataset:replies",
            evaluators=[polite, names_the_time],
            repeats=3,
            gate=Gate(threshold=0.9, baseline="latest", tolerance=0.03),
        )
    ],
)
```

A `[[job]]` block in `operonx.toml` is refused with a pointer to
`app/main.py`.

`evaluators` are objects in your code, ready to call — for a helper,
bind it first:

```python
# checks.py
from operonx.app.evals import judge

polite = judge("llm:judge", "The reply is polite and correct.", name="polite")

async def names_the_time(output, expected):
    return expected in output
```

```bash
operonx run replies          # exits with the gate's code: 0 pass, 1 failed/regressed, 2, 3
```

Runs carry `origin=eval` and are filed under `.operonx/runs/evals/`,
kept forever by default ([Runs](11-runs.md#retention)).

## The command line: `operonx eval`

```bash
operonx eval list                                   # evals, their cases, the last verdict
operonx eval run replies                            # one experiment; exits with the gate's code
operonx eval run replies --repeats 3 --split smoke --tag critical --sample 50
operonx eval run replies --baseline main --tolerance 0.03 --strict --report md,junit --out out/eval
operonx eval compare <expA> <expB> [--tolerance 0.03]   # any two experiments, paired
operonx eval report <exp> --format md|json|junit        # from its record, or the score store
operonx eval rescore <exp> --evaluators checks:strict   # new checks on recorded outputs
operonx eval calibrate replies --runs 3 --tolerance 0.03
operonx eval power replies --delta 0.05
operonx eval dataset validate|stats|diff replies [--against main]
```

`run` writes the experiment to the project's **score store** — `[evals]
scores = "score_store:<name>"` in `operonx.toml`, else the ClickHouse sink
of `[tracing]` (the runs' database), else files under the runs root —
unless the eval names its own store or `--no-store` is given. An
experiment is named by its run id (looked up in the evals' record
directories, then the store) or a record directory.

| Exit | `run`, and `compare` with a `--tolerance` |
|---|---|
| 0 | pass, or inconclusive (a warning says why) |
| 1 | failed or regressed |
| 2 | inconclusive under `--strict`; or the command could not run as asked — an unknown eval, a bad flag, no baseline at the merge-base, a store that cannot be opened (stderr says which) |
| 3 | an infrastructure error: retry the job, the quality is unknown |

**`--baseline main`** (the same as `git:origin/main`; use `git:<ref>` for
another branch) compares with the experiment of `git merge-base HEAD
origin/main`: the commit the branch started from, which is what the
change should be measured against. It is read from the score store: the
newest finished run of this eval at that commit on a clean tree, the one
with the same dataset and evaluators if there is one. If main never
stored one, the command stops before running anything and says so —
run the eval on main (a pipeline on main, or a nightly one, keeps
baselines warm).

**Reports.** `--report md,json,junit` writes `report.md` (the verdict
first, then why: metrics with intervals, the comparison, the cases that
flipped, failing and flaky cases, cost), `experiment.json` and
`junit.xml` (a `gate` testcase, then one per case and check; it
validates against the JUnit schema GitLab's test widget reads).

## Calibrate before you pick a tolerance

A tolerance smaller than the eval's noise turns every run
`inconclusive`; a larger one hides real drops. Measure it:

```bash
operonx eval calibrate replies --runs 3 --tolerance 0.03
```

`calibrate` runs the eval three times on this commit (an A/A test), fits
each case's pass probability, and checks synthetic A/A pairs through the
gate itself: the tolerance 95% of them pass at, for 1, 2, 3 and 5
repeats, and the fewest repeats that reach yours. The interval covers
the choice of cases, not only flakes: 40 cases that always pass still
cannot rule out an 8.8-point drop. Then:

```bash
operonx eval power replies --delta 0.05     # how many cases a 5-point drop needs
```

With 10% of cases changing between versions, a 5-point drop needs about
312 cases to be seen 80% of the time.

## In CI

The merge request's job compares with main's experiment at the
merge-base, so main must store its experiments somewhere CI can read —
a ClickHouse in `[tracing]` (or `[evals] scores`) with its credentials
in CI variables — and the checkout needs the history down to the fork
point.

**GitLab:**

```yaml
variables:
  GIT_DEPTH: 0                         # the merge-base needs history

.eval:
  stage: test
  artifacts:
    when: always
    reports: { junit: out/eval/junit.xml }   # the MR's test widget
    paths: [out/eval/]
  retry: { max: 1, exit_codes: [3] }         # 3 = infrastructure, not quality

eval:main:                             # stores the experiment MRs compare with
  extends: .eval
  script:
    - uv run operonx eval run replies --report md,junit --out out/eval
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
    - if: $CI_PIPELINE_SOURCE == "schedule"

eval:mr:
  extends: .eval
  script:
    - git fetch origin $CI_MERGE_REQUEST_TARGET_BRANCH_NAME
    - uv run operonx eval run replies --baseline git:origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME
        --tolerance 0.03 --report md,junit --out out/eval
  after_script:                        # the report as an MR comment (a project token)
    - >-
      curl -sS --request POST --header "PRIVATE-TOKEN: $EVAL_BOT_TOKEN"
      --data-urlencode "body@out/eval/report.md"
      "$CI_API_V4_URL/projects/$CI_PROJECT_ID/merge_requests/$CI_MERGE_REQUEST_IID/notes"
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

**GitHub Actions** (GitHub has no built-in JUnit widget; a report action
renders it):

```yaml
on:
  push: { branches: [main] }
  pull_request:

jobs:
  eval:
    runs-on: ubuntu-latest
    permissions: { contents: read, pull-requests: write, checks: write }
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }       # the merge-base needs history
      - uses: astral-sh/setup-uv@v5
      - name: eval on main (stores the baseline)
        if: github.event_name == 'push'
        run: uv run operonx eval run replies --report md,junit --out out/eval
      - name: eval against main's merge-base
        if: github.event_name == 'pull_request'
        run: uv run operonx eval run replies --baseline main --tolerance 0.03 --report md,junit --out out/eval
      - name: comment the report
        if: always() && github.event_name == 'pull_request'
        run: gh pr comment ${{ github.event.pull_request.number }} --body-file out/eval/report.md
        env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}" }
      - uses: mikepenz/action-junit-report@v5
        if: always()
        with: { report_paths: out/eval/junit.xml }
```

On either, a merge-request pipeline that runs on the merge result
compares with the target branch's tip — the merge-base of the merge
commit — so main's pipeline must have run on that commit.

## pytest

An opt-in plugin makes a pytest session one experiment: each test that
calls `run_case` is a case, its verdict is the test's outcome, and the
session ends with a record, a gate and reports. Installing operonx never
loads it:

```bash
pytest -p operonx.app.evals.pytest_plugin --operonx-eval-report md,junit
# or, in the root conftest.py:  pytest_plugins = ["operonx.app.evals.pytest_plugin"]
```

```python
import pytest

from operonx.app.evals import exact
from operonx.app.evals.pytest_plugin import cases

from bot import reply_flow


@pytest.mark.parametrize("case", cases("dataset:replies", split="smoke"))
async def test_reply(case, run_case):
    got = await run_case(reply_flow, case, evaluators=[exact("intent")])
    assert got.trace.path(types={"llm"}) == ["classify", "answer"]
```

A failing check fails its test with the reason; a failing `assert` is
recorded on the case. `got.check(evaluator)` adds a synchronous check;
a sync test calls `run_case.sync(...)`. Options: `--operonx-eval-name`,
`--operonx-eval-dir`, `--operonx-eval-baseline` / `--operonx-eval-tolerance`
/ `--operonx-eval-strict` (a gate that does not pass makes a passing
session exit 1), `--operonx-eval-report` / `--operonx-eval-out`,
`--operonx-eval-store`. Keep tests that call a real model behind a
marker, so plain `pytest` stays offline.

## Where to go next

- Compare runs and watch their cost: [Runs](11-runs.md).
- The API: [operonx.app](../api/app.md#evals).
