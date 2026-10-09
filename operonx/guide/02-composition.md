# 2. The composition ladder

One example climbs every rung:

| Rung | What it adds | Reach for it when |
|---|---|---|
| **op** | one step of logic | always: it is where the code lives |
| **operon** — a `@graph` run by `Operon` | ops wired in order, run as one unit | a script, a test, or the thing the rungs above run |
| **Job** | runs the graph once per item, keeps every result in a record; `reduce` and `steps` | batches, backfills, nightly work |
| **Service** | puts the graph behind HTTP or a websocket | a client calls it |
| **Application** | all services and jobs of a product, their resources and tracing | the product itself |
| **`operonx.toml`** | tells the CLIs where the application is | serving and running it |

## op and operon

```python
import asyncio

from operonx import END, START, Operon, graph, op


@op
def score(call: dict) -> dict:
    words = len(call["text"].split())
    return {
        "result": {
            "id": call["id"],
            "words": words,
            "verdict": "engaged" if words >= 5 else "brief",
        }
    }


@graph
def score_one(call):
    s = score(call=call)
    START >> s >> END


async def main():
    engine = Operon(score_one, params={"call": None})
    out = await engine.run(inputs={"call": {"id": "c1", "text": "yes I can talk now"}})
    assert out["result"] == {"id": "c1", "words": 5, "verdict": "engaged"}


asyncio.run(main())
```

### An op that runs another op or graph: `invoke`

Calling an `@op` function builds a graph node; it does not run the function. Inside a running op,
that call raises `TypeError`. To run an op or a graph from an op body (an agent's tool, a helper
graph per call), `await invoke(target, **inputs)`:

```python
import asyncio

from operonx import END, START, Operon, graph, invoke, op


@op
def lookup(order_id: str) -> dict:
    return {"status": f"{order_id}: shipped"}


@op
async def answer(order_id: str) -> dict:
    found = await invoke(lookup, order_id=order_id)  # runs it; a @graph works the same way
    return {"text": f"Order {found['status']}"}


@graph
def support(order_id):
    a = answer(order_id=order_id)
    START >> a >> END


async def main():
    handle = Operon(support, params={"order_id": None}).start({"order_id": "A1"})
    assert (await handle.result())["text"] == "Order A1: shipped"
    names = [n.op_full_name for n in handle.trace.nodes]
    # the run invoke started is a step of this one, recorded under the op that started it
    assert names[:2] == ["support.a.lookup.lookup", "support.a.lookup"]


asyncio.run(main())
```

- The target runs as a run of its own graph (a bare `@op` gets a graph of one node), built once and
  reused. Its records join the caller's trace under the calling op, or under the `child()` step it
  ran in, so the studio opens that step onto the graph. A plain `Operon(g).run()` inside an op body
  nests the same way.
- A failure inside it raises `OpFailed` in the caller. It fails the caller's run only if the
  caller lets it through.

## Doors: one graph for jobs and services

Jobs and services feed a graph through **doors**: `ingress()` yields each
incoming item, `egress(item=...)` sends a result out. Write the graph once;
every rung above runs it unchanged.

```python file=scorer.py
from operonx import END, START, graph, op
from operonx.app.serve import egress, ingress


@op
def score(call: dict) -> dict:
    words = len(call["text"].split())
    return {
        "result": {
            "id": call["id"],
            "words": words,
            "verdict": "engaged" if words >= 5 else "brief",
        }
    }


@op
def tally(results: list) -> dict:
    engaged = sum(r["verdict"] == "engaged" for r in results)
    return {"report": {"calls": len(results), "engaged": engaged}}


@graph
def score_flow():
    src = ingress()  # one item per call, from whoever runs the graph
    s = score(call=src["item"])
    out = egress(item=s["result"])  # the item handed back
    START >> src >> s >> out >> END


@graph
def report_flow(results):  # no doors: a job's `reduce`, run once over every result
    t = tally(results=results)
    START >> t >> END
```

`ingress()` is transient (each item is freed once used), so never
`.collect()` it; a job that needs every result at once gives them to a
`reduce` graph.

## Job — one run per item

```python
import asyncio

from operonx.app.jobs import Job

from scorer import report_flow, score_flow

CALLS = [{"id": "c1", "text": "yes I can talk now"}, {"id": "c2", "text": "busy"}]


async def main():
    job = Job("score_calls", graph=score_flow, items=CALLS, key="id", reduce=report_flow)
    run = await job.run()  # run.status, run.counts, run.items — and a record on disk
    assert run.status == "ok" and run.counts["ok"] == 2
    assert [run.results[k]["verdict"] for k in ("c1", "c2")] == ["engaged", "brief"]
    assert run.reduced == {"report": {"calls": 2, "engaged": 1}}


asyncio.run(main())
```

- `items`: a list, a function that yields them (called on every run: your
  own loader, a database query), or a `.jsonl` path. Nothing else: a CSV or
  a folder is a two-line generator.
- Binding: a graph with doors takes the item through `ingress`. Otherwise a
  dict item fills the graph's parameters by name (a field the graph does
  not take is an error), `input="case"` hands the whole item to one
  parameter, and anything else goes to the graph's only free parameter.
  `inputs={...}` are fixed inputs for every item.
- Every result is kept in the record (`run.results`, `results.jsonl`).
  `output="out/x.jsonl"` or `output=fn(key, result)` also exports each
  success as it finishes.
- `reduce=graph` runs once after the last item with `results`: every
  successful result, in key order, including the ones an earlier run kept.
- `key` makes items resumable: `job.run(resume=True)` runs only the keys
  that did not finish.
- `on_error="skip"` (default) or `"stop"`; `retry=Retry(max_attempts=3)` and
  `timeout=30` per item. A timed-out item keeps its `trace_id`.
- A failed item fails the run. `fail_run=False` when a failed item is
  an outcome (a batch scorer writing an error file for it): the run, and the
  step after it, stay `ok`.
- Runs are recorded under `.operonx/jobs/<job>/<run>/` in the project
  (`[jobs] dir` in `operonx.toml`, or `record_dir=`, to move them).
- `job.run_sync()` from plain code; `job.main()` turns it into a CLI.

## Steps — jobs in order, as one command

```python
import asyncio

from operonx.app.jobs import Job

from scorer import score_flow

CALLS = [{"id": "c1", "text": "yes I can talk now"}]
LATER = [{"id": "c2", "text": "busy"}]


async def main():
    today = Job("today", graph=score_flow, items=CALLS, key="id")
    backlog = Job("backlog", graph=score_flow, items=LATER, key="id")
    nightly = Job("nightly", steps=[today, backlog])
    run = await nightly.run()  # the first step that is not ok stops the rest
    assert run.status == "ok" and [s["name"] for s in run.meta["steps"]] == ["today", "backlog"]


asyncio.run(main())
```

Each step keeps its own record; `--resume` reaches every step. Anything
more (a condition, a loop over days) is a Python function calling
`job.run()`. On a clock, cron or CI runs `operonx run nightly`; it exits
non-zero when a step failed.

## Service — behind HTTP or a websocket

```python
from operonx.app import Service, http

from scorer import score_flow

score_service = Service("score", http("POST", "/score", port=8017), graph=score_flow)
```

- `http(...)`: the JSON body is the one ingress item; the reply is the
  egress item(s), sent when the run ends, with the run's
  `x-operonx-trace-id` header. A body that is not JSON is answered `400`
  and starts no run; an empty body is the item `None`.
- `websocket(path, port=...)` needs `max_inflight=N`: every frame is an
  item, every egress item is sent at once. Text frames are JSON, decoded
  the same way as an HTTP body (bytes frames stay bytes); a frame that is
  not JSON gets `{"error": ...}` back and never reaches the graph.
- `codec="text"` on `http`, `websocket` or `webhook` passes text through
  instead of decoding JSON. `codec="json"` is the default.
- `webhook(path, port=...)`: for events nobody waits on (a new email, a
  Slack message). The POST is answered `202 {"accepted": true, "run_id": ...}`
  at once and the run goes on in the background, traced. `max_inflight=N`
  answers `429` beyond N pending runs.
- `schedule(every="5m")` or `schedule(at="08:00", port=...)`: a clock that
  starts a run per tick, inside the server on that port; the ingress item is
  `{"tick": n, "at": ...}`. A tick that lands while the last run is still
  going is skipped and counted; a failing run does not stop the clock.
- `queue="runs.db"` (or `queue={url="postgresql://…"}` in `operonx.toml`)
  on a webhook or schedule makes it durable: a webhook writes the event to
  the queue **before** its `202`, and every replica claims events from it.
  A replica that dies mid-run stops renewing its lease (`lease=30` seconds);
  another replica runs the event again with the same `run_id` (at least
  once — make side effects idempotent on `run_context().idempotency_key`).
  A schedule's ticks line up on the clock and each fires on one replica.
  Rows of one `thread_id` run one at a time, in order. Inspect it with
  `SqliteQueue("runs.db").items("<service>")`.
- On a queued webhook, `multitask=` says what a second message on a busy
  thread (`?thread_id=` or header `x-operonx-thread`) does: `"enqueue"`
  (default: waits its turn), `"reject"` (`409`), `"interrupt"` (stops the
  running run, keeping what it did) or `"rollback"` (stops it, marked
  discarded). `callback_hosts=["hooks.example.com"]` lets a request add
  `?callback=https://hooks.example.com/…`; when the run ends that URL gets
  `{run_id, service, status, output, errors}` — from whichever replica ended
  it. A host not on the list is answered `400`.
- An `http` door streamed as server-sent events numbers each event (`id:`).
  A reader that dropped sends the same request with `?run_id=<id>&after_seq=N`
  (or `Last-Event-ID: N`) and gets the events after N, then the rest live.
  The events are kept 15 minutes after the run ends, on the replica that ran
  it (route reconnects to the same replica).
- `on_session=fn` turns the request into the graph's inputs
  (`RunRequest(inputs={...})`, or `None` to refuse). Without it the query
  string becomes the inputs.
- `variants={"formal": {"style": formal}, "casual": {"style": casual}}`
  compiles the door's module-level `@graph` once per variant, each with
  those parameters fixed; `on_session` picks one with
  `RunRequest(variant=...)`. A graph factory is refused (see 05).
- Also: `replay=True` (keep requests for replay), `key_ops=[...]`.

## Application and `operonx.toml`

The application lists everything the product runs; `operonx.toml` points
the CLIs at it.

```python file=app.py
from operonx.app import Application, Service, env, http
from operonx.app.jobs import Job

from scorer import score_flow

APP = Application(
    "scorer",
    services=[Service("score", http("POST", "/score", port=env("PORT", 8017)), graph=score_flow)],
    jobs=[
        Job(
            "score_calls",
            graph=score_flow,
            items="calls.jsonl",
            output="out/scored.jsonl",
            key="id",
        )
    ],
    trace=["trace_local:default"],  # every run recorded under .operonx/runs
)
```

```toml file=operonx.toml
[project]
name = "scorer"
app  = "app:APP"      # module:attribute of the Application

[resources]
overlay = "resources.yaml"
```

```yaml file=resources.yaml
trace_local:
  default: {}
```

```json file=calls.jsonl
{"id": "c1", "text": "yes I can talk now"}
{"id": "c2", "text": "busy"}
```

```bash run
operonx serve --list        # what would listen, and where
operonx run --list          # the jobs
operonx run score_calls     # run one; exits non-zero if it failed
```

`operonx serve` serves every service (`--only score` for one);
`operonx run NAME --resume` continues a job.

Test a service in-process, without a port:

```python
from starlette.testclient import TestClient

from operonx.app import Application

APP = Application.find(".")  # loads operonx.toml, then the app it names
with TestClient(APP.asgi()) as client:
    reply = client.post("/score", json={"id": "c9", "text": "call me back later please"}).json()
    assert reply == {"id": "c9", "words": 5, "verdict": "engaged"}
```

### Which sinks are on: `[tracing]`

`operonx.toml` is the operator's switch for tracing. `[tracing] sinks` sends
every run to all of them; `[tracing.services.<name>]` and
`[tracing.jobs.<name>]` override one service or job. `"local"` is the
built-in local consumer (`.operonx/runs`); any other entry is a resource
key from `resources.yaml`. `sinks = []` turns tracing off at that level.

```toml file=operonx.toml
[project]
name = "scorer"
app  = "app:APP"

[resources]
overlay = "resources.yaml"

[tracing]
sinks = ["local", "trace_local:default"]   # beats Application(trace=...)

[tracing.jobs.score_calls]
sinks = []                                  # this job is not traced
```

```python
from operonx.app import Application

APP = Application.find(".")
d = APP.describe()  # what `operonx-serve --list` / `operonx-run --list` print
score = next(s for s in d["services"] if s["name"] == "score")
assert (score["sinks"], score["sinks_from"]) == (["local", "trace_local:default"], "[tracing]")
job = next(j for j in d["jobs"] if j["name"] == "score_calls")
assert (job["sinks"], job["sinks_from"]) == ([], "[tracing.jobs.score_calls]")
```

```bash run
operonx-serve --list        # each service with "sinks: ...  (<where they came from>)"
operonx-run --list          # each job, the same
```

Most specific wins: `[tracing.<services|jobs>.<name>]`, then the service's or
job's own `trace=`, then `[tracing] sinks`, then `Application(trace=...)`,
then the default (a job records locally; a service is not traced). A
script's `Operon(flow, trace="project")` uses the same `[tracing]` sinks
(local when none are set); `Operon()` without `trace=` records nothing. Every
sink of one run gets the same trace id, including a caller's `?trace_id=`.
A typo in `[tracing]` fails at load; a sink missing from `resources.yaml`
fails when the service or job starts.
