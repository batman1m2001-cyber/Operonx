# Evals: cases, repeats, a gate

An **eval** runs a graph over a dataset of cases and judges each output. It
is a [Job](02-composition.md) with `origin=eval`, so it runs concurrently,
resumes and writes a record; `operonx run <eval>` exits with the gate's
code, which is what CI reads. One eval run is one **experiment**: its
record says what produced the numbers (a fingerprint), how sure they are
(confidence intervals), and what the gate decided.

## The system under test, the cases, a check

```python file=labels.py
from operonx import END, START, graph, op


@op(bound="sync")
def classify(text: str = "") -> dict:
    return {"label": "refund" if "money back" in text else "other"}


@graph
def flow(text: str = ""):
    c = classify(text=text)
    START >> c >> END
```

A dataset is a JSONL file, one case per line. `"dataset:labels"` names
`datasets/labels.jsonl`. `tags` slice the results; a `critical` case is
must-pass under a gate.

```json file=datasets/labels.jsonl
{"id": "refund-1", "input": "I want my money back", "expected": {"label": "refund"}, "tags": ["critical"]}
{"id": "refund-2", "input": "money back, please", "expected": {"label": "refund"}}
{"id": "hello", "input": "hello there", "expected": {"label": "other"}}
```

An evaluator is a function that takes any of `input`, `output`,
`expected`, `row`, `outputs`, `trace` by name and returns a bool, a score
in [0, 1], or `{"passed", "score", "reason"}`. Built-ins: `exact`,
`contains`, `fuzzy`, `json_match`, a model as a judge (`judge`, below),
and over the run: `trajectory.ops`, `trajectory.tool_calls`,
`trajectory.op_output`, `budget`.

## Run it: repeats, metrics, the fingerprint

```python
from operonx.app.evals import Eval, Gate, exact

from labels import flow

ev = Eval(
    "labels",
    graph=flow,
    input="text",  # no doors: each case's input is bound to `text`
    dataset="dataset:labels",
    evaluators=[exact("label")],
    repeats=3,  # each case three times: a flaky case shows up as flaky
    gate=Gate(threshold=0.9),  # under 90% fails; a failing `critical` case fails
)
run = ev.run_sync()
s = run.meta["eval"]

print(s["cases"], s["trials"], s["pass_rate"])  # 3 9 1.0
print(s["metrics"]["pass"])  # mean, ci_lo, ci_hi, se, n, method — never a bare number
print(s["reliability"])  # stable_pass / stable_fail / flaky, and pass^k for k = 1..3
print(s["fingerprint"])  # code_version, graph_hash, config_hash, dataset_version, …
assert s["gate"]["verdict"] == "pass" and s["gate"]["exit_code"] == 0
assert [i.key for i in run.items][:4] == ["refund-1#0", "refund-2#0", "hello#0", "refund-1#1"]
```

- With `repeats=N` every case is N items, keyed `<id>#<r>`. `passed` and
  `failed` count trials; `pass_rate` is the mean over cases of each
  case's pass share.
- `metrics` has `pass` (every check passed) and one entry per check, each
  with a 95% interval: Wilson for one 0/1 trial per case, the CLT for
  shares over repeats, a clustered SE when cases share a `cluster`
  (`Eval(cluster="scenario")` names the field).
- The fingerprint is two experiments' identity: same `dataset_version`
  and `evaluators_hash` means directly comparable. `config_hash` covers
  the resolved `llm:` resources with every secret left out.

## Compare against a baseline

```python
from operonx.app.evals import Eval, Gate, exact

from labels import flow


def make():
    return Eval(
        "labels_vs_latest",
        graph=flow,
        input="text",
        dataset="dataset:labels",
        evaluators=[exact("label")],
        gate=Gate(baseline="latest", tolerance=0.05),  # a drop over 5 points matters
    )


first = make().run_sync()  # nothing to compare yet: a warning, thresholds only
second = make().run_sync()  # compared with `first`, case by case
gate = second.meta["eval"]["gate"]
test = gate["comparison"]["tests"][0]
print(gate["verdict"], test["metric"], test["diff"], test["ci_lo"], test["ci_hi"], test["p"])
print(gate["reasons"])  # why it is not a pass
assert gate["comparison"]["baseline"] == first.run_id
# same answers on all three cases — but three cases cannot rule out a 5-point drop
assert test["diff"] == 0 and test["ci_lo"] < -0.05
assert gate["verdict"] == "inconclusive" and gate["exit_code"] == 0
```

The comparison is paired: the same cases in both runs, so each case is
its own control. A 0/1 check uses the exact McNemar test and Newcombe's
paired interval; shares over repeats and clustered cases use a seeded
paired bootstrap. Per gated
metric (`pass` unless `Gate(metrics=[…])`):

| Verdict | When | Exit |
|---|---|---|
| `pass` | the CI rules out a drop larger than `tolerance` | 0 |
| `inconclusive` | it cannot: too few cases, or too noisy | 0, or 2 with `strict=True` |
| `regressed` | the drop is larger than `tolerance` and significant (Holm-adjusted), or a must-pass case that passed in the baseline now fails | 1 |
| `failed` | a `threshold` missed, or a must-pass case fails with no baseline | 1 |
| `error` | more than `max_error_rate` (5%) of trials errored, or the run stopped early | 3 |

`tolerance` is required with a baseline: there is no honest default.
The run above is `inconclusive` although nothing changed: the interval on
three cases is about ±56 points. A real gate needs hundreds of cases (a
5-point drop needs about 312 paired cases to be seen 80% of the time).
Without a `gate`, an eval passes or fails exactly as `threshold` says,
and exits 0 or 1.

## Check the path, not just the answer

An evaluator that takes `trace` gets a `TraceView` of the case's own run:
every op execution in order, with inputs, outputs, status, timing and
cost. The built-ins on top of it check how the answer was reached.

```python file=agent.py
from operonx import END, START, graph, op
from operonx.core.ops import if_


@op(bound="sync")
def classify(text: str = "") -> dict:
    return {"kind": "order" if "order" in text else "chat"}


@op(bound="sync")
def plan(text: str = "") -> dict:
    # stands in for an LLM call: an op whose outputs carry `cost_usd` is one
    calls = [{"name": "lookup", "args": {"order_id": text.split()[-1]}}]
    return {"tool_calls": calls, "cost_usd": 0.0}


@op(bound="sync")
def chat(text: str = "") -> dict:
    return {"reply": "hi!"}


@graph
def agent(text: str = ""):
    c = classify(text=text, name="classify")
    p = plan(text=text, name="plan")
    s = chat(text=text, name="chat")
    route = if_(c["kind"] == "order", p).else_(s)
    START >> c >> route
    p >> END
    s >> END
```

A case can carry its reference trajectory:

```json file=datasets/agent.jsonl
{"id": "order", "input": "where is order 42", "expected": {"kind": "order"}, "trajectory": {"ops": ["classify", "plan"], "tool_calls": [{"name": "lookup", "args": {"order_id": "42"}}]}}
{"id": "chat", "input": "hello", "expected": {"kind": "chat"}, "trajectory": {"ops": ["classify", "chat"], "tool_calls": []}}
```

```python
import asyncio

from operonx.app.evals import Eval, budget, exact, trajectory
from operonx.telemetry.runs import open_run_store

from agent import agent

runs = open_run_store({"backend": "files"})  # .operonx/runs, or OPERONX_RUNS_DIR


def planned_the_lookup(output=None, trace=None):  # any evaluator can read the run
    step = trace.last("plan")
    return step is None or step.outputs["tool_calls"][0]["name"] == "lookup"


ev = Eval(
    "agent",
    graph=agent,
    input="text",
    dataset="dataset:agent",
    evaluators=[
        trajectory.ops(mode="strict"),  # the case's trajectory.ops, in order
        trajectory.tool_calls(mode="superset", args="subset"),
        trajectory.op_output("classify", exact("kind")),  # one op's output, not the answer
        budget(ms=2000, llm_calls=1),
        planned_the_lookup,
    ],
    trace=[runs],  # a store keeps each case's run, for rescore
)
run = ev.run_sync()
order = next(i.verdict for i in run.items if i.key == "order")
assert run.meta["eval"]["passed"] == 2, run.meta["eval"]["checks"]
print(order["checks"]["op_output(classify:exact(kind))"]["op"])  # the op judged: its op_id

# a new check over the same runs: nothing runs again
again = asyncio.run(
    ev.rescore(run.run_id, [trajectory.ops(["classify"], mode="superset")], store=runs)
)
assert again.summary["passed"] == 2
```

- `path()` lists the ops that ran, in order, leaving out branch routing;
  names are the ops' names (`name=` or the variable they were assigned to).
- Modes (AgentEvals'): `strict` — same steps, same order; `unordered` —
  same steps, any order; `subset` — nothing beyond the reference;
  `superset` — at least the reference. Tool arguments match `exact`,
  `subset` (the reference's arguments, extra ones allowed) or `ignore`.
- `budget` limits are inclusive; a cost limit over a call that reported
  no price fails, because that cost is unknown.
- `rescore` re-runs deterministic checks over a recorded run: the
  recorded outputs, the cases (an edited case is reported, not judged),
  and the stored runs for checks that read `trace`. A judge
  (`llm_judge`) is not rescored.
- An evaluator that does not take `trace` costs nothing extra; async
  evaluators of one case run at the same time.

## Keep experiments in a score store

With `scores=`, an eval writes its experiment, each item and every
check's score to a `ScoreStore` as it runs: `files` (JSONL plus an index,
under the runs root) by default, or the team's ClickHouse — the database
the runs are in. Studio and CI read experiments there instead of from one
machine's `evals/` folder.

```python
from operonx.app.evals import Eval, exact, publish
from operonx.telemetry.scores import ExperimentFilter, ScoreFilter, open_score_store

from labels import flow

store = open_score_store({"backend": "files"})  # or "score_store:team" from resources.yaml
ev = Eval(
    "labels_stored",
    graph=flow,
    input="text",
    dataset="dataset:labels",
    evaluators=[exact("label")],
    scores=store,
)
run = ev.run_sync()

got = store.get_experiment(run.run_id)
print(got.experiment.status, got.experiment.metrics["pass"]["mean"], len(got.items))
for s in store.scores(ScoreFilter(experiment_id=run.run_id)):
    print(s.case_id, s.score_name, s.passed, s.evaluator_version)
listed = store.list_experiments(ExperimentFilter(eval="labels_stored"))
assert [e.experiment_id for e in listed.items] == [run.run_id]

# a store that was down, or a run from before the store: publish its record
assert publish(run, store) == {"experiments": 1, "items": 3, "scores": 3}  # again: the same rows
assert len(store.scores(ScoreFilter(experiment_id=run.run_id))) == 3
```

- The job record is written first, always. A store that is slow or down
  costs the run at most `scores_timeout` (10 s) at its end; what it did
  not take is logged, and `publish(run, store)` sends it later.
- A score's id comes from what it judges (experiment, case, repeat,
  check), so writing the same verdict twice is one row.
- In `resources.yaml`: `score_store: {team: {backend: clickhouse, host: …,
  database: …}}`; on the eval: `Eval(..., scores="score_store:team")`.

## Declared in the application

```python file=checks.py
from operonx.app.evals import exact

label = exact("label")
```

```python file=app.py
from operonx.app import Application, Eval
from operonx.app.evals import Gate

from checks import label
from labels import flow

APP = Application(
    "evaldemo",
    jobs=[
        Eval(
            "labels",
            graph=flow,
            input="text",
            dataset="dataset:labels",
            evaluators=[label],
            repeats=2,
            gate=Gate(threshold=0.9),
        )
    ],
)
```

```toml file=operonx.toml
[project]
name = "evaldemo"
app  = "app:APP"
```

```bash run
operonx run labels
```

`operonx run <eval>` prints the summary and the gate's reasons, and exits
with the gate's code.

## `operonx eval`: run, compare, report

`operonx eval run` is the eval as one experiment: the record, the gate's
exit code, and the experiment in the project's score store (`[evals]
scores`, else the ClickHouse sink of `[tracing]`, else files under the
runs root; `--no-store` skips it). Flags override the declaration for
one run: `--repeats`, `--split`/`--tag`/`--cases`/`--sample` (a stable
sample) choose cases, `--baseline`/`--tolerance`/`--strict` set the gate.

```bash run
operonx eval list
operonx eval dataset validate labels
operonx eval run labels --report md,junit --out out/eval
operonx eval run labels --cases refund-1,hello --variant "fewer cases" --no-store
operonx eval run labels --baseline latest --tolerance 0.5 --report md --out out/eval2
operonx eval power labels --delta 0.1 --discordance 0.2
```

| Exit | `run` (and `compare --tolerance`) |
|---|---|
| 0 | pass, or inconclusive (with a warning) |
| 1 | failed or regressed |
| 2 | inconclusive under `--strict`, or the command could not run as asked (unknown eval, bad flag, no baseline at the merge-base, an unopenable store) |
| 3 | an infrastructure error: retry, the quality is unknown |

`--baseline main` (= `git:origin/main`) compares with the experiment of
`git merge-base HEAD origin/main` — the commit the branch started from —
read from the score store. Main's own runs put it there; when there is
none the command stops before running anything and says how to get one.
In CI that means a shared store (ClickHouse) and full git history.

The same as a library: any experiment, from its record or the store.

```python
from operonx.app.evals import compare, load_experiment
from operonx.app.evals.experiments import experiments_of
from operonx.app.evals.report import junit, markdown

newer, older = experiments_of("labels", record_dirs=["evals"])[:2]
print(markdown(newer))  # what CI posts on the merge request
assert junit(newer).startswith('<?xml version="1.0" encoding="UTF-8"?>')

got = compare(older, newer, tolerance=0.05)  # newer against older, paired
print(got["verdict"], got["comparison"]["tests"][0]["diff"])
assert compare(older, newer)["verdict"] is None  # no tolerance: reported, not judged
assert load_experiment(newer.experiment_id, record_dirs=["evals"]).eval == "labels"
```

## Calibrate before you pick a tolerance

A tolerance smaller than the eval's own noise makes every run
`inconclusive`. `calibrate` runs the eval k times on one commit (an A/A
test), and measures, through the gate itself, the tolerance 95% of A/A
runs pass at — per number of repeats. `power` says how many cases a drop
needs.

```bash run
operonx eval calibrate labels --runs 2 --simulations 50 --tolerance 0.3 --out out/cal
```

- Three cases that never flip still need a 56-point tolerance: the
  interval is about the cases, not just the flakes. The note says the
  30 points asked for cannot be had, and `power` says how many cases it
  would take.
- Put the number in `Gate(tolerance=…)` (or `[job.gate]`), where the
  merge request shows it. `calibrate --experiments a,b,c` reads runs
  that already exist instead of running.

## A pytest session as an experiment

The plugin is opt-in (`-p operonx.app.evals.pytest_plugin`, or
`pytest_plugins = [...]` in the root `conftest.py`); installing operonx
never loads it. Each test that calls `run_case` is one case of the
session's experiment, and the case's verdict is the test's outcome.

```python file=test_labels.py
import pytest

from operonx.app.evals import exact
from operonx.app.evals.pytest_plugin import cases

from labels import flow


@pytest.mark.parametrize("case", cases("dataset:labels"))
def test_label(case, run_case):
    got = run_case.sync(flow, case, evaluators=[exact("label")], input="text")
    assert got.trace.path() == ["c"]  # ops as the graph names them: `c = classify(...)`
```

```bash run
pytest -p operonx.app.evals.pytest_plugin test_labels.py -q --operonx-eval-name labels_pytest --operonx-eval-report junit
```

- An async test writes `got = await run_case(...)`.
- A failing check fails its test with the reason; a failing `assert` is
  recorded on the case. The session's record is
  `evals/<--operonx-eval-name>/<run_id>`, with the gate and its reports
  (`--operonx-eval-report md,json,junit`); `--operonx-eval-baseline` and
  `--operonx-eval-tolerance` compare it like an eval.

## Judges: a model grades one criterion

`judge()` is an evaluator that asks a model whether the output meets one
criterion: `PASS` or `FAIL`, with its reason kept. It is an operonx graph,
so each call is its own traced run (`origin=eval`, `role=judge`,
`judged_trace` = the case's run), its cost is kept apart from the
system's, and with a score store it is cached: an output it has already
judged, with the same rubric and model, costs no call.

```yaml file=resources.yaml
llm:judge:
  api_type: openai
  api_key: ${LLM_API_KEY}
  base_url: ${LLM_BASE_URL}
  model: gpt-4o-mini
```

```python
import operonx
from operonx.app.evals import Eval, judge
from operonx.telemetry.runs import open_run_store
from operonx.telemetry.scores import open_score_store

from labels import flow

operonx.bootstrap(resources="resources.yaml")  # the judge's model is a resource
store = open_score_store({"backend": "files"})  # experiments, scores — and the judge cache
runs = open_run_store({"backend": "files"})

refund = judge(
    "llm:judge",
    "The label is 'refund' exactly when the message asks for money back.",
    name="refund_label",  # the check is judge:refund_label; a rubric file names itself
)


def make():
    return Eval(
        "judged",
        graph=flow,
        input="text",
        dataset="dataset:labels",
        evaluators=[refund],
        scores=store,
        trace=[runs],
    )


run = make().run_sync()
s = run.meta["eval"]
hello = next(i for i in run.items if i.key == "hello")
check = hello.verdict["checks"]["judge:refund_label"]
print(check["label"], check["reason"])  # FAIL, and why: the rationale is kept
judged = runs.get_run(check["judge_trace_id"]).meta["metadata"]  # the judge's own run
assert judged["role"] == "judge" and judged["judged_trace"] == hello.trace_id
print(s["judges"]["judge:refund_label"])  # version, model, calls, cached, cost, alignment
print(s["gate"]["warnings"])  # UNVALIDATED JUDGE …: nothing shows it agrees with people yet

again = make().run_sync()  # the same outputs, the same judge: answered from the cache
assert again.meta["eval"]["judges"]["judge:refund_label"]["calls"] == 0
assert again.meta["eval"]["judges"]["judge:refund_label"]["cached"] == 3
```

- One judge checks one criterion and answers with one label. `labels=`
  and `pass_labels=` make it categorical (`("warm", "neutral", "cold")`);
  there is no 1–10 scale.
- `reference="auto"` shows the case's `expected` when it has one;
  `examples=[{input, output, verdict, reason}]` are few-shot;
  `include_trace=True` shows the case's run (a trajectory judge).
- The version (in the fingerprint) is the rubric, examples, labels,
  temperature, the model `llm:judge` resolves to, and the judge's code:
  editing any of them is a new judge and a cache miss. `Eval(judge_cache=
  False)` / `operonx eval run --no-cache` asks again (to see a judge's own
  noise).
- Any `@graph` you pass as an evaluator is a judge the same way: its
  inputs are named `input`, `output`, `expected`, `row`, `outputs` or
  `trace_summary`, and it returns `{passed, reason}`.
- A judge whose model is also the system's own gets a self-preference
  warning. Judges are never rescored.

## Is the judge right? Align it with people

A judge can gate, but until it is measured against human labels every
report says `UNVALIDATED JUDGE`. Human labels are scores with
`source="human"` on the same runs (Studio's review writes them); `align`
counts where the two agree.

```python
from operonx.app.evals import Eval, judge
from operonx.app.evals.align import align, record_alignment
from operonx.app.evals.experiments import experiments_of
from operonx.telemetry.scores import Score, open_score_store

store = open_score_store({"backend": "files"})
latest = experiments_of("judged", store=store)[0]
human = {"refund-1": True, "refund-2": True, "hello": False}  # what a person said
store.put_scores(
    [
        Score(
            score_name="judge:refund_label",
            source="human",
            target="trace",
            trace_id=item["trace_id"],
            passed=human[item["case"]],
            author="reviewer@example.com",
        )
        for item in latest.items
    ]
)
got = align(store, "judge:refund_label")
print(got.summary())  # κ, TPR, TNR, accuracy, n, the 2×2 table
assert (got.tp, got.tn, got.n) == (2, 1, 3) and got.kappa == 1.0
record_alignment(store, got)  # kept for this judge version: reports read it
```

```bash run
operonx eval align judge:refund_label
```

- κ (Cohen's kappa) is agreement beyond chance: ≥ 0.6 counts as aligned;
  under it, or with no record for the judge's current version, a gating
  judge is reported as `UNVALIDATED JUDGE` (the verdict is not changed).
- TPR: the judge says PASS when people do. TNR: it says FAIL when people
  do — the failures it catches. Three cases prove little: label a
  hundred.
- `align` exits 0 aligned, 1 not, 2 when nothing could be paired.

## Which answer is better? Pairwise

`pairwise()` asks which of two experiments' answers to a case is better,
in both orders at once (two branches of one judge run). When the orders
disagree the case is a `tie` marked `inconsistent`: the choice followed the
position, not the answer.

```python
import asyncio

import operonx
from operonx.app.evals import compare_pairwise, pairwise
from operonx.app.evals.experiments import experiments_of
from operonx.telemetry.scores import open_score_store

operonx.bootstrap(resources="resources.yaml")
store = open_score_store({"backend": "files"})
newer, older = experiments_of("judged", store=store)[:2]
better = pairwise("llm:judge", "Which label fits the message better?", name="better_label")
got = asyncio.run(compare_pairwise(older, newer, [better], scores=store))
j = got["judges"]["pairwise:better_label"]
print(j["wins_a"], j["wins_b"], j["ties"], j["inconsistency_rate"], j["preference"])
assert j["ties"] == 3  # the same labels both times: neither is better
```

`operonx eval compare A B --pairwise judges:better` adds the same table to
the comparison.

## Production traffic: online evals

An experiment has expected answers; production does not. An `OnlineEval`
judges runs that already happened — read back from the run store, after
the service answered — with checks that need no reference. Nothing runs on
the service's path, so its latency cannot move.

```python
import asyncio

from operonx.app.evals import OnlineEval
from operonx.app.jobs import Job
from operonx.telemetry.runs.sqlite import SqliteRunStore
from operonx.telemetry.scores import ScoreFilter, open_score_store

runs = SqliteRunStore(path="runs.sqlite")
traffic = ["hello", "I want my money back", "money back please", "hi"]
asyncio.run(Job("bot", graph="labels:flow", items=traffic, input="text", trace=[runs]).run())


def no_refund(output) -> bool:  # reference-free: it reads only what the run said
    return output["label"] != "refund"


scores = open_score_store({"backend": "files"})
online = OnlineEval(
    "refund_watch",
    runs={"origin": "job", "name": "bot"},  # which runs: a RunFilter
    store=runs,
    evaluators=[no_refund],
    scores=scores,
    sample=1.0,  # production: 0.05, stable per trace id
    queue={"to": "refunds", "when": "any_failed"},
)
run = asyncio.run(online.run())  # one pass; the next starts after it
print(run.meta["online"]["checks"])  # {'no_refund': {'passed': 2, 'failed': 2, ...}}
assert len(scores.scores(ScoreFilter(rule="refund_watch"))) == 4
assert asyncio.run(online.run()).counts.get("ok", 0) == 0  # nothing new since
```

- Declared, it is an `OnlineEval` in `Application(jobs=[...])`; cron (or a
  CI schedule) calls `operonx run refund_watch`.
- `sample` is stable — `sha1(trace_id)` under the rate — so a second
  worker and a backfill judge the same runs.
  `operonx eval online backfill refund_watch --since 7d` judges a past
  window (a new judge on last week's traffic) and leaves the cursor alone.
- `budget_usd_per_day` caps what judges spend in a UTC day across hosts;
  past it, code checks still run and the item says `budget_exhausted`.
- An evaluator taking `expected` is refused: production has no expected
  answer. It gets `input`, `output` (the run's request and answer),
  `trace`, `trace_summary`, `run` and `judging`.
- An alert on `score_fail_rate:no_refund` or `score_mean:<score>` watches
  the scores like `error_rate` watches runs.

## Review queues: what people look at

A queue is declared in `operonx.toml` (`[[queue]]` with `name`,
`reviewers`, `rubric`) and filled by an online eval's `queue = {to = …}` or
by `operonx eval queue add`. A review is a human score, so it is what a
judge is aligned against.

```python
from operonx.app.evals import QueueSpec, pending, queue_agreement, review
from operonx.telemetry.scores import open_score_store

scores = open_score_store({"backend": "files"})  # the online eval's store, from above
spec = QueueSpec("refunds", rubric={"resolved": "bool"}, reviewers=1)
waiting = pending(scores, ".operonx/queues", spec)
assert len(waiting) == 2  # the two runs the check failed
item, reviewed_by = waiting[0]
review(
    scores,
    author="ann",
    verdict="bad",
    rubric={"resolved": False},
    note="promised a refund we do not give",
    trace_id=item.trace_id,
    queue="refunds",
)
assert len(pending(scores, ".operonx/queues", spec)) == 1
print(queue_agreement(scores, "refunds"))  # κ needs two people on ≥ 10 items
```

`operonx eval queue list refunds` prints what still waits, and
`operonx eval queue add refunds --experiment <id> --failed` queues an
experiment's failed cases.

- `reviewers = 2` sends each item to two people; `queue_agreement`
  reports Cohen's κ between them per rubric entry — if people disagree,
  aligning a judge against them means little.
- Studio's older `reviews.jsonl` reads as review scores
  (`reviews_as_scores`); `operonx eval migrate-reviews` writes them to the
  score store once.
