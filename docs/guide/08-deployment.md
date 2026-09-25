# Deployment

A project is deployed from its manifest. `operonx.toml` declares what
listens and what runs on a schedule; `operonx-serve` and `operonx-run`
boot it, and `Application` is the same thing from Python.

```toml
[project]
name = "callbot"

[[serve]]
name    = "call"
kind    = "websocket"
path    = "/ws/call"
port    = "${WS_PORT:9922}"
graph   = "graphs.call.graph:ws_callbot_pipeline"
session = "per_connection"
max_inflight = 4000
on_session   = "app.serve.call_session:open_call"

[[serve]]
name  = "admin"
kind  = "asgi"
path  = "/"
port  = 9923
app   = "app.serve.admin:app"

[[job]]
name     = "nightly"
runbook  = "app.jobs.nightly:nightly"
schedule = "0 3 * * *"
```

```bash
pip install "operonx[serve]"
operonx-serve --list          # what would run, and where
operonx-serve                 # every listener, in one process
operonx-run --list            # every job, with its schedule
operonx-run nightly           # what the deployment's cron calls
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

`ingress` and `egress` are the two door ops a served graph uses; they
read the session the transport minted, so the same graph is served on
one day and run over a file by a job on the next. See
[Jobs and runbooks](10-jobs.md).

For a static-binary edge deployment, the
[operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs) crate ships
an equivalent Axum server (`operonx-serve` binary) that reads the same
`graph.json` and `resources.yaml`.

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
