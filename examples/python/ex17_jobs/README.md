# 17 · Jobs — one graph, run over a file

```
                         ┌────────────────────────────┐
 data/calls.jsonl ─[Job]─►  ingress ─► score ─► egress ├─► results ─► report
                         └────────────────────────────┘
                                      ▲
                         the same graph a Service puts
                         behind an HTTP route, unchanged
```

A `Job` names a graph, the items it runs over, what identifies an item,
and what a failure means. One run per item; the graph is not told it is
in a batch.

```python
score_calls = Job(
    "score_calls",
    graph=score_call,
    items=HERE / "data" / "calls.jsonl",   # or a list, or a function that yields
    key="call_id",                         # makes it resumable
    output=OUT / "scores.jsonl",           # optional export, as each finishes
    reduce=report,                         # one graph over every result
)
```

## What it buys over a for-loop

Every run leaves a record:

```
/tmp/operonx_jobs/ex17/jobs/score_calls/<run_id>/
  run.json       status, counts {ok, failed, empty, skipped}, what ran
  items.jsonl    {key, status, error, trace_id, ms} — one line per call
  results.jsonl  {key, result} — every call that finished
```

- **failed** items name the op and the error.
- **empty** items ran cleanly and sent nothing — the batch bug that
  otherwise reports OK.
- `run.results` is `{key: result}`; `run.reduced` is what `reduce` returned.
- `--resume` reads the last run and touches only the keys that are not done.

## Run it

```bash
uv sync
uv run python main.py
#   score_calls 20261006T… failed  ok=2 failed=1 empty=0 skipped=0
#   report: {'report': {'calls': 2, 'engaged': 1}}
#   failed c3: scored: ValueError: empty transcript
```

Give call `c3` a transcript in `data/calls.jsonl`, then:

```bash
uv run operonx run score_calls --resume     # ok=1 skipped=2; the report covers all three
uv run operonx run score_calls --show       # what would run, and exit
uv run operonx run --list                   # every job of the application
uv run operonx serve --only score           # the served form of the same graph
```

## Your own loader

`items` takes any function that yields items, called on every run — a
database query, a folder walk, a CSV reader:

```python
def recent_calls():
    yield {"call_id": "r1", "transcript": "..."}

score_recent = Job("score_recent", graph=score_call, items=recent_calls, key="call_id")
```

## Jobs in order: steps

```python
nightly = Job("nightly", steps=[score_recent, score_calls])
```

```bash
uv run operonx run nightly     # exits non-zero when a step failed
```

The first step that is not ok stops the rest; each step keeps its own
record. Anything more (a condition, a loop over days) is a Python
function calling `job.run()`.

Every run's traces carry `job`, `job_run` and `key`, as fields and as
tags, so a trace filter finds one job, one run, or one item.
