# Jobs and Runbooks

A served graph gets its work from a listener. A **Job** gives the same
graph its work from a *source* — a JSONL or CSV file, a Python iterable,
a resource — one run per item, and writes what `egress` sends to a
*sink*. The graph is not told which. That is the whole idea:

```
                      ┌───────────────────────────────┐
   socket ──[[serve]]─►                               ├─► socket
                      │   graph  (ingress … egress)   │
   file/db ─[[job]]───►                               ├─► file/db
                      └───────────────────────────────┘
```

`ingress` and `egress` read their peer from the run's session, never
from a wire. A job is a session whose `recv()` reads a source and whose
`send()` writes a sink. A scoring graph is served as HTTP on Monday and
run over a table on Tuesday without a line changing.

What a job adds over a for-loop is the **record**: every run leaves one
line per item saying what happened to it, and `--resume` reads that.

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
    out = egress(item=scored["result"])   # whatever reaches here goes to the sink
    START >> src >> scored >> out >> END


score_calls = Job(
    "score_calls",
    graph=score_call,
    source="data/calls.jsonl",
    sink="out/scores.jsonl",
    key="call_id",                        # what identifies an item
    concurrency=8,
    on_error="skip",
)

run = score_calls.run_sync()
print(run.summary())
# score_calls 20260924T020003-129907 failed  ok=98 failed=2 empty=0 skipped=0
for item in run.failed:
    print(item.key, item.error)          # c3  scored: ValueError: empty transcript
```

From `async` code, `await score_calls.run()`. From a shell:

```bash
operonx-run jobs:score_calls              # a Job object, as module:attr
operonx-run jobs:score_calls --resume     # only the keys the last run did not finish
operonx-run jobs:score_calls --show       # what would run, and exit
operonx-run jobs:score_calls --set day=2026-09-25 --sink out/today.jsonl
```

The exit status is 0 only when every item finished cleanly, so a cron
mail or a CI step sees a failed batch.

A job is also its own command line. `job.main()` takes the same flags,
minus the name — so a package whose `__main__.py` is

```python
from .job import job
raise SystemExit(job.main(doc=__doc__))
```

runs as `python -m jobs.score_calls --resume`, and `--help` prints the
package's docstring. `Runbook.main()` is the same; its `--set` reaches
every job.

A job with **no source** runs its graph once, on one empty item — the
shape of work done once rather than per item: create a table, write a
report. `preflight=["llm:gpt", "vector_store:docs"]` checks those
resources answer before any item runs, so a job whose endpoint is down
fails in seconds, once, instead of item by item.

## What the record says

One directory per run:

```
jobs/score_calls/20260924T020003-129907/
  run.json      status, counts, what ran (graph, source, sink, key, policy)
  items.jsonl   {key, status, error, trace_id, ms, sent, attempts} — one line per item
```

Per-item statuses:

| status    | meaning                                                                 |
|-----------|-------------------------------------------------------------------------|
| `ok`      | the run finished and `egress` sent at least one thing                   |
| `failed`  | an op raised — `error` names the op and the exception                   |
| `empty`   | the run finished cleanly and **sent nothing**                           |
| `timeout` | the run passed `item_timeout` and was cancelled                         |
| `skipped` | done in the run being resumed                                           |

`empty` has its own name because it is the batch bug that otherwise
reports OK: ninety items processed, nothing written, runner says done.

An op that raises does not raise out of the run. The engine records the
error on the run's trace and the run drains, so the runner reads item
failure from the trace, and `items.jsonl` carries each item's
`trace_id`. Every trace a job mints is tagged `job`, `job_run` and
`key` — as fields and as tags — so Langfuse filters one job, one run or
one item. A job itself is never a span.

### Resume

```python
run = score_calls.run_sync(resume=True)
```

reads the job's last run and feeds only the keys that are not `ok`,
`empty` or `skipped`. A job without `key` gets a random id per item and
cannot resume.

## Failure policy and deadlines

```python
Job(..., on_error="skip")        # carry on; the run is `failed` if any item failed
Job(..., on_error="stop")        # start nothing new after a failure; the run is `stopped`
Job(..., on_error="retry:3")     # try an item up to three more times, then carry on
Job(..., item_timeout=30)        # seconds; past it the run is cancelled → `timeout`
```

A timed-out item counts as failed for the policy and runs again on
resume. `concurrency` bounds items in flight; a slow item never blocks
the others.

## Sources and sinks

A source is anything with an async `items()`; a sink anything with
`write(key, item)` and `close()`. `Job` takes them in whatever shape you
have:

| you pass                              | it becomes                              |
|---------------------------------------|-----------------------------------------|
| `"data/calls.jsonl"`, `Path(...)`     | `JsonlSource` / `CsvSource` by extension |
| a directory                           | `DirSource` — `{"path", "name"}` per file (as a sink, an existing directory is a `DirSink`) |
| `None` (as a source)                  | one empty item — the graph runs once    |
| a list, an iterator, a generator function, an async generator | `PythonSource` |
| `"source:calls_today"`                | resolved through the resource hub       |
| a list (as a sink)                    | `ListSink`, appending to your list      |
| a callable `fn(key, item)`            | `PythonSink`                            |
| `None` (as a sink)                    | `NullSink` — the record still counts    |

Sources and sinks are **resources**: `resources.yaml` names what a
project reaches out to, and a table or a folder is that.

```yaml
source:calls_today:
  kind: jsonl                 # jsonl | csv | dir | python
  path: data/calls.jsonl
source:from_code:
  kind: python
  entry: feeds:calls          # module:attr — an iterable or a function returning one
sink:scores:
  kind: jsonl
  path: out/scores.jsonl
  mode: overwrite             # auto (default) | append | overwrite
sink:outputs:
  kind: dir                   # one <key>.json per item
  path: out/calls/
  options: {skip_existing: true}
```

A row written to a file sink carries the item's key first, as `_key`,
so it can be joined back to the source.

A file sink's default mode is `auto`: a fresh run starts the file over
and a `--resume` run adds to it, so running a job twice never doubles
its output. `DirSink(skip_existing=True)` skips any key whose file is
already there — re-running over a directory does only what is missing,
with no record needed.

## A graph without doors

A graph that takes the item as an input and returns a result — the
`engine.batch()` shape — runs as a job too:

```python
@graph
def doubling(val):
    d = double(x=val)
    START >> d >> END

Job("double", graph=doubling(val=PARENT["val"]), source=[1, 2, 3],
    sink=results, item_input="val")
```

The run's result is written to the sink as the item's result.

## In the manifest

`[[job]]` blocks sit beside the `[[serve]]` blocks in `operonx.toml`.
Paths are relative to the manifest; `source:`/`sink:` keys go to the
hub; `schedule` is cron text that is listed, not executed — the
deployment's cron calls `operonx-run`.

```toml
[[serve]]
name  = "score"
kind  = "http"
path  = "/score"
graph = "pipeline:score_call"        # the same graph …

[[job]]
name        = "score_calls"
graph       = "pipeline:score_call"  # … run over a file
source      = "data/calls.jsonl"
sink        = "sink:scores"
key         = "call_id"
concurrency = 8
on_error    = "retry:2"
item_timeout = 30
trace       = ["trace_local:default"]
schedule    = "0 2 * * *"
description = "Score every call; one run per call, resumable by call_id."
```

```bash
operonx-run --list                    # every [[job]], with its schedule
operonx-run score_calls               # by name
operonx-run score_calls --resume
operonx-serve --only score            # the served form of the same graph
```

## Stream mode

`session = "stream"` feeds every item through **one** run — the shape a
phone call has: shared state across items, one trace, and no per-item
accounting.

```python
Job("summarise", graph=summarise_flow, source="out/scores.jsonl",
    sink="out/summary.jsonl", session="stream", max_inflight=1024)
```

The record counts what was `fed` and what `egress` `sent`, holds the one
trace id, and cannot resume. Outputs reach the sink keyed by their
index, because nothing ties an output to an input inside one run.
`max_inflight` caps how far the source runs ahead of the graph.

## Runbooks: many jobs, one command, wired like a graph

Stage 2 sometimes needs *all* of stage 1 first — embed everything, then
cluster — and stages run in sequence or side by side. A `Runbook` states
that the way a `@graph` body states its ops: `>>` statements, one per
line, as many lines as the flow needs. A list on either side of `>>` is
one wire per element. No branches.

```python
from operonx.app.jobs import Runbook

with Runbook("nightly", schedule="0 3 * * *") as nightly:
    fetch >> [score, audit]          # one line, several wires
    score >> [report, export]
    [report, audit] >> notify        # notify waits for both
```

```
fetch ─┬─► score ─┬─► report ─┐
       │          └─► export  │
       └─► audit ─────────────┴─► notify
```

- The runbook is the set of wires from all its lines — a DAG, not a
  tree. A job is one node however many wires touch it; a cycle is
  refused when the block closes, naming its jobs.
- A job starts when every job wired into it has finished. Jobs with no
  incoming wire start first; jobs not wired together run side by side.
- `on_error="stop"` (the default) skips every job **downstream** of a
  failed one — the rest still runs. `on_error="continue"` runs
  downstream anyway. A running job is never cancelled.
- `Runbook("qc", a >> [b, c])` is the one-expression form of the same
  thing. `>>` returns its right side, so `a >> [b, c] >> d` chains.
- Hand-off between jobs is by **naming the same resource** — one job's
  sink and the next job's source point at the same file. Nothing is
  rewired at run time.
- `runbook.run(resume=True)` reaches the per-item jobs; stream jobs run
  fresh.
- A runbook that needs a condition is a Python function calling
  `run()` twice.
- `schedule=` declares when the deployment's cron should run it; it
  does not run anything.

A runbook sits in `Application(jobs=[...])` beside plain jobs, run by
name and listed by `--list` and the studio. The record,
`jobs/nightly/<run>/run.json`, holds the wires and, per job, a status,
timing and its own run id and path. A runbook is a record, never a
span: traces stay one per graph run, tagged with the job that minted it.

```toml
[[job]]
name       = "nightly"
runbook    = "jobs.nightly:nightly"    # names the Runbook object; its jobs declare the rest
schedule   = "0 3 * * *"
```

```bash
operonx-run nightly
operonx-run nightly --show             # prints the wires: fetch >> [score, audit] …
```

## What a job is not

Not a queue. Producers pushing work at runtime, several workers pulling,
a lease per item, ack and dead-letter — that is a service (Redis, SQS,
Postgres `SKIP LOCKED`), and a queue is one more *source* against the
same protocol. Not an op, and not a graph of graphs: a job declares
work to do with one Operon, and a runbook orders jobs. Design notes and
what is still to come: `docs/JOB_PLAN.md` in the repository.

## Where to go next

- `examples/python/ex17_jobs` — the worked example: a per-item job, a
  stream job, a runbook, the manifest form of each.
- [Deployment](08-deployment.md) — the served side of the same graph.
- [Tracing](07-tracing.md) — where a job's runs land.
