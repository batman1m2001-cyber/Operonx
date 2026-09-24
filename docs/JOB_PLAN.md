# Jobs — running Operons over data that does not talk back

Status: **phase 1 built, 2026-09-24** (branch `feat/jobs`, §11). §8 holds the
decisions the user has made; §9 what is still open.

## 1. The idea in one picture

A project is a set of graphs plus the things that *put work into them*.
Today the only such thing is a listener. A job is the other one: a source
that is read, a sink that is written, a trigger that is a command or a
clock. Same graph, two ways to mint a run.

```
                      ┌───────────────────────────────┐
   socket ──[[serve]]─►                               ├─► socket
                      │   graph  (ingress … egress)   │
   file/db ─[[job]]───►                               ├─► file/db
                      └───────────────────────────────┘
                                 ▲
                      one graph, one trace shape,
                      one pair of door ops
```

`ingress` and `egress` already resolve their peer from the run's session
(`current_session()`), never from a wire. A job is a session whose `recv()`
reads a source and whose `send()` writes a sink. The graph cannot tell the
difference: a scoring graph is served as HTTP on Monday and run over a
table on Tuesday without a line changing.

## 2. What exists, what is missing

| Have                                              | Missing                                        |
|---------------------------------------------------|------------------------------------------------|
| `Session` protocol; `ingress`/`egress` ops        | a session backed by a source and a sink        |
| `[[serve]]` with `per_request`/`per_connection`   | `[[job]]` with `per_item`/`stream`             |
| `STREAM_KINDS` already names `"file"`, `"queue"`  | anything that implements them                  |
| `engine.batch()` = gather + semaphore             | progress, per-item failure, resume, a record   |
| nothing that composes jobs                        | a Runbook: Sequential / Parallel / `>>`        |
| studio manifest has a `schedule` field            | core reads it; anything runs it                |

The gap is what every project rebuilds as a "runner script": a loop with
no record, so nothing can say which items produced nothing.

## 3. The Job

A `Job` is a class, not an op. It declares work to do with an Operon and
owns everything the graph should not know about:

```
Job
 ├─ graph        : entry point
 ├─ source       : where items come from            (resource "source:…")
 ├─ sink         : where results go                 (resource "sink:…")
 ├─ key          : which item field is its identity (else a random id)
 ├─ session      : per_item | stream
 ├─ concurrency  : items in flight (per_item only)
 ├─ on_error     : skip | stop | retry:N
 ├─ trace        : consumer keys, as [[serve]] has
 └─ schedule     : cron text, declarative in phase 1 (§7.2)
```

Python and manifest are the same object, as `engine.serve()` and
`[[serve]]` already are:

```python
score = Job("score_calls", graph=score_call,
            source="source:calls_today", sink="sink:scores",
            key="call_id", on_error="skip", concurrency=8)
run = await score.run()        # → JobRun: counts, failures, per-item status
```

```toml
[[job]]
name        = "score_calls"
graph       = "pipeline.score:score_call"
source      = "source:calls_today"
sink        = "sink:scores"
key         = "call_id"
session     = "per_item"
concurrency = 8
on_error    = "skip"
trace       = ["trace_local:default"]
schedule    = "0 2 * * *"
```

```
$ operonx-run score_calls                 # manual
$ operonx-run score_calls --resume        # only keys not already ok
$ operonx-run --list                      # jobs the manifest declares
```

## 4. Two session modes

```
per_item  (default)                 stream
──────────────────────              ──────────────────────
source ─┬─ item 1 ─► run 1 ─► sink  source ─► items ─► ONE run ─► sink
        ├─ item 2 ─► run 2 ─► sink            (ingress yields each item)
        └─ item 3 ─► run 3 ─► sink
one trace / failure / retry         one trace; shared state across items;
per item; resumable by key          the callbot shape
```

`per_item` is the default because the failure story is what batch work
gets wrong, and one run per item makes it honest: an item that produced
nothing is a run whose egress never fired, and the record says so.

## 5. Runbook — many jobs, one command

Stage 2 sometimes needs *all* of stage 1 first (embed everything, then
cluster), and stages run in sequence or side by side. That is composed
**above** the engine, by a `Runbook`, never by a graph: a graph of jobs
that contain graphs is two ideas wearing one name, and its trace would
nest a job inside a run inside a job.

A Runbook is a tree of two shapes and one operator:

```python
nightly = Runbook("nightly",
    extract >> [embed >> cluster, score]      # >> = Sequential, [ ] = Parallel
)
# the same thing, spelled out:
nightly = Runbook("nightly",
    Sequential(extract, Parallel(Sequential(embed, cluster), score)))
```

```
  extract ──► ┬─► embed ──► cluster ─┐
              └─► score ─────────────┴─► done
```

- `Sequential` runs each child after the previous one *succeeds*; a
  failed child stops the sequence (the runbook's `on_error`: `stop` |
  `continue`).
- `Parallel` runs its children with `asyncio.gather` and waits for all.
- Hand-off is by **naming the same resource**: `extract.sink` and
  `embed.source` both point at `data/embeds.jsonl`. Explicit, visible in
  resources.yaml, nothing rewired at run time.
- The runner is ~80 lines of asyncio walking the tree. No engine, no
  branch, no loop; a runbook that needs a condition is a Python
  function calling `runbook.run()` twice.

Manifest: `[[job]] name = "nightly"  runbook = "jobs.nightly:nightly"`.
`operonx-run nightly` runs it; `--list` prints the tree.

**Tracing, decided.** Two layers that never mix:

```
 records  (jobs/…)                      traces  (Operon runs, as today)
 ─────────────────────────────          ────────────────────────────────
 jobs/nightly/<run>/run.json            one trace per graph run:
   tree, status per node, timings         per_item → N traces
 jobs/extract/<run>/items.jsonl           stream   → 1 trace
   key, status, error, trace_id  ───────► tagged job=extract job_run=<id> key=<key>
```

A runbook or a job is never a span. A trace belongs to one graph run,
exactly as now; the job adds three tags so Langfuse can filter one job's
traces, and the record holds the trace id per item so the studio can link
from an item row to its trace. No consumer changes, no nesting.

## 6. Source, sink, record

**Sources and sinks are resources.** resources.yaml declares what a
project reaches out to; a table and a folder are that. The manifest stays
inbound: what starts work.

```yaml
source:
  calls_today: {type: jsonl, path: data/calls.jsonl}
  from_db:     {type: sql,   resource: "sql:warehouse", query: "select …"}
  by_hand:     {type: python, entry: "feeds:calls"}          # any iterable
sink:
  scores:      {type: jsonl, path: out/scores.jsonl}
  to_db:       {type: sql,   resource: "sql:warehouse", table: scores}
```

Phase 1 ships `jsonl`, `csv` and `python`. `sql` and `queue` come later
behind the same two protocols:

```python
class Source(Protocol):
    def items(self) -> AsyncIterator[Any]: ...
class Sink(Protocol):
    async def write(self, key: str, item: Any) -> None: ...
    async def close(self) -> None: ...
```

**The record is what makes a job more than a for-loop.** One directory
per job run, next to traces:

```
jobs/score_calls/2026-09-24T02-00-03/
  run.json        started, ended, status, counts {ok, failed, skipped}, sink
  items.jsonl     {key, status, error, trace_id, ms}   — one line per item
```

Resume reads `items.jsonl` of the last run and feeds only keys not `ok`.
The studio's Jobs tab reads the same two files.

## 7. Folder structure after

```
operonx/
  core/
    manifest.py            + JobSpec, [[job]] parsing, `jobs` on Manifest
    serve/                 unchanged; jobs import Session/ingress/egress from here
    jobs/                  NEW
      __init__.py          Job, JobRun, run_job
      job.py               the Job class; from_spec(JobSpec)
      runbook.py           Runbook, Sequential, Parallel, `>>`; the tree runner
      session.py           JobSession(BoundedSession): recv←source, send→sink
      runner.py            per_item / stream loops; concurrency; on_error; resume
      record.py            run.json / items.jsonl; JobRun view over them
      sources.py           jsonl, csv, python   (Source protocol)
      sinks.py             jsonl, csv, python   (Sink protocol)
  providers/
    registry/…             + "source" and "sink" resource categories
  cli/
    run.py                 NEW  operonx-run  (manual trigger, --resume, --list)
examples/python/
  ex17_jobs/               NEW  the toy project: one per_item job, one stream job,
                                one runbook of four jobs; the gate for every phase
tests/internal/core/jobs/  NEW  one file per module above
docs/JOB_PLAN.md           this file
```

Unchanged: the engine, the ops, tracing, `[[serve]]`. Jobs sit beside
serve; the shared pieces are the session protocol and the two door ops.

## 8. Decided

1. **Job is not serve.** Own `[[job]]` table, own package, own CLI.
2. **Scheduling is declarative in phase 1.** `schedule` is parsed, shown
   by `--list` and the studio, and executed by the deployment's cron
   calling `operonx-run`. An in-process scheduler is ~100 lines with
   `croniter`; the cost is a process that must stay up plus overlap and
   missed-run rules. Nothing here blocks adding it as `operonx-run
   --daemon` later.
3. **Identity is declared by the job** (`key = "call_id"`), read from the
   item; a job without `key` gets a random id per item and cannot resume.
4. **Composition is a Runbook** (§5): Sequential / Parallel / `>>`, run by
   asyncio above the engine. Never a graph of jobs. Jobs and runbooks are
   records, never spans.
5. **First user is a toy project**, `examples/python/ex17_jobs`, not the
   Analyze repo.

## 9. Open

- `engine.batch()`: keep as the no-record convenience, or make it a thin
  `Job` with a python source and a list sink. Lean: thin Job, one loop.
- HTTP trigger: a `[[serve]]` http entry that *starts* a job and returns
  its run id. Cheap once jobs exist; not phase 1.
- `Runbook` vs `JobFlow` as the name: runbook is the operator's word for
  "the ordered jobs I run"; flow already means a graph in the studio.

## 10. Phases

| # | Work                                                                  | Gate (all in ex17_jobs)                                                                 |
|---|-----------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| 1 | `Job`, `JobSession`, per_item runner, jsonl/csv/python, record, `operonx-run` | 3-item jsonl → graph → jsonl, one item failing: record says 2 ok 1 failed; `--resume` runs only that one |
| 2 | `[[job]]` in manifest; `stream` mode; `on_error=retry`; trace tags      | the same graph runs as `[[serve]]` http and as `[[job]]` unchanged; stream mode yields one trace |
| 3 | `Runbook`, `Sequential`, `Parallel`, `>>`; `runbook =` in manifest      | `nightly` runs embed and score in parallel, cluster after embed; run.json shows the tree with per-node status; each job has its record; no new spans anywhere |
| 4 | Studio Jobs tab; `sql` source/sink; HTTP trigger; `--daemon` schedule  | each if wanted                                                                          |

## 11. Log

- **2026-09-24 — phase 1.** `operonx/core/jobs/` (`Job`, `JobSession`,
  per_item runner, record, jsonl/csv/python sources and sinks as
  `source:`/`sink:` resource categories), `operonx-run`, `ex17_jobs`.
  Two things learned building it: an op that raises does not raise out
  of the run — the engine records it on the trace and drains — so "did
  the item fail" is read from the trace's node statuses; and an item that
  runs cleanly but never reaches `egress` is its own status, `empty`, not
  `ok`. Added beyond the plan: `item_input=` for a graph with no doors
  (the `engine.batch()` shape, now with a record). **Gate: ex17 first run
  `ok=2 failed=1` naming `c3: scored: ValueError: empty transcript`;
  after the fix `--resume` gives `ok=1 skipped=2`; 76 new tests; studio
  extractor builds the example offline.**
