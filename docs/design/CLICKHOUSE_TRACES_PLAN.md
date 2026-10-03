# ClickHouse traces — plan

A ClickHouse run store that is also a trace consumer, with media blobs
stored beside it (content-addressed, type detected from the bytes). It
answers the same `RunStore` contract as `files`, `sqlite`, `postgres` and
`mongo`, and passes the same shared tests.

## Shape

| Module | What |
|---|---|
| `operonx/telemetry/media.py` | `detect_media()`, `MediaInfo`, `MediaStore`, `LocalMediaStore`, `offload_to_store()`. No ClickHouse in it: any store can reuse it. |
| `operonx/telemetry/writer.py` | `BackgroundWriter`: bounded queue, batching thread, drop-and-count. Generic. |
| `operonx/telemetry/runs/clickhouse.py` | `ClickHouseRunStore`: schema and migrations, row mapping, writes, reads. |
| `operonx/telemetry/consumers/clickhouse.py` | `trace_clickhouse:` config and factory. |
| `runs/config.py` | `run_store: {backend: clickhouse}` with the same fields. |

`clickhouse-connect` (HTTP) is the driver, in a new `clickhouse` extra,
imported only when a client is first needed. Constructing the store does
not touch the network either, so a service whose ClickHouse is down still
boots and serves.

## Schema

One database (`database:`, default `operonx`), created if missing. Four
tables:

* `runs`: one row per run, the `RunSummary` columns plus `metadata` (JSON
  text) and `meta` (the run's `meta.json`). `ReplacingMergeTree(written_at)`,
  `ORDER BY (origin, name, started_at, trace_id)`: the studio's tree,
  `list_runs` and `groups` all filter on origin and name and sort by time.
  Bloom-filter skip indexes on `trace_id` and `job_run` serve `get_run` and
  a job run's items.
* `nodes`: one row per execution, the row every store keeps (`ctx`,
  `upstreams`, `inputs`, `outputs` as JSON text with `ZSTD`), plus `seq`
  (its position in the trace) and the run's origin, name and start.
  `ORDER BY (trace_id, seq)`: the only query over nodes is "one run's
  nodes, in order".
* `op_rollups`: one row per op per run, as every other store keeps them.
  `ORDER BY (origin, name, run_started, trace_id, op)`, so op stats over a
  service and a window read a contiguous range.
* `schema_version`: one row per applied migration.

Every data table is `PARTITION BY toYYYYMM(toDateTime(started))`. Columns
with few values (`origin`, `name`, `status`, `op_name`, `op_type`, `service`,
`job` and the like) are `LowCardinality`. Inputs and outputs are `String`
holding JSON, not the `JSON` type: they are read back whole, never queried
by path, and their shape changes per op.

`ReplacingMergeTree` makes a write idempotent: a batch retried after a
timeout that actually landed, or a run stored twice, collapses to one row
per key. Reads use `FINAL`.

## Retention

Each row carries `expires_at`, and every table has `TTL expires_at`, so
ClickHouse deletes old runs itself in its merges. `expires_at` is computed
at write time from `ttl_days`:

* unset: operonx's per-origin policy (`DEFAULT_RETENTION`: services 30
  days, jobs and evals forever, playground 7, ad hoc 30);
* a number: that many days for every origin; `0`: forever.

Forever is the largest `DateTime` (2106). Changing `ttl_days` affects new
rows only, which needs no `ALTER`. `apply_retention()` (the studio's sweep)
still works through `delete_runs`.

Blobs are shared between runs, so deleting a run never deletes one.
`prune_media(older_than_s)` removes blobs no node references any more and
that are older than the age guard. The age guard protects blobs written for
runs still waiting in the queue.

## Writing

* `consume(trace)` (the engine's hook) only **enqueues** the finished trace
  on a bounded queue (`queue_size` runs, default 1000). It never blocks,
  never raises, and never does I/O.
* A daemon thread drains it. It turns traces into rows (sanitising and
  offloading media happen here, off the hot path) and inserts nodes, then
  rollups, then runs: a listed run always has its nodes. It inserts when
  `batch_size` node rows (default 10 000) are ready or `flush_interval`
  (default 1 s) has passed. Inserts use `async_insert=1,
  wait_for_async_insert=1`: many service processes' small batches merge
  server-side, and a failed insert is still reported to the thread.
* When the queue is full, the run is dropped and counted
  (`writer.stats["dropped_full"]`). The first drop logs a warning. Nothing
  more is logged until a write succeeds again; that write logs how many
  were lost.
* A failed batch is retried 3 times with backoff (0.5 s, up to 30 s).
  Then it is dropped and counted (`dropped_failed`), and the failure is
  logged once in the same way. While ClickHouse is down the queue fills,
  and new runs drop at the bound instead of growing memory.
* `put_trace(trace)` writes synchronously and returns the summary (the
  contract's method; scripts, backfills).
* Reads first wait (`read_timeout`, 5 s) for this process's queue to
  drain. A run the process just recorded is listed by the same process.
* `flush()` and `close()` are explicit, and an `atexit` hook flushes for up
  to 5 s, so a short script's last runs are not lost. After `fork`, the
  child starts its own thread and queue.

## Reading: the contract to SQL

| Method | SQL |
|---|---|
| `list_runs(where, order, limit, cursor)` | `SELECT count()` and `SELECT … FROM runs FINAL WHERE … ORDER BY … LIMIT n OFFSET cursor`. The cursor is an offset, as in the other stores. `cost_desc` is `isNull(cost_usd), cost_usd DESC`. |
| `RunFilter` | Columns become `=` with bound parameters. `since`/`until` become ranges on `started_at`. `trace_ids` becomes `IN {ids:Array(String)}`. `metadata` becomes `JSONExtractString(metadata,k) = v OR JSONExtractRaw(metadata,k) = v`. `search` becomes `positionCaseInsensitiveUTF8(trace_id ‖ key ‖ metadata, q) > 0`. |
| `get_run(id)` | The run row by `trace_id` (bloom index), then `nodes FINAL WHERE trace_id = id ORDER BY seq`. Rows come back in the shape `nodes.jsonl` has. |
| `rollups(where)` | `op_rollups FINAL WHERE trace_id IN (SELECT trace_id FROM runs FINAL WHERE …)`. |
| `op_stats(where)` | Native: `sum`, `max`, `uniqExact(trace_id)`, and `quantilesExactInclusiveArray(.5,.95,.99)(samples)`, which is the same linear-between-ranks percentile `percentile()` computes. Cost is `NULL` when no rollup was priced. |
| `groups(where, by)` | `GROUP BY by … ORDER BY max(started_at) DESC`. `cost_usd` is `NULL` when nothing in the group was priced. |
| `count(where)` | `SELECT count()`. |
| `delete_runs(where)` | Select the matching ids, then a lightweight `DELETE FROM` on each table by `trace_id IN`. |
| retention | TTL (above), plus `apply_retention` → `delete_runs`. |

## Media

* `offload_to_store(payload, store, threshold)` walks a sanitised value.
  A `Media` value is always stored, whatever its size. `bytes`, `bytearray`,
  `memoryview` and numpy arrays are stored at or above `media_threshold`
  (default 1024).
* The node keeps `{"$media": sha256, "mime", "size", "duration_s"?,
  "sample_rate"?, "channels"?, "store"}`. The studio already renders a
  `$media` marker.
* `MediaStore` has `name`, `put(data, info) → sha256`, `get(sha)`,
  `path(sha)`, `exists(sha)`, `delete(sha)` and `keys()`. `LocalMediaStore`
  writes `<dir>/<sha[:2]>/<sha>.<ext>`. Writes are atomic (temp file plus
  `os.replace`) and skipped when the hash is already there, so the same
  audio is stored once across runs and calls. An S3/MinIO store implements
  the same interface later. It is not built now.
* `detect_media(data, declared_mime)` reads magic bytes: WAV (RIFF/WAVE;
  the `fmt ` and `data` chunks give rate, channels and duration), MP3 (ID3
  or a valid frame sync), OGG (Opus gets its rate and duration from
  `OpusHead` and the last granule position), FLAC (STREAMINFO), WebM and
  Matroska (EBML DocType), PNG, JPEG, GIF, WebP, PDF and `.npy`. Anything
  else is `application/octet-stream`.
* The bytes win when they identify a format. Otherwise the declared mime
  wins: raw PCM can't be detected. `Media` has two fields, `data` and
  `mime_type`, so a PCM producer declares its rate in mime parameters:
  `Media(pcm, "audio/L16;rate=16000;channels=1")` (RFC 2586 style;
  `audio/pcm` and `bits=` also work). Size, rate, channels and bits give
  `duration_s`.

## Configuration

```yaml
trace_clickhouse:
  default: &clickhouse
    host: ${CLICKHOUSE_HOST:localhost}
    port: 8123                 # 8443 with secure: true
    user: ${CLICKHOUSE_USER:default}
    password: ${CLICKHOUSE_PASSWORD:}
    database: operonx
    secure: false
    ttl_days:                  # unset: per origin; 0 = forever
    media_dir: /data/operonx-media
    media_threshold: 1024
    batch_size: 10000          # node rows per insert
    flush_interval: 1.0        # seconds
    queue_size: 1000           # runs waiting; past it, dropped and counted

run_store:
  default:                     # what the studio reads
    <<: *clickhouse
    backend: clickhouse
```

`trace=["trace_langfuse:edupia", "trace_clickhouse:default"]` sends each
run to both. `trace_clickhouse:` and `run_store: {backend: clickhouse}`
build the same class. The studio finds its store through `run_store:`
(`[studio] runs = "run_store:<name>"`, else `run_store: default`), as it
does for every backend.

## Migrations

On first use (first write, or first read), the store runs `CREATE
DATABASE IF NOT EXISTS`, then creates `schema_version` and applies every
migration newer than `max(version)`, recording each one. Version 1
creates the three tables. Later versions must be idempotent (`ADD COLUMN
IF NOT EXISTS`), because two processes may migrate at once. A failed
migration is retried on the next write and never raises into a run.

## Studio

Nothing studio-specific is needed. The studio opens stores with
`open_run_store(spec)` and calls only contract methods (`list_runs`,
`get_run`, `groups`, `op_stats`, `count`, `delete_runs`, `refresh`). It
renders `$media` markers. Its `${VAR}` expansion covers `password:`. One
limit: it anchors relative paths only for `files` and `sqlite`, so
`media_dir` should be absolute.

## Tests

* Offline: detection on generated samples (`wave` for WAV, hand-built
  headers for the rest); `LocalMediaStore` dedup; row mapping; the
  writer's bound, drop counts and never-blocking behaviour, against fake
  sinks that hang or raise; config and hub wiring.
* Live (a throwaway local container; skipped unless
  `OPERONX_TEST_CLICKHOUSE` is set and reachable): the shared `RunStore`
  contract; a real `Operon` run read back as the same tree; a `Media`
  WAV blob landing once in the media dir as `audio/wav` with its
  duration; Langfuse (fake client) and ClickHouse on one engine; TTL and
  `prune_media`.
* Measure: the time a 2000-op streaming run takes with the consumer and
  without it.
