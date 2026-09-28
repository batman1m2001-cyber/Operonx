# Runs: stores, retention and alerts

A **run** is one graph execution: one call a service answered, one item a
job processed, one eval case, one playground session. [Tracing](07-tracing.md)
records every run; a **run store** keeps them so they can be asked for
later — the slowest runs of a service this week, what one call did op by
op, which op costs the most across a month of runs.

A store keeps each run twice over: **in full** (every execution with its
inputs and outputs) and as a **summary with per-op rollups**. One run
opens fully; a month of runs summarises without opening any.

## Choosing a backend

A store is a resource (`run_store:` in `resources.yaml`) and a trace
consumer, so `trace=["run_store:default"]` records into it directly:

```yaml
run_store:
  default:
    backend: files            # run directories + a SQLite index (the default)
    root: ""                  # unset → <project>/.operonx/runs
  archive:
    backend: sqlite           # one file
    path: /data/runs.sqlite
  team:
    backend: postgres         # one database every service and the studio share
    dsn: ${RUNS_PG_DSN}
  docs:
    backend: mongo
    uri: ${RUNS_MONGO_URI}
    database: operonx
  remote:
    backend: langfuse         # read-only, over runs a Langfuse consumer shipped
    host: ${LANGFUSE_HOST}
    public_key: ${LANGFUSE_PUBLIC_KEY}
    secret_key: ${LANGFUSE_SECRET_KEY}
```

| Backend | Install | Notes |
|---|---|---|
| `files` | — | The `trace_local` directories plus `.index.sqlite` beside them. `refresh()` indexes directories another writer left, and forgets removed ones. |
| `sqlite` | — | One file; large payloads offloaded beside it. |
| `postgres` | `operonx[postgres]` | `prefix` names its tables; `media_dir` takes large payloads. |
| `mongo` | `operonx[mongo]` | Native queries and pipelines; `media_dir` as above. |
| `langfuse` | `operonx[langfuse]` | Reads only: nothing is written through it. |

Backends import lazily: declaring a store pulls in no driver its backend
does not use. A tool that must not import your project (the studio)
opens one from the YAML fields with `open_run_store({...})`.

## Asking for runs

The contract is five methods, on purpose — a backend implements each
natively, and anything beyond is your own code over its results:

```python
import time

from operonx.core.registry import ResourceHub
from operonx.telemetry.runs import RunFilter

store = ResourceHub.instance().get("run_store:default")

week = RunFilter(origin="service", name="call", since=time.time() - 7 * 86400)
page = store.list_runs(week, order="duration_desc", limit=20)
for s in page.items:
    print(s.trace_id, s.status, round(s.duration_ms), s.cost_usd, s.first_error)

run = store.get_run(page.items[0].trace_id)      # summary + every execution
for op in store.op_stats(week)[:5]:              # slowest ops, by total time
    print(op.op, op.runs, op.avg_ms, op.p95_ms)

store.groups(week, by=("origin", "name"))        # runs, errors, cost per group
store.delete_runs(RunFilter(origin="playground", until=time.time() - 7 * 86400))
```

`RunFilter` is a small data object, never a query language: `origin`,
`name`, `status`, `since`/`until` (epoch seconds), `version`, `job_run`,
`runbook_run`, `trace_ids`, exact `metadata` matches, and `search` (a
case-insensitive substring over the id, the key and the metadata).
`list_runs` pages with a cursor (`page.next_cursor`) and orders by
`started_desc`, `started_asc`, `duration_desc`, `cost_desc` or
`errors_desc`.

The methods are synchronous (the engine runs consumers in a worker
thread); async code wraps a call in `asyncio.to_thread`.

## What a summary says

`summarize()` is the one definition of a run's numbers, and every
backend uses it: status, the first error's last line, executions and
ops, duration, LLM calls and tokens, and **cost**.

Cost keeps one distinction everywhere: **a priced zero is a price; an
unpriced call is not $0.** A model priced at 0 (an in-house one) adds 0;
a call with no price leaves the cost unknown and counts in `unpriced`. A
run's `cost_usd` is `None` only when nothing in it was priced — never a
silent $0.

Per op, a rollup keeps the count, total and max time, errors, cost and
tokens, and up to 64 duration samples, so percentiles across runs stay
honest without storing every execution twice.

## Retention

How long runs are kept is a policy per origin, in days or `None` for
forever. The default:

| Origin | Kept |
|---|---|
| `service` | 30 days |
| `job`, `eval` | forever |
| `playground` | 7 days |
| `adhoc` | 30 days |

```python
from operonx.telemetry.runs import DEFAULT_RETENTION, apply_retention
from operonx.telemetry.runs.retention import plan_retention

plan_retention(store)                              # {"service": 120, ...} — nothing deleted
apply_retention(store, {**DEFAULT_RETENTION, "service": 14})
```

The studio runs this when it opens a project and once a day, with the
policy from its Settings (`[studio.retention]` in `operonx.toml`).

## Alerts

An alert watches one service or job over a trailing window, from what the
store already keeps — no extra recording:

| Metric | Fires when |
|---|---|
| `error_rate` | the share of failed runs is over the threshold |
| `p95_ms` | the 95th-percentile duration is over it — of whole runs, or of one op with `op=` (time to first audio, the LLM call) |
| `cost_per_hour` | priced cost per hour is over it (unpriced runs are counted beside it, never as $0) |
| `runs` | there are **fewer** runs than the threshold: a service that went quiet |

```python
from operonx.telemetry.runs.alerts import Alert, deliver, evaluate, message, step

alert = Alert(name="call errors", origin="service", target="call",
              metric="error_rate", threshold=0.05, window_min=15, min_runs=5,
              repeat_min=60, webhook="https://hooks.slack.com/services/…")

state = evaluate(store, alert)          # the number over the window, and whether it crosses
kind = step(alert, previous, state)     # "firing", "reminder", "resolved" or None
if kind:
    deliver(alert.webhook, message(alert, state, kind))
previous = state
```

`min_runs` keeps a window of two runs from paging anyone. `step` sends a
`firing` once, a reminder every `repeat_min` while it stays over, and a
`resolved` when it comes back. `deliver` posts `{"text": …}` plus the
fields — the shape Slack and Teams incoming webhooks take. The studio
evaluates its alerts every minute while it runs; anything else can run
the same loop.

## Where to go next

- Try a service by hand, record the session: [The playground bridge](12-playground.md).
- Judge a graph against a dataset: [Evals](13-evals.md).
- The API: [operonx.telemetry.runs](../api/runs.md).
