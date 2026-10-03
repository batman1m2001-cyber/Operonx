# 2. The composition ladder

One example climbs every rung:

| Rung | What it adds | Reach for it when |
|---|---|---|
| **op** | one step of logic | always: it is where the code lives |
| **operon** — a `@graph` run by `Operon` | ops wired in order, run as one unit | a script, a test, or the thing the rungs above run |
| **Job** | runs the graph once per item of a source, writes a sink, keeps a record | batches, backfills, nightly work |
| **Runbook** | orders several jobs | "do A, then B and C" |
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

## Doors: one graph for jobs and services

Jobs and services feed a graph through **doors**: `ingress()` yields each
incoming item, `egress(item=...)` sends a result out. Write the graph once;
every rung above runs it unchanged.

```python file=scorer.py
import json

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
def summarize(path: str) -> dict:
    rows = [json.loads(line) for line in open(path)]
    return {"report": {"calls": len(rows), "engaged": sum(r["verdict"] == "engaged" for r in rows)}}


@graph
def score_flow():
    src = ingress()  # one item per call, from whoever runs the graph
    s = score(call=src["item"])
    out = egress(item=s["result"])  # the item handed back
    START >> src >> s >> out >> END


@graph
def report_flow(path):  # no doors: a job runs it once, and its outputs go to the sink
    s = summarize(path=path)
    START >> s >> END
```

`ingress()` is transient (each item is freed once used), so never
`.collect()` it; a job that needs every item at once reads the file the
previous job wrote.

## Job — one run per item

```python
import asyncio

from operonx.app.jobs import Job

from scorer import score_flow

CALLS = [{"id": "c1", "text": "yes I can talk now"}, {"id": "c2", "text": "busy"}]


async def main():
    got = []
    job = Job("score_calls", graph=score_flow, source=CALLS, sink=got, key="id")
    run = await job.run()  # run.status, run.counts, run.items — and a record on disk
    assert run.status == "ok" and run.counts["ok"] == 2
    assert [r["verdict"] for r in got] == ["engaged", "brief"]


asyncio.run(main())
```

- `source`: a list, a `.jsonl`/`.csv` path, a directory, a generator, or a
  `"source:name"` resource. `sink`: a list, a path, a function
  `fn(key, item)`, or `"sink:name"`.
- `key` makes items resumable: `job.run(resume=True)` skips done keys.
- `on_error`: `"skip"` (default), `"stop"`, `"retry:N"`, or `"record"`.
- `session="stream"` feeds every item through one run instead of one run each.
- `job.run_sync()` from plain code; `job.main()` turns it into a CLI.

## Runbook — jobs in order

One job's sink is the next one's source.

```python
import asyncio

from operonx.app.jobs import Job, Runbook

from scorer import report_flow, score_flow

CALLS = [{"id": "c1", "text": "yes I can talk now"}, {"id": "c2", "text": "busy"}]


async def main():
    reports = []
    scores = Job("scores", graph=score_flow, source=CALLS, sink="out/scores.jsonl", key="id")
    report = Job("report", graph=report_flow, inputs={"path": "out/scores.jsonl"}, sink=reports)
    with Runbook("nightly") as nightly:
        scores >> report  # one wire per line; `a >> [b, c]` fans out
    run = await nightly.run()
    assert run.status == "ok" and reports[0]["report"] == {"calls": 2, "engaged": 1}


asyncio.run(main())
```

## Service — behind HTTP or a websocket

```python
from operonx.app import Service, http

from scorer import score_flow

score_service = Service("score", http("POST", "/score", port=8017), graph=score_flow)
```

- `http(...)`: the JSON body is the one ingress item; the reply is the
  egress item(s), sent when the run ends.
- `websocket(path, port=...)` needs `max_inflight=N`: every frame is an
  item, every egress item is sent at once.
- `webhook(path, port=...)`: for events nobody waits on (a new email, a
  Slack message). The POST is answered `202 {"accepted": true, "run_id": ...}`
  at once and the run goes on in the background, traced. `max_inflight=N`
  answers `429` beyond N pending runs.
- `schedule(every="5m")` or `schedule(at="08:00", port=...)`: a clock that
  starts a run per tick, inside the server on that port; the ingress item is
  `{"tick": n, "at": ...}`. A tick that lands while the last run is still
  going is skipped and counted; a failing run does not stop the clock.
- `on_session=fn` turns the request into the graph's inputs
  (`RunRequest(inputs={...})`, or `None` to refuse). Without it the query
  string becomes the inputs.
- Also: `replay=True` (keep requests for replay), `key_ops=[...]`,
  `variants={...}`.

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
            "score_calls", graph=score_flow, source="calls.jsonl", sink="out/scored.jsonl", key="id"
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
operonx-serve --list        # what would listen, and where
operonx-run --list          # the jobs
operonx-run score_calls     # run one; exits non-zero if it failed
```

`operonx-serve` serves every service (`--only score` for one);
`operonx-run NAME --resume` continues a job.

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
then the default (a job records locally; a service is not traced). Every
sink of one run gets the same trace id, including a caller's `?trace_id=`.
A typo in `[tracing]` fails at load; a sink missing from `resources.yaml`
fails when the service or job starts.
