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
  events:
    backend: clickhouse       # many runs, many writers; never blocks a run
    host: ${CLICKHOUSE_HOST}
    password: ${CLICKHOUSE_PASSWORD}
    media_dir: /data/operonx-media
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
| `clickhouse` | `operonx[clickhouse]` | Writes from a background queue, never on the run's path; TTL retention; blobs content-addressed in `media_dir`. See [below](#clickhouse). |

Backends import lazily: declaring a store pulls in no driver its backend
does not use. A tool that must not import your project (the studio)
opens one from the YAML fields with `open_run_store({...})`.

## ClickHouse

For many runs from many processes: a call centre's month of calls, every
worker of every service writing into one database the studio reads.
Declare it as a trace consumer, beside Langfuse or anything else:

```yaml
trace_clickhouse:
  default: &clickhouse
    host: ${CLICKHOUSE_HOST:localhost}
    port: 8123                 # 8443 with secure: true
    user: ${CLICKHOUSE_USER:default}
    password: ${CLICKHOUSE_PASSWORD:}
    database: operonx          # created on first use
    secure: false
    ttl_days:                  # unset: the retention per origin below; 0: forever
    media_dir: /data/operonx-media
    media_threshold: 1024      # bytes; a Media value is stored at any size
    batch_size: 10000          # executions per insert
    flush_interval: 1.0        # seconds a batch waits to fill
    queue_size: 1000           # runs waiting; past it, dropped and counted

run_store:
  default:                     # what the studio reads: the same database
    <<: *clickhouse
    backend: clickhouse
```

```python
engine = Operon(graph, trace=["trace_langfuse:edupia", "trace_clickhouse:default"])
```

**A run never waits on ClickHouse.** The consumer's `consume` puts the
finished trace on a bounded queue and returns, in about 50 µs. A
background thread builds the rows, stores the blobs and inserts in
batches (`async_insert`). While ClickHouse is slow or down, runs past
`queue_size` are dropped, not queued without end. A failing batch is
retried three times, then dropped. Each outage logs one warning, and its
recovery logs one more line. The counts are in `store.writer.stats`.
`store.flush()` waits for the queue, which a short script wants before it
exits; an exit hook also gives it 5 s. Reads in the same process wait for
its own queue first, so a run it just recorded is listed.

The writer's work is real CPU: about 46 µs per execution, sharing the
GIL with whatever runs next. With gaps between runs (calls), a run's time
does not change. Runs back to back with no idle time slow each other.

**Tables.** `runs` (one row per run, the summary columns), `nodes` (one
row per execution, inputs and outputs as JSON text), `op_rollups` (one
row per op per run) and `schema_version`. They are `ReplacingMergeTree`,
so a retried batch never duplicates a run, partitioned by month and
ordered for the queries above: runs by `(origin, name, started_at,
trace_id)`, nodes by `(trace_id, seq)`. Every method of the contract is
one or two SQL statements; `op_stats` and `groups` aggregate in
ClickHouse.

**Retention.** Rows expire on their own through `TTL expires_at`. Each row's
expiry is set when it is written: the origin's default below, or
`ttl_days` for every origin. `apply_retention` (the studio's sweep) works
too, by lightweight `DELETE`.

**Media.** Every `Media` value, and any `bytes` or array of
`media_threshold` bytes or more, is stored once in `media_dir`, named by its
SHA-256. The row keeps a reference:

```json
{"$media": "9f2c…", "mime": "audio/wav", "size": 48044,
 "duration_s": 1.5, "sample_rate": 16000, "channels": 1, "store": "local"}
```

The type comes from the bytes: WAV (with rate, channels and duration from
its header), MP3, OGG/Opus, FLAC, WebM, PNG, JPEG, GIF, WebP, PDF, `.npy`;
else `application/octet-stream`. Raw PCM has no header, so declare it:
`Media(pcm, "audio/L16;rate=16000;channels=1")` gets its duration from
its size. `store.media.get(sha)` reads a blob back. Deleting a run keeps
its blobs, which other runs may share; `store.prune_media()` removes the
ones nothing references. `operonx.telemetry.media` (`detect_media`,
`LocalMediaStore`) is usable by any other store.

## Reading a project's own sinks

A reader that is not the project — the studio, a script on another
machine — wants the runs where the project writes them. Ask the project's
files, without importing its code:

```python
from operonx.telemetry.runs import project_stores

for src in project_stores("/srv/callbot"):
    print(src.source, "→", src.describe() if src.readable else src.reason)
# [tracing] → local → files at /srv/callbot/.operonx/runs
# [tracing], [tracing.services.call] → trace_clickhouse:default → ClickHouse callbot_traces at ch.internal:8123
# [tracing] → trace_langfuse:edupia → Langfuse at https://langfuse.example
store = next(s for s in project_stores("/srv/callbot") if s.backend == "clickhouse").open()
```

`project_stores(root)` reads `[tracing]` in `operonx.toml` — the
project-wide `sinks`, every `[tracing.services.<n>]` and
`[tracing.jobs.<n>]`, and the `trace =` of any `[[serve]]` or `[[job]]`
they do not override — and returns each sink once, as a
`StoreSource`: its `spec` for `open_run_store`, the `levels` that name it,
`source` in one line, and `describe()` without credentials.

| Sink | Read as |
|---|---|
| `"local"` | `files` at `<project>/.operonx/runs` (or `OPERONX_RUNS_DIR`) |
| `trace_local:<n>` | `files` at its `root` |
| `trace_clickhouse:<n>` | `clickhouse` with the consumer's own fields |
| `trace_langfuse:<n>` | `langfuse`, through its `client_resource` |
| `run_store:<n>` | that store |
| anything else | unreadable — `reason` says why |

`${VAR}` resolves as the project's own bootstrap does it: the process
environment, with the project's `.env` filling in what it lacks. A
variable set in neither makes that sink unreadable, naming it. Relative
paths anchor where the writer anchors them: a files `root` and a
`media_dir` at the project, a sqlite `path` under the runs root. Both
forms of a resources file work, nested (`run_store:` / `  default:`) and
flat (`run_store:default:`). No `[tracing]` table is an empty list.

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
