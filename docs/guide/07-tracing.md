# Tracing

Every run records itself. While a graph runs, the engine writes each op
execution — its inputs, its outputs, its time, its cost — into a
`WorkflowTrace` on the handle. Nothing in your graph asks for this, and
nothing in your graph changes when you change where it goes.

Where it goes is the job of **consumers**. A consumer reads the finished
trace, after the run, in a worker thread, and turns it into something: a
directory of files, a Langfuse trace, a row in a run store. You name the
consumers; the engine calls every one of them for every run.

## Consumers are resources

Declare them in `resources.yaml`, like any other resource, and refer to
them by key:

```yaml
trace_local:
  default: {}                  # every field at its default: <project>/.operonx/runs

langfuse:
  default:
    public_key: ${LANGFUSE_PUBLIC_KEY}
    secret_key: ${LANGFUSE_SECRET_KEY}
    host: ${LANGFUSE_HOST}

trace_langfuse:
  default:
    client_resource: langfuse:default

run_store:
  default:
    backend: files             # see Runs: stores, retention and alerts
```

| Category | What it writes |
|---|---|
| `trace_local` | One directory per run: `nodes.jsonl` (every execution), `view.txt` (a readable timeline), `media/` (large payloads, content-addressed) |
| `trace_langfuse` | A Langfuse trace, one span per op, LLM generations with their model, tokens and cost (`pip install "operonx[langfuse]"`) |
| `run_store` | A run store: the run in full plus its summary, queryable later ([Runs](11-runs.md)) |
| `trace_clickhouse` | The ClickHouse run store, written from a background queue so a run never waits on the database; blobs stored once, typed from their bytes ([Runs → ClickHouse](11-runs.md#clickhouse); `pip install "operonx[clickhouse]"`) |

## Wiring them to a run

On one engine:

```python
from operonx import Operon, bootstrap

bootstrap(resources="resources.yaml")
engine = Operon(graph, trace=["trace_local:default", "trace_langfuse:default"])
```

A key, a consumer object, or a list of both. `trace=[]` records nothing,
and so does leaving `trace=` out.

| `trace=` | What a run records to |
|---|---|
| unset, or `[]` | nothing |
| `"local"` | the built-in local consumer: `<project>/.operonx/runs` inside a project (an `operonx.toml` at or above the working directory), else `/tmp/operonx_traces` |
| `"project"` | the project's own sinks: `[tracing] sinks`, else `[project] trace`, else `"local"`; `sinks = []` records nothing. Outside a project it raises |
| `"trace_langfuse:default"` | a resource key, through the hub (`operonx.bootstrap()` first) |
| a `Consumer` | that object |

A script run from anywhere inside a project, with `trace="project"`,
goes where the project's services and jobs go, and the studio lists it
under **Ad hoc**, named after its graph. `"project"` reads
`operonx.toml`, not an `Application(trace=...)` written in Python: it
would have to import the application. Every resource key it names is
checked when the engine is built.

A run started inside an op of a running engine — a helper graph a
service op runs per call — is part of that op's run. It records into its
own `handle.trace`, and its consumers are not called, so it files no
second trace.

For a whole application, name them once. Every service and job that does
not name its own inherits them:

```python
APP = Application(
    "callbot",
    services=[Service("call", websocket("/ws/call"), graph=call_graph)],
    jobs=[Job("score_calls", graph=score_call, items="data/calls.jsonl")],
    trace=["trace_local:default", "trace_langfuse:default"],
)
```

A service or job with its own `trace=` keeps it; `trace=[]` on one of
them still means "trace nothing". A job with no consumers anywhere records
locally anyway, so its item records never point at traces that were not
written. A service with none is not traced.

## Switching sinks in `operonx.toml`

The operator's switch is `[tracing]` in `operonx.toml`: the one place that
says which sinks are on. How to reach each sink stays in `resources.yaml`.

```toml
[tracing]
sinks = ["local", "trace_langfuse:edupia", "trace_clickhouse:default"]   # every run goes to all of them

[tracing.services.call]            # one service, overridden
sinks = ["local", "trace_langfuse:edupia"]

[tracing.jobs.backfill_call_logs]  # one job, overridden
sinks = []                         # this job is not traced
```

`"local"` is the built-in local consumer, the one a job records to when
nothing is configured (`<project>/.operonx/runs`); it needs no entry in
`resources.yaml`. Any other entry is a resource key, resolved through the
hub exactly like a `trace=[...]` entry.

The most specific setting wins:

| Level | Where | |
|---|---|---|
| 1 | `[tracing.services.<name>]` / `[tracing.jobs.<name>]` | the operator, for one service or job |
| 2 | `Service(trace=...)` / `Job(trace=...)`, or `trace =` on a (deprecated) `[[serve]]` block | the code, for one service or job |
| 3 | `[tracing] sinks` | the operator, for the project |
| 4 | `Application(trace=...)` (or `[project] trace`) | the code, for the project |
| 5 | the built-in default | a job records locally; a service is not traced |

An explicit `sinks = []` (or `trace=[]`) means "not traced" at its level;
it does not fall through. A steps job's entry, `[tracing.jobs.<name>]`,
reaches each of its steps; a step can still be named on its own.

`[project] trace` and `[tracing] sinks` cannot both be set: they sit at
different levels (`Application(trace=...)` beats the first and loses to
the second), so one file holding both would leave a reader guessing which
applies. Move the list to `[tracing] sinks`.

What is checked, and when:

- **At load**: an unknown key in `[tracing]`, a `sinks` that is not a list,
  an entry that is neither `"local"` nor `category:name`, a sink listed
  twice, and a `[tracing.services.<name>]` / `[tracing.jobs.<name>]` that
  names no service or job. Each error names the file and the key.
- **When a service or job starts**: every sink is in `resources.yaml`. A
  missing one stops the start, naming the key, the level that chose it and
  who uses it — never a run that quietly goes untraced.

`operonx-serve --list` and `operonx-run --list` print each service's and
job's sinks and the level they came from, and `Application.describe()`
carries them (`sinks`, `sinks_from`) for the studio:

```text
callbot
  0.0.0.0:8000
    call           websocket  /ws/call         -> pipeline.graph:call  [per_connection max_inflight=4000]
      sinks: local, trace_langfuse:edupia  ([tracing.services.call])
```

Every sink of one run receives the same `WorkflowTrace`, so the same trace
id — including one a caller passed as `?trace_id=` to a webhook or an http
door.

## Every run knows where it came from

A run carries its **origin** in its metadata, and every consumer reads
it: the local consumer files the run under it, Langfuse receives it as
tags, a run store indexes it.

| Origin | Set by | Also carries |
|---|---|---|
| `service` | a served door | `service`, `transport`, `variant` |
| `job` | a job's item | `job`, `job_run`, `key` (and `runbook`, `runbook_run` when run as a step) |
| `eval` | an eval's case | as a job |
| `playground` | the studio's playground | `service`, the session's script |
| `adhoc` | anything else: a test, a script | — |

`Application.bootstrap()` also stamps the project and its git commit
(with a dirty flag) once per process; every trace carries them, so a run
says which code produced it.

## Where local runs go

`trace_local` files runs by origin:

```text
<root>/
  services/<service>/<day>/<run>/
  jobs/<job>/<job run>/<run>/
  evals/<eval>/<job run>/<run>/
  playground/<day>/<run>/
  adhoc/<workflow>/<day>/<run>/
```

The root is `root:` when set (a relative one resolves against the
project), else `$OPERONX_RUNS_DIR`, else `<project>/.operonx/runs`, else
`/tmp/operonx_traces`. The project is the one an `Application` bootstrapped,
else the nearest `operonx.toml` at or above the working directory. Days are UTC. `layout: flat` keeps the pre-1.9
shape, `<root>/<run>/`; any other string is a template over `{origin}`,
`{name}`, `{group}`, `{day}`, `{trace_id}` and the run's metadata keys.

## Writing a consumer

Subclass `Consumer` and override `consume`:

```python
from operonx.telemetry.consumer import Consumer

class SlowOps(Consumer):
    def consume(self, trace):
        slow = [n.op_name for n in trace.nodes if n.end_time - n.start_time > 1.0]  # seconds
        if slow:
            print(trace.trace_id, "slow:", slow)

engine = Operon(graph, trace=["trace_local:default", SlowOps()])
```

The base offers `sanitize`, `offload_media` and `truncate` for payloads
that must survive JSON or a size limit. See [operonx.telemetry](../api/telemetry.md).

A consumer that also overrides `on_start(trace)` or `on_execution(trace,
execution)` sees the run as it goes: the engine calls them as the run
starts and as each execution lands, on the event loop — queue the work and
return. The ClickHouse and SQL run stores do this to list a run as
`running` before it ends.

## Where to go next

- Keep, query and alert on runs: [Runs: stores, retention and alerts](11-runs.md).
- Stream traces alongside frames: [Streaming](06-streaming.md).
- Inspect resource configs: [Resource hub](../architecture/resource-hub.md).
