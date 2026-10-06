# Jobs

A served graph gets its work from a listener. A **Job** gives a graph its
work from **items** — a list, a `.jsonl` file, or a function that yields
them — one run per item, and keeps every result. That is the whole idea:

```
                      ┌───────────────────────────────┐
   socket ─[Service]──►                               ├─► socket
                      │   graph  (ingress … egress)   │
   items ──[Job]──────►                               ├─► results
                      └───────────────────────────────┘
```

A graph with doors reads its item through `ingress` and its result is what
`egress` sends — so a scoring graph is served as HTTP on Monday and run
over a file on Tuesday without a line changing. A plain graph that takes
inputs and returns outputs runs as a job too.

What a job adds over a for-loop is the **record**: every run leaves one
line per item saying what happened to it, keeps each result, and
`--resume` reads that.

## A first job

```python
from operonx.core import END, START, graph, op
from operonx.app.jobs import Job
from operonx.app.serve import egress, ingress


@op(bound="sync")
def score(call: dict = None) -> dict:
    if not call["transcript"].strip():
        raise ValueError("empty transcript")
    return {"result": {"call_id": call["call_id"], "words": len(call["transcript"].split())}}


@graph
def score_call():
    src = ingress()                       # one item per run
    scored = score(call=src["item"])
    out = egress(item=scored["result"])   # what reaches here is the item's result
    START >> src >> scored >> out >> END


score_calls = Job(
    "score_calls",
    graph=score_call,
    items="data/calls.jsonl",
    key="call_id",                        # what identifies an item
    concurrency=8,
)

run = score_calls.run_sync()
print(run.summary())
# score_calls 20261006T020003-129907 failed  ok=98 failed=2 empty=0 skipped=0
run.results                               # {"c1": {...}, "c2": {...}, ...}
for item in run.failed:
    print(item.key, item.error)           # c3  scored: ValueError: empty transcript
```

From `async` code, `await score_calls.run()`. From a shell, by its name in
the application or as `module:attr`:

```bash
operonx run score_calls                   # a job of the project's Application
operonx run jobs:score_calls              # a Job object, as module:attr
operonx run score_calls --resume          # only the keys the last run did not finish
operonx run score_calls --show            # what would run, and exit
operonx run score_calls --items today.jsonl --set day=2026-10-06
operonx run --list                        # every job of the application
```

The exit status is 0 only when every item finished cleanly, so a cron
mail or a CI step sees a failed batch. `job.main()` makes a job its own
command line with the same flags (`python -m jobs.score_calls --resume`).

## Items

| you pass                         | the job loops over                                  |
|----------------------------------|-----------------------------------------------------|
| a list, any iterable or async iterable | its elements                                  |
| a function (a generator function, usually) | what it returns — **called on every run** |
| `"data/calls.jsonl"` or a `Path` | one item per line                                   |
| `None`                           | one empty item: the graph runs once                 |

That is all. A CSV, a folder, a database table, an S3 bucket is a
function you write:

```python
def calls_today():
    for row in db.execute("SELECT * FROM calls WHERE day = current_date"):
        yield dict(row)

Job("score_calls", graph=score_call, items=calls_today, key="call_id")
```

A synchronous iterator is advanced in a worker thread, so a slow loader
does not stall the runs in flight.

### How an item reaches the graph

- A graph with doors takes the item through `ingress` — `src["item"]`.
- `input="call"` hands the whole item to that graph parameter.
- Otherwise a dict item fills the graph's parameters by name (a field the
  graph does not take is an error), and anything else goes to the graph's
  only free parameter.
- `inputs={...}` are fixed inputs for every item; `--set k=v` adds to them.

```python
@graph
def doubling(val):
    d = double(x=val)
    START >> d >> END

Job("double", graph=doubling, items=[{"val": 1}, {"val": 2}])     # by name
Job("double", graph=doubling, items=[1, 2, 3])                    # the only parameter
```

## Results, output and reduce

Every successful result is kept: `run.results` is `{key: result}`, read
from the record's `results.jsonl`. A resumed run keeps the earlier run's
results for the keys it skipped, so `run.results` is always the whole
batch. `keep_results=False` keeps nothing, for results too big to store.

`output=` also exports each result as it finishes:

```python
Job(..., output="out/scores.jsonl")                 # {"key", "result"} lines
Job(..., output=lambda key, result: db.save(key, result))   # sync or async
```

`reduce=` runs one graph once, after the last item, over every result —
counting, a report, a summary for a channel:

```python
@op(bound="sync")
def tally(results: list = None) -> dict:
    return {"report": {"calls": len(results)}}


@graph
def report(results):
    t = tally(results=results)
    START >> t >> END


Job("score_calls", graph=score_call, items="data/calls.jsonl", key="call_id", reduce=report)
run = score_calls.run_sync()
run.reduced                                         # {"report": {"calls": 98}}
```

`results` is the list of successful results in key order, including the
ones an earlier run kept. `on_error="stop"` after a failure skips the
reduce.

## What the record says

One directory per run:

```
.operonx/jobs/score_calls/20261006T020003-129907/
  run.json       status, counts, what ran (graph, items, key, policy)
  items.jsonl    {key, status, error, trace_id, ms, sent, attempts} — one line per item
  results.jsonl  {key, result} — one line per item that finished
```

Runs go under `.operonx/jobs` at the project root; `[jobs] dir` in
`operonx.toml` or `record_dir=` moves them. Per-item statuses:

| status    | meaning                                                                 |
|-----------|-------------------------------------------------------------------------|
| `ok`      | the run finished with a result                                          |
| `failed`  | an op raised — `error` names the op and the exception                   |
| `empty`   | the run finished cleanly and **sent nothing**                           |
| `timeout` | the run passed `timeout` and was cancelled                              |
| `skipped` | done in the run being resumed                                           |

`empty` has its own name because it is the batch bug that otherwise
reports OK: ninety items processed, nothing written, runner says done.

An op that raises does not raise out of the run: the engine records the
error on the run's trace and the runner reads it from there. Every trace a
job mints is tagged `job`, `job_run` and `key` — as fields and as tags —
so a trace filter finds one job, one run or one item. A job itself is
never a span.

### Resume

```python
run = score_calls.run_sync(resume=True)
```

reads the job's last run and feeds only the keys that are not `ok`,
`empty` or `skipped`. A job without `key` gets a random id per item and
cannot resume.

## Failure policy and deadlines

```python
from operonx import Retry

Job(..., on_error="skip")                    # default: carry on; the run is `failed` if any item failed
Job(..., on_error="stop")                    # start nothing new after a failure; the run is `stopped`
Job(..., retry=Retry(max_attempts=3))        # a failed or timed-out item runs again, with backoff
Job(..., timeout=30)                         # seconds per item; past it the run is cancelled → `timeout`
Job(..., preflight=["llm:gpt"])              # these resources must answer before any item runs
```

`concurrency` (default 4) bounds items in flight; a slow item never
blocks the others. `on_item=fn` is called with each item's result as it
is recorded — progress lines, events for a host.

## Steps: jobs in order, as one command

```python
nightly = Job("nightly", steps=[fetch, score_calls, report_job])
```

```bash
operonx run nightly            # exits non-zero when a step failed
operonx run nightly --resume   # reaches every step
```

The first step whose run is not `ok` stops the rest. Each step keeps its
own record; the steps job's `run.json` lists each step's status and run
id. Hand-off between steps is by naming the same file — one step's
`output=` is the next one's `items=`. Anything more (a condition, a loop
over days) is a Python function calling `job.run()`. On a clock, cron or
CI runs `operonx run nightly`.

## In the application

Jobs are declared in Python, beside the services, in the module
`operonx.toml` points at (`app = "app.main:APP"`):

```python
APP = Application(
    "scoring",
    services=[Service("score", http("POST", "/score", port=8017), graph=score_call)],
    jobs=[score_calls, nightly],
)
```

A `[[job]]` block in `operonx.toml` is refused with a pointer here.

## What a job is not

Not a queue. Producers pushing work at runtime, several workers pulling,
a lease per item, ack and dead-letter — that is a service (Redis, SQS,
Postgres `SKIP LOCKED`); a job's `items` can read from one. Not an op,
and not a graph of graphs: a job declares work to do with one graph, and
`steps` orders jobs.

## Where to go next

- `examples/python/ex17_jobs` — the worked example: a job over a file, a
  loader function, a reduce, steps.
- [Deployment](08-deployment.md) — the served side of the same graph.
- [Tracing](07-tracing.md) — where a job's runs land.
- [Evals](13-evals.md) — a job that judges its graph against a dataset.
