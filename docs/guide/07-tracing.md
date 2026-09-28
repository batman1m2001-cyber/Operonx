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

## Wiring them to a run

On one engine:

```python
from operonx import Operon, bootstrap

bootstrap(resources="resources.yaml")
engine = Operon(graph, trace=["trace_local:default", "trace_langfuse:default"])
```

A key, a consumer object, or a list of both. `trace=[]` records nothing.

For a whole application, name them once. Every service and job that does
not name its own inherits them:

```python
APP = Application(
    "callbot",
    services=[Service("call", websocket("/ws/call"), graph=call_graph)],
    jobs=[Job("score_calls", graph=score_call, source="data/calls.jsonl")],
    trace=["trace_local:default", "trace_langfuse:default"],
)
```

or, in `operonx.toml`, `[project] trace = ["trace_local:default"]`. A
service or job with its own `trace=` keeps it; `trace=[]` on one of them
still means "trace nothing". A job with no consumers anywhere records
locally anyway, so its item records never point at traces that were not
written.

## Every run knows where it came from

A run carries its **origin** in its metadata, and every consumer reads
it: the local consumer files the run under it, Langfuse receives it as
tags, a run store indexes it.

| Origin | Set by | Also carries |
|---|---|---|
| `service` | a served door | `service`, `transport`, `variant` |
| `job` | a job's item | `job`, `job_run`, `key` (and `runbook`, `runbook_run` inside a runbook) |
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
`/tmp/operonx_traces`. Days are UTC. `layout: flat` keeps the pre-1.9
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

## Where to go next

- Keep, query and alert on runs: [Runs: stores, retention and alerts](11-runs.md).
- Stream traces alongside frames: [Streaming](06-streaming.md).
- Inspect resource configs: [Resource hub](../architecture/resource-hub.md).
