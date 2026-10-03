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
the judge cost. Each case's item record carries its verdict.

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
operonx run replies          # exits non-zero when the eval fails: a CI gate
```

Runs carry `origin=eval` and are filed under `.operonx/runs/evals/`,
kept forever by default ([Runs](11-runs.md#retention)).

## Where to go next

- Compare runs and watch their cost: [Runs](11-runs.md).
- The API: [operonx.app](../api/app.md#evals).
