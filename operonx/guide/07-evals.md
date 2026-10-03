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
`expected`, `row`, `outputs` by name and returns a bool, a score in
[0, 1], or `{"passed", "score", "reason"}`. Built-ins: `exact`,
`contains`, `fuzzy`, `json_match`, `llm_judge`.

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
