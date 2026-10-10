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

## One graph for jobs and services

A graph's **parameters are what the caller sends**; its **outputs are what
the caller gets back**. A job fills the parameters from each item, a
service from the request — write the graph once and every rung above runs
it unchanged.

```python file=scorer.py
from operonx import END, START, graph, op


@op
def score(id: str, text: str) -> dict:
    words = len(text.split())
    return {"id": id, "words": words, "verdict": "engaged" if words >= 5 else "brief"}


@op
def tally(results: list) -> dict:
    engaged = sum(r["verdict"] == "engaged" for r in results)
    return {"report": {"calls": len(results), "engaged": engaged}}


@graph
def score_flow(id, text):  # one call in: {"id", "text"}; out: {id, words, verdict}
    s = score(id=id, text=text)
    START >> s >> END


@graph
def report_flow(results):  # a job's `reduce`, run once over every result
    t = tally(results=results)
    START >> t >> END
```

The result of a run is what `Operon(g).run(...)` returns, without the `$`
keys. A graph that should answer with a flat object has its last op return
those fields at the top level, as `score` does.

### Doors: for streams

A run that handles many items over its life — a phone call, a chat socket —
reads them through **doors**: `ingress()` yields each item the client
sends, `egress(item=...)` sends one back, as many times as it likes.

```python file=echo.py
from operonx import END, START, graph, op
from operonx.app.serve import egress, ingress


@op
def shout(text: str) -> dict:
    return {"reply": text.upper()}


@graph
def echo_flow():
    src = ingress()  # one item per frame the client sends
    s = shout(text=src["item"])
    out = egress(item=s["reply"])  # one frame back per item
    START >> src >> s >> out >> END
```

`ingress()` is transient (each item is freed once used), so never
`.collect()` it. A `websocket` door needs this shape; `http`, `webhook`,
`schedule` and jobs take either. A graph with doors gets the item through
`ingress` and answers with what `egress` sent.

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
- Binding: a dict item fills the graph's parameters by name (a field the
  graph does not take is an error), `input="case"` hands the whole item to
  one parameter, and anything else goes to the graph's only free parameter.
  `inputs={...}` are fixed inputs for every item. A graph with doors takes
  the item through `ingress` instead.
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
`job.run()`. From cron or CI, `operonx run nightly` exits non-zero when a
step failed.

## A job on a clock

`schedule=` runs a job (or a job of steps) by itself, inside `operonx serve`
on the schedule's port, beside that port's services:

```python
from operonx.app import Application, schedule
from operonx.app.jobs import Job

from scorer import score_flow

CALLS = [{"id": "c1", "text": "yes I can talk now"}]

APP = Application(
    "scorer",
    jobs=[Job("morning", graph=score_flow, items=CALLS, key="id", schedule=schedule(at="07:00"))],
)
job = next(j for j in APP.describe()["jobs"] if j["name"] == "morning")
assert job["schedule"] == {"at": "07:00", "port": 8000}
assert APP.run_sync("morning").status == "ok"  # on demand too: `operonx run morning`
```

Each tick is a fresh run with per-item records, and its `run.json` says
`"trigger": {"by": "schedule", "slot", "at"}`. A tick that lands while the
last run is still going is skipped and counted; a failed run does not stop
the clock. With `queue=` on the listener, each tick fires on one replica.
A graph that should run per tick with no items is a `Service` on
`schedule(...)` instead.

## Service — behind HTTP or a websocket

```python
from starlette.testclient import TestClient

from operonx.app import Application, Service, http, websocket

from echo import echo_flow
from scorer import score_flow

score_service = Service("score", http("POST", "/score", port=8017), graph=score_flow)
echo_service = Service("echo", websocket("/echo", port=8017), graph=echo_flow, max_inflight=8)

with TestClient(Application("demo", services=[score_service, echo_service]).asgi()) as client:
    reply = client.post("/score", json={"id": "c1", "text": "busy"})
    assert reply.json() == {"id": "c1", "words": 1, "verdict": "brief"}
    assert client.post("/score", json={"id": "c1"}).status_code == 400  # `text` is missing
    with client.websocket_connect("/echo") as ws:
        ws.send_json("hi")
        assert ws.receive_text() == "HI"  # a str item goes out as a text frame
```

- `http(...)`: the JSON body and the query string fill the graph's
  parameters; the reply is the run's outputs, with its `x-operonx-trace-id`
  header. A parameter left out takes the `@graph`'s default. Refused with
  `400 {"error", "endpoint", "field"}` and no run: a body that is not JSON,
  a field that is not a parameter, a required parameter nobody gave, a name
  in both the query and the body. A body that is not an object goes to the
  graph's one required parameter. A run that fails answers `500` with its
  `trace_id`, never the error text.
- With doors, the body is the one ingress item (an empty body is `None`)
  and the reply is the egress item(s).
- `websocket(path, port=...)` needs `max_inflight=N` and a graph with
  doors: every frame is an item, every egress item is sent at once. Text frames are JSON, decoded
  the same way as an HTTP body (bytes frames stay bytes); a frame that is
  not JSON gets `{"error": ...}` back and never reaches the graph.
- `codec="text"` on `http`, `websocket` or `webhook` passes text through
  instead of decoding JSON. `codec="json"` is the default.
- `webhook(path, port=...)`: for events nobody waits on (a new email, a
  Slack message). The POST is answered `202 {"accepted": true, "run_id": ...}`
  at once and the run goes on in the background, traced. `max_inflight=N`
  answers `429` beyond N pending runs.
- `schedule(every="5m")` or `schedule(at="08:00", port=...)`: a clock that
  starts a run per tick, inside the server on that port. A graph with
  parameters named `tick` or `at` gets them (`n`, the ISO time); with doors,
  the ingress item is `{"tick": n, "at": ...}`. A tick that lands while the last run is still
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
  (`RunRequest(inputs={...})`, or `None` to refuse); the body fills the
  parameters it leaves. Without it the query string becomes the inputs
  (`trace_id`, `callback`, `thread_id` are the door's own and are not).
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
