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
`contains`, `fuzzy`, `json_match`, `llm_judge`, and over the run:
`trajectory.ops`, `trajectory.tool_calls`, `trajectory.op_output`,
`budget`.

## Run it: repeats, metrics, the fingerprint

```python
from operonx.app.evals import Eval, Gate, exact

from labels import flow

ev = Eval(
    "labels",
    graph=flow,
    item_input="text",  # no doors: each case's input is bound to `text`
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
        item_input="text",
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
    item_input="text",
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
again = asyncio.run(ev.rescore(run.run_id, [trajectory.ops(["classify"], mode="superset")], store=runs))
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
    item_input="text",
    dataset="dataset:labels",
    evaluators=[exact("label")],
    scores=store,
)
run = ev.run_sync()

got = store.get_experiment(run.run_id)
print(got.experiment.status, got.experiment.metrics["pass"]["mean"], len(got.items))
for s in store.scores(ScoreFilter(experiment_id=run.run_id)):
    print(s.case_id, s.score_name, s.passed, s.evaluator_version)
assert [e.experiment_id for e in store.list_experiments(ExperimentFilter(eval="labels_stored")).items] == [run.run_id]

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
  database: …}}`; in `operonx.toml`: `scores = "score_store:team"` on the
  `[[job]]`.

## Declared in `operonx.toml`

```python file=checks.py
from operonx.app.evals import exact

label = exact("label")
```

```toml file=operonx.toml
[project]
name = "evaldemo"

[[job]]
name       = "labels"
graph      = "labels:flow"
item_input = "text"
dataset    = "dataset:labels"
evaluators = ["checks:label"]
repeats    = 2

[job.gate]
threshold = 0.9
```

```bash run
operonx run labels
```

`operonx run <eval>` prints the summary and the gate's reasons, and exits
with the gate's code.
