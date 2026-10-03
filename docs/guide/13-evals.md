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

## Evaluators

An evaluator is a function — plain, async, or an `@op` (called for its
body) — that takes any of `input`, `output`, `expected`, `row` and
`outputs` by name, and returns a verdict:

- `True` / `False`;
- a score in [0, 1], which passes at 0.5;
- or `{"passed": …, "score": …, "reason": …}`.

`output` is what the case produced: the one item the graph sent (or its
result, for a graph with no doors), a list when it sent several, `None`
when it sent nothing. An evaluator that raises fails its case, with the
error on the verdict.

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
| `llm_judge(resource, rubric)` | an LLM grades the case against the rubric; its cost and usage stay on the verdict |

## Passing and failing

Without a `threshold`, the run fails when any case fails. With one, it
fails when the pass rate is under it. `run.meta["eval"]` — and the run's
`run.json` — hold the numbers: cases, passed, failed, errored,
`pass_rate`, each check's own pass count, the median case time, and what
the judge cost. Each case's item record carries its verdict, with the
`case`, its `repeat` and its `case_hash` (input + expected).

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
        baseline="latest",       # this eval's last finished run, or a run id
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

## In the manifest

An eval is a `[[job]]` with a `dataset`:

```toml
[[job]]
name        = "replies"
graph       = "bot:reply_flow"
dataset     = "dataset:replies"
evaluators  = ["checks:polite", "checks:names_the_time"]
threshold   = 0.9
concurrency = 4
```

`repeats` and `cluster` are keys of the block too, and a gate is its own
table (then `threshold` goes inside it):

```toml
[[job]]
name       = "replies"
graph      = "bot:reply_flow"
dataset    = "dataset:replies"
evaluators = ["checks:polite"]
repeats    = 3

[job.gate]
threshold = 0.9
baseline  = "latest"
tolerance = 0.03
```

`evaluators` names objects in your code, ready to call — for a helper,
bind it there first:

```python
# checks.py
from operonx.app.evals import llm_judge

polite = llm_judge("llm:judge", "Is the reply polite and correct?")

async def names_the_time(output, expected):
    return expected in output
```

```bash
operonx run replies          # exits with the gate's code: 0 pass, 1 failed/regressed, 2, 3
```

Runs carry `origin=eval` and are filed under `.operonx/runs/evals/`,
kept forever by default ([Runs](11-runs.md#retention)).

## Where to go next

- Compare runs and watch their cost: [Runs](11-runs.md).
- The API: [operonx.app](../api/app.md#evals).
