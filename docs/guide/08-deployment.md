# Deployment

A project is deployed from its application: what listens, what runs on
a schedule, on which graph. Declare it in Python where a reader sees
the graph, the door and the bound objects together, and let
`operonx.toml` point at it:

```python
# app/main.py
from operonx.app import Application, Service, asgi, env, http, websocket

APP = Application(
    "callbot",
    services=[
        Service("call", websocket("/ws/call", port=env("WS_PORT", 9922), workers=4),
                graph=ws_callbot_pipeline,             # (call_id, customer_id, …): the query
                trace_id="call_id", max_inflight=4000,
                on_startup=[startup.warmup]),          # in each call worker, nowhere else
        Service("admin", asgi("/", port=env("HTTP_PORT", 9923)), app=admin.app),
    ],
    jobs=[nightly],
)

if __name__ == "__main__":
    APP.serve()                                        # every listener, in the shape it declares
```

```toml
# operonx.toml — what is not code
[project]
name = "callbot"
src  = ["src"]
app  = "app.main:APP"
```

`operonx serve`, `operonx run` and the studio find the file, then read
the object.

- **The graph's signature is the door's contract.** The connection's
  query (and a one-shot door's body) fills the graph's parameters; a
  missing required one is refused at the door, before a websocket's
  handshake, naming the parameter. There is no second list to keep in
  step. What a run opens (a call's per-call objects, a counter) is its
  first op; what it must close however it ended (the call's record) is
  `END >> op`. `on_session` / `on_close` are deprecated (removed in 2.0).
- **A door op says what it is.** `@op(door="ingress")` /
  `@op(door="egress")` on an op that reads or writes the session; the
  built-in `ingress()` / `egress()` declare it the same way. The studio
  draws them as doors; the service names none.
- **The process is shaped by the listeners.** `workers=4` on a listener
  runs it as four worker processes (each loads the application again from
  `operonx.toml`, so it compiles its own engines); a listener with one
  worker runs in the main process. `on_startup=` on a service runs in
  that listener's workers only — the model warmed for the call workers is
  not warmed again for the admin port. `on_startup=` on the
  `Application` runs for every listener.
- **What every response passes through is middleware.** `middleware=[...]`
  on the `Application` (or `[project] middleware` in `operonx.toml`):
  ASGI middleware, outermost first, each called `entry(app=app)` — a
  Starlette middleware class or a function returning the wrapped app. It
  wraps each listener whole, the services' routes and a mounted app alike,
  so a security header cannot miss a door.
- **A pooled listener's connections are spread evenly.** Each worker
  accepts only while it holds no more connections than the least loaded
  one, so 20 calls on 4 workers are 5 each (the kernel alone put 10 on one
  and none on another). Size `workers` by what one worker holds: a run's
  trace says which worker served it and how loaded it was
  (`metadata["worker"]`, guide 08 of the package). `OPERONX_SERVE_BALANCE=off`
  turns it off.

`[[serve]]` blocks in `operonx.toml` are **deprecated** (1.19) and removed
in 2.0: loading one warns once, naming the file. Move each block to a
`Service(...)` in `app/main.py` and set `[project] app`. `[project]`,
`[resources]`, `[tracing]`, `[retry]`, `[studio]` and `[[graph]]` stay in the file.

A scheduled job runs inside the server too: `Job(..., schedule=schedule(at="07:00",
port=...))` puts its clock on that port (see [Jobs and runbooks](10-jobs.md)).

```bash
pip install "operonx[serve]"
operonx serve --list          # what would run, and where
operonx serve                 # every listener, each with its workers
operonx run --list            # every job of the application (declared in Python)
operonx run nightly           # what the deployment's cron calls
```

The same from Python — for a process that runs uvicorn itself, a test
client, or a tool that wants the three lists:

```python
from operonx.app import Application

app = Application.find()             # the nearest operonx.toml
app.serve(only=["call"])             # block, serving
asgi = app.asgi(port=9923)           # one listener's ASGI app
app.run_sync("nightly")              # a job or runbook, with its record
app.describe()                       # graphs, services, jobs — plain data
```

A request–reply graph needs no door ops: on `http`, `webhook` and
`schedule` its parameters are filled from the body and query, and its
outputs are the reply. `ingress` and `egress` are the door ops of a graph
that handles many items in one run (a call on a websocket); they read the
session the transport minted, so the same graph is served on one day and
run over a file by a job on the next. See [Jobs and runbooks](10-jobs.md).

Every door reads what its caller sends the same way. An HTTP body, a
webhook body and a websocket text frame are JSON by default, so one graph
served on HTTP and on a websocket gets the same item for the same
payload; a websocket bytes frame stays bytes. A body that is not JSON is
answered `400` before a run starts, and a websocket frame that is not
JSON gets `{"error": ...}` back. `codec = "text"` (or
`websocket(..., codec="text")`) passes text through instead. HTTP and
webhook replies carry the run's `x-operonx-trace-id` header.

## One door, several graphs: variants

A door whose graph differs by caller — one turn graph per agent, one
pipeline per tenant tier — declares the variants instead of threading a
"which am I" input through every op or declaring one service per kind:

```python
Service(
    "call",
    websocket("/ws/call", port=9922),
    graph=call_flow,                       # a module-level @graph
    max_inflight=4000,
    variants={
        "educa_hr": {"turn": educa_hr_turn, "config": "agents/educa_hr/prompts.yaml"},
        "ahamove": {"turn": ahamove_turn, "config": "agents/ahamove/prompts.yaml"},
    },
)
```

Each variant fixes some of the `@graph`'s parameters at build time. A bound
value that reads as `module:attr` is loaded, anything else is a literal. `operonx serve` compiles one engine
per variant at boot, and the connection picks one with `?variant=`
(`/ws/call?variant=educa_hr&…`), before its parameters are bound: variants
may take different ones.

A session that names no variant gets the first one declared. A name the
door does not declare is refused at the door, with the declared names in
the log — no run is minted. `Application.graphs` lists one
graph per variant (`build[educa_hr]`, `build[ahamove]`) under the one
service, each compilable on its own. Worked example:
`examples/python/ex18_variants` (declared in Python, `app.py`).

## A `src/` layout

A project that keeps its packages under `src/` says so once, and every
`module:attr` in the manifest resolves from there:

```toml
[project]
src = ["src"]        # import roots, relative to the manifest; default ["."]
```

`Application.bootstrap()` puts each root on `sys.path`; `operonx serve`,
`operonx run` and the studio all go through it.

## Durable runs

One table in `operonx.toml` journals every step of the project's jobs and
request–reply services:

```toml
[durable]
journal = "sqlite:.operonx/journal.db"   # relative to the project; or "memory"
# durability = "async"                   # "sync" (each step before the next) or "exit"
```

What a journalled run can do that a plain one cannot:

- **Survive a crash.** `operonx run NAME --resume` finishes the items the
  last run left mid-way or failed, from the op where each stopped.
- **Wait for a person.** An `InterruptOp` parks the run: the reply is
  `{"run_id", "interrupted": [{"id", "op", "payload"}]}`, and
  `POST <path>/resume` with `{"run_id", "answers": {"<id>": value}}`
  continues it, on any worker that opens the same journal.
- **Run again from its failure.** A failed run, after the fix, runs again
  from its failed op (`engine.resume(run_id, retry_failed=True)`); what
  ended before it is not done again.
- **Fork from a step.** `engine.fork(run_id, at="op", outputs={...})` runs
  it again as if one op had answered otherwise; the original is unchanged.

The studio does the last three from a run's page: **Runs → Waiting for an
answer** answers a parked run (Approve, Deny, or any JSON), a failed run
has **Retry from the failure**, and every step has **What if → Change this
output and continue**. Compare runs then shows what changed, step by step.

Each resumed, answered, retried or forked run keeps one trace, the whole
run, under its own id.

**Cost:** an 8-op run takes about 3.8 ms with a SQLite journal against
1.4 ms without; at 400 runs at once, about 3.7 ms against 1.2 ms. A graph
with a door (a websocket call) is never journalled: its items come from a
live connection. SQLite is one machine; runs that must move between
machines need a shared journal (Postgres, planned).

## Configuration

The server honours the standard Operonx setup:

- `.env` for credentials.
- `resources.yaml` for model and consumer configs.
- `bootstrap()` at startup.

For Kubernetes / containerised deployments, mount `resources.yaml` and
provide credentials through the platform's secret store rather than a
file-based `.env`.

## Production checklist

- Configure a persistent path for the local trace consumer (or skip it
  and use Langfuse / OTEL).
- Give every stream listener a `max_inflight`; the manifest refuses one without.
- Mount your health and admin routes as a `kind = "asgi"` entry, beside the graphs.
- Pin model versions in `resources.yaml` — never reference `latest`.
- Watch the [Tracing](07-tracing.md) backend for token-cost and latency
  drift.

## Where to go next

- [Architecture overview](../architecture/overview.md) — internals.
