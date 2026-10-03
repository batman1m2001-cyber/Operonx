"""The ClickHouse store: runs, every execution, and rollups, in one database.

Built for many runs (a call centre's month of calls) written by many
processes, and read by the studio. It is a trace consumer whose
:meth:`~ClickHouseRunStore.consume` only **enqueues**: a background
thread builds the rows, moves blobs into a :class:`MediaStore`, and
inserts in batches. A slow or down ClickHouse costs a run nothing. Past
the queue's bound, runs are dropped and counted (see
:mod:`operonx.telemetry.writer`).

Tables (in ``database``, created on first use, versioned in
``schema_version``):

* ``runs``: one row per run, the :class:`RunSummary` columns plus the
  metadata and ``meta.json``, ordered by ``(origin, name, started_at,
  trace_id)``;
* ``nodes``: one row per execution, the row every store keeps, ordered by
  ``(trace_id, seq)``;
* ``op_rollups``: one row per op per run, ordered by ``(origin, name,
  run_started, trace_id, op)``.

All are ``ReplacingMergeTree`` (a retried batch collapses to one row),
partitioned by month, and expire on their own through ``TTL expires_at``.
``ttl_days`` unset means operonx's per-origin retention, a number means
that many days for every origin, and ``0`` means keep forever.

Blobs (every :class:`~operonx.core.media.Media`, and any ``bytes`` or
array at ``media_threshold`` bytes or more) go to ``media_dir``,
content-addressed. The row keeps ``{"$media": sha256, "mime", "size",
"duration_s", "store"}``; :attr:`ClickHouseRunStore.media` reads them back.

Uses ``clickhouse-connect`` over HTTP (the ``clickhouse`` extra),
imported when a client is first needed. Constructing the store does no
I/O, so a service starts even while its ClickHouse is down.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx.core.loggings import LOGGER
from operonx.telemetry.consumers.local import resolve_root
from operonx.telemetry.media import LocalMediaStore, MediaStore, offload_to_store
from operonx.telemetry.writer import BackgroundWriter

from .base import RunStore, _check_by
from .model import (
    OpRollup,
    OpStats,
    Page,
    RunFilter,
    RunRecord,
    RunSummary,
    meta_of_trace,
    rows_of_trace,
    summarize,
)
from .retention import DEFAULT_RETENTION
from .sql import SUMMARY_COLUMNS

__all__ = [
    "FOREVER",
    "MIGRATIONS",
    "ClickHouseRunStore",
    "expires_at",
    "node_rows",
    "rollup_rows",
    "run_row",
]

#: The largest ``DateTime`` (2106-02-07): what "keep forever" expires at.
FOREVER = 4294967295

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# ── schema ──────────────────────────────────────────────────────────────

_DDL_VERSION = """
CREATE TABLE IF NOT EXISTS {db}.schema_version (
  version UInt32,
  note String,
  applied_at DateTime DEFAULT now()
) ENGINE = MergeTree ORDER BY version
"""

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS {db}.runs (
  trace_id String,
  workflow LowCardinality(String),
  origin LowCardinality(String),
  name LowCardinality(String),
  status LowCardinality(String),
  started_at Float64,
  duration_ms Float64,
  executions UInt32,
  ops UInt32,
  errors UInt32,
  first_error Nullable(String),
  cost_usd Nullable(Float64),
  unpriced UInt32,
  llm_calls UInt32,
  tokens_in UInt64,
  tokens_out UInt64,
  tokens_cached UInt64,
  version LowCardinality(Nullable(String)),
  version_dirty Nullable(Bool),
  service LowCardinality(Nullable(String)),
  transport LowCardinality(Nullable(String)),
  variant LowCardinality(Nullable(String)),
  job LowCardinality(Nullable(String)),
  job_run Nullable(String),
  key Nullable(String),
  runbook LowCardinality(Nullable(String)),
  runbook_run Nullable(String),
  project LowCardinality(Nullable(String)),
  session_id Nullable(String),
  user_id Nullable(String),
  request_id Nullable(String),
  metadata String CODEC(ZSTD(3)),
  meta String CODEC(ZSTD(3)),
  expires_at DateTime,
  written_at DateTime64(3) DEFAULT now64(3),
  INDEX by_trace trace_id TYPE bloom_filter GRANULARITY 4,
  INDEX by_job_run job_run TYPE bloom_filter GRANULARITY 4
) ENGINE = ReplacingMergeTree(written_at)
PARTITION BY toYYYYMM(toDateTime(started_at))
ORDER BY (origin, name, started_at, trace_id)
TTL expires_at
"""

_DDL_NODES = """
CREATE TABLE IF NOT EXISTS {db}.nodes (
  trace_id String,
  seq UInt32,
  run_started Float64,
  origin LowCardinality(String),
  name LowCardinality(String),
  op_id String,
  op_name LowCardinality(String),
  op_full_name LowCardinality(String),
  op_type LowCardinality(String),
  ctx Array(String),
  start_time Float64,
  end_time Nullable(Float64),
  wall_start Nullable(Float64),
  duration_ms Float64,
  is_yield Bool,
  status LowCardinality(String),
  error Nullable(String) CODEC(ZSTD(3)),
  inputs String CODEC(ZSTD(3)),
  outputs String CODEC(ZSTD(3)),
  upstreams String CODEC(ZSTD(3)),
  expires_at DateTime,
  written_at DateTime64(3) DEFAULT now64(3)
) ENGINE = ReplacingMergeTree(written_at)
PARTITION BY toYYYYMM(toDateTime(run_started))
ORDER BY (trace_id, seq)
TTL expires_at
"""

_DDL_ROLLUPS = """
CREATE TABLE IF NOT EXISTS {db}.op_rollups (
  trace_id String,
  op LowCardinality(String),
  op_type LowCardinality(String),
  run_started Float64,
  origin LowCardinality(String),
  name LowCardinality(String),
  count UInt32,
  total_ms Float64,
  max_ms Float64,
  errors UInt32,
  cost_usd Nullable(Float64),
  unpriced UInt32,
  tokens_in UInt64,
  tokens_out UInt64,
  samples Array(Float64),
  exact Bool,
  expires_at DateTime,
  written_at DateTime64(3) DEFAULT now64(3),
  INDEX by_trace trace_id TYPE bloom_filter GRANULARITY 4
) ENGINE = ReplacingMergeTree(written_at)
PARTITION BY toYYYYMM(toDateTime(run_started))
ORDER BY (origin, name, run_started, trace_id, op)
TTL expires_at
"""

#: ``(version, note, statements)``, applied in order on first use. A new
#: version appends here and must be idempotent (``ADD COLUMN IF NOT
#: EXISTS``): two processes may migrate at once.
MIGRATIONS: List[Tuple[int, str, List[str]]] = [
    (1, "runs, nodes and op_rollups", [_DDL_RUNS, _DDL_NODES, _DDL_ROLLUPS]),
]

# ── rows ────────────────────────────────────────────────────────────────

RUN_COLUMNS: Tuple[str, ...] = tuple(c for c in SUMMARY_COLUMNS if c != "location") + (
    "meta",
    "expires_at",
)
_SUMMARY_READ = tuple(c for c in SUMMARY_COLUMNS if c != "location")
NODE_COLUMNS: Tuple[str, ...] = (
    "trace_id", "seq", "run_started", "origin", "name", "op_id", "op_name",
    "op_full_name", "op_type", "ctx", "start_time", "end_time", "wall_start",
    "duration_ms", "is_yield", "status", "error", "inputs", "outputs",
    "upstreams", "expires_at",
)  # fmt: skip
_NODE_READ = (
    "op_id", "op_name", "op_full_name", "ctx", "start_time", "end_time",
    "wall_start", "duration_ms", "op_type", "is_yield", "status", "error",
    "inputs", "outputs", "upstreams",
)  # fmt: skip
ROLLUP_COLUMNS: Tuple[str, ...] = (
    "trace_id", "op", "op_type", "run_started", "origin", "name", "count",
    "total_ms", "max_ms", "errors", "cost_usd", "unpriced", "tokens_in",
    "tokens_out", "samples", "exact", "expires_at",
)  # fmt: skip
_ROLLUP_READ = (
    "trace_id", "op", "op_type", "count", "total_ms", "max_ms", "errors",
    "cost_usd", "unpriced", "tokens_in", "tokens_out", "samples", "exact",
)  # fmt: skip

_ORDER_SQL = {
    "started_desc": "started_at DESC, trace_id DESC",
    "started_asc": "started_at ASC, trace_id ASC",
    "duration_desc": "duration_ms DESC, trace_id DESC",
    "cost_desc": "isNull(cost_usd) ASC, cost_usd DESC, trace_id DESC",
    "errors_desc": "errors DESC, started_at DESC",
}


def _dumps(value: Any) -> str:
    """JSON text for a value already sanitised — orjson, then the stdlib
    for anything orjson declines."""
    try:
        import orjson

        return orjson.dumps(
            value, default=str, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY
        ).decode("utf-8")
    except Exception:  # noqa: BLE001
        return json.dumps(value, default=str, ensure_ascii=False)


def _loads(text: Any) -> Any:
    if not text:
        return None
    try:
        import orjson

        return orjson.loads(text)
    except Exception:  # noqa: BLE001
        return json.loads(text)


def expires_at(started_at: float, origin: str, ttl_days: Optional[float]) -> int:
    """When a run's rows expire, as epoch seconds. ``ttl_days`` unset: the
    origin's :data:`DEFAULT_RETENTION` (an origin it does not name is
    kept); ``0``: forever."""
    days = DEFAULT_RETENTION.get(origin) if ttl_days is None else ttl_days
    if not days:
        return FOREVER
    start = started_at if started_at and started_at > 0 else time.time()
    return int(min(FOREVER, start + float(days) * 86400.0))


def run_row(summary: RunSummary, meta: Dict[str, Any], expires: int) -> List[Any]:
    """A summary as a ``runs`` row, in :data:`RUN_COLUMNS` order."""
    out: List[Any] = []
    for col in RUN_COLUMNS:
        if col == "metadata":
            out.append(
                json.dumps(summary.metadata or {}, default=str, sort_keys=True, ensure_ascii=False)
            )
        elif col == "meta":
            out.append(json.dumps(meta or {}, default=str, ensure_ascii=False))
        elif col == "expires_at":
            out.append(expires)
        elif col == "version_dirty":
            out.append(None if summary.version_dirty is None else bool(summary.version_dirty))
        elif col in ("first_error", "cost_usd"):
            out.append(getattr(summary, col))
        else:
            value = getattr(summary, col)
            if col in ("started_at", "duration_ms"):
                value = float(value or 0.0)
            elif col in ("workflow", "origin", "name", "status", "trace_id"):
                value = str(value or "")
            elif isinstance(value, bool) or col in (
                "executions", "ops", "errors", "unpriced", "llm_calls",
                "tokens_in", "tokens_out", "tokens_cached",
            ):  # fmt: skip
                value = int(value or 0)
            elif value is not None:
                value = str(value)
            out.append(value)
    return out


def node_rows(summary: RunSummary, rows: Sequence[Dict[str, Any]], expires: int) -> List[List[Any]]:
    """A run's rows (the shape ``nodes.jsonl`` holds) as ``nodes`` rows."""
    out = []
    for seq, r in enumerate(rows):
        end, wall = r.get("end_time"), r.get("wall_start")
        out.append(
            [
                summary.trace_id,
                seq,
                float(summary.started_at or 0.0),
                summary.origin or "",
                summary.name or "",
                str(r.get("op_id") or ""),
                str(r.get("op_name") or ""),
                str(r.get("op_full_name") or ""),
                str(r.get("op_type") or ""),
                [str(c) for c in (r.get("ctx") or [])],
                float(r.get("start_time") or 0.0),
                float(end) if isinstance(end, (int, float)) else None,
                float(wall) if isinstance(wall, (int, float)) else None,
                float(r.get("duration_ms") or 0.0),
                bool(r.get("is_yield")),
                str(r.get("status") or "ok"),
                None if r.get("error") is None else str(r.get("error")),
                _dumps(r.get("inputs")),
                _dumps(r.get("outputs")),
                _dumps(r.get("upstreams") or []),
                expires,
            ]
        )
    return out


def rollup_rows(summary: RunSummary, rollups: Sequence[OpRollup], expires: int) -> List[List[Any]]:
    """A run's per-op rollups as ``op_rollups`` rows."""
    return [
        [
            summary.trace_id,
            r.op,
            r.op_type or "",
            float(summary.started_at or 0.0),
            summary.origin or "",
            summary.name or "",
            int(r.count),
            float(r.total_ms),
            float(r.max_ms),
            int(r.errors),
            r.cost_usd,
            int(r.unpriced),
            int(r.tokens_in),
            int(r.tokens_out),
            [round(float(x), 3) for x in r.samples],
            bool(r.exact),
            expires,
        ]
        for r in rollups
    ]


def summary_of(row: Sequence[Any]) -> RunSummary:
    d = dict(zip(_SUMMARY_READ, row))
    d["metadata"] = _loads(d.get("metadata")) or {}
    if d.get("version_dirty") is not None:
        d["version_dirty"] = bool(d["version_dirty"])
    return RunSummary.from_dict(d)


def node_of(row: Sequence[Any]) -> Dict[str, Any]:
    d = dict(zip(_NODE_READ, row))
    d["ctx"] = list(d["ctx"] or [])
    d["is_yield"] = bool(d["is_yield"])
    for key in ("inputs", "outputs"):
        d[key] = _loads(d[key])
    d["upstreams"] = _loads(d["upstreams"]) or []
    return d


# ── the store ───────────────────────────────────────────────────────────


class ClickHouseRunStore(RunStore):
    """See the module docstring.

    ``client=`` takes a ready client (anything with ``query``,
    ``command`` and ``insert`` the way ``clickhouse_connect``'s has):
    tests hand in a fake. ``read_timeout`` is how long a read waits for
    this process's queued runs to land first.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 0,
        user: str = "default",
        password: str = "",
        database: str = "operonx",
        secure: bool = False,
        ttl_days: Optional[float] = None,
        media_dir: Any = "",
        media_threshold: int = 1024,
        batch_size: int = 10000,
        flush_interval: float = 1.0,
        queue_size: int = 1000,
        timeout: float = 10.0,
        read_timeout: float = 5.0,
        media: Optional[MediaStore] = None,
        client: Any = None,
    ):
        if not _IDENT.match(database or ""):
            raise ValueError(f"database {database!r} is not a plain identifier")
        if ttl_days is not None and float(ttl_days) < 0:
            raise ValueError(f"ttl_days is {ttl_days}; must be >= 0, or unset")
        super().__init__(config={"host": host, "database": database})
        self.host = host or "localhost"
        self.port = int(port or (8443 if secure else 8123))
        self.user = user or "default"
        self.password = password or ""
        self.database = database
        self.secure = bool(secure)
        self.ttl_days = None if ttl_days is None else float(ttl_days)
        self.media_threshold = int(media_threshold)
        self.timeout = float(timeout)
        self.read_timeout = float(read_timeout)
        if media is None:
            root = resolve_root(media_dir) if media_dir else resolve_root("") / "media"
            media = LocalMediaStore(root)
        self.media = media
        self._given_client = client
        self._ch = client
        self._ch_pid = os.getpid()
        self._ready = False
        self._lock = threading.RLock()
        self.writer = BackgroundWriter(
            self._write_batch,
            name=f"clickhouse:{self.host}/{database}",
            max_queue=queue_size,
            batch_size=batch_size,
            flush_interval=flush_interval,
            weight=lambda trace: len(getattr(trace, "nodes", ()) or ()) + 1,
        )

    # -- connection and schema ----------------------------------------------------

    def _client(self) -> Any:
        if self._ch is None or (self._given_client is None and self._ch_pid != os.getpid()):
            with self._lock:
                if self._ch is None or self._ch_pid != os.getpid():
                    try:
                        import clickhouse_connect
                    except ImportError as exc:  # pragma: no cover — the extra's absence
                        raise ImportError(
                            'the clickhouse run store needs: pip install "operonx[clickhouse]"'
                        ) from exc
                    self._ch = clickhouse_connect.get_client(
                        host=self.host,
                        port=self.port,
                        username=self.user,
                        password=self.password,
                        secure=self.secure,
                        connect_timeout=self.timeout,
                        send_receive_timeout=max(self.timeout, 30.0),
                        autogenerate_session_id=False,
                    )
                    self._ch_pid = os.getpid()
                    self._ready = False
        if not self._ready:
            with self._lock:
                if not self._ready:
                    self._migrate(self._ch)
                    self._ready = True
        return self._ch

    def _migrate(self, ch: Any) -> int:
        """Create what is missing; apply migrations newer than recorded."""
        db = self.database
        ch.command(f"CREATE DATABASE IF NOT EXISTS {db}")
        ch.command(_DDL_VERSION.format(db=db))
        rows = ch.query(f"SELECT max(version) FROM {db}.schema_version").result_rows
        current = int((rows[0][0] if rows else 0) or 0)
        for version, note, statements in MIGRATIONS:
            if version <= current:
                continue
            for sql in statements:
                ch.command(sql.format(db=db))
            ch.insert(f"{db}.schema_version", [[version, note]], column_names=["version", "note"])
            current = version
        return current

    def schema_version(self) -> int:
        rows = self._query(f"SELECT max(version) FROM {self.database}.schema_version")
        return int(rows[0][0] or 0)

    def _query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Sequence[Any]]:
        return list(self._client().query(sql, parameters=params or None).result_rows)

    def _command(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._client().command(sql, parameters=params or None)

    def _insert(self, table: str, rows: List[List[Any]], columns: Sequence[str]) -> None:
        if rows:
            self._client().insert(
                f"{self.database}.{table}",
                rows,
                column_names=list(columns),
                settings={"async_insert": 1, "wait_for_async_insert": 1},
            )

    # -- write -------------------------------------------------------------------

    def consume(self, trace: Any) -> bool:
        """The engine's hook: queue the run and return. ``False`` when the
        queue was full and the run was dropped (counted in
        ``writer.stats``)."""
        return self.writer.submit(trace)

    def build(self, trace: Any) -> Tuple[RunSummary, List[Any], List[List[Any]], List[List[Any]]]:
        """A finished trace as its rows: ``(summary, run row, node rows,
        rollup rows)``. Blobs go to :attr:`media` on the way."""
        rows = rows_of_trace(
            trace,
            self,
            offload=lambda v: offload_to_store(v, self.media, self.media_threshold),
        )
        meta = meta_of_trace(trace)
        summary, rollups = summarize(str(trace.trace_id), rows, meta, location=None)
        exp = expires_at(summary.started_at, summary.origin, self.ttl_days)
        return (
            summary,
            run_row(summary, meta, exp),
            node_rows(summary, rows, exp),
            rollup_rows(summary, rollups, exp),
        )

    def put_trace(self, trace: Any) -> RunSummary:
        """Store one run now, synchronously (scripts, backfills). The
        engine's path is :meth:`consume`."""
        summary, run, nodes, rollups = self.build(trace)
        self._insert_all([run], nodes, rollups)
        return summary

    def _insert_all(
        self, runs: List[List[Any]], nodes: List[List[Any]], rollups: List[List[Any]]
    ) -> None:
        # nodes first: a run that is listed always has its executions
        self._insert("nodes", nodes, NODE_COLUMNS)
        self._insert("op_rollups", rollups, ROLLUP_COLUMNS)
        self._insert("runs", runs, RUN_COLUMNS)

    _built_errors = 0

    def _write_batch(self, traces: List[Any]) -> None:
        """The writer thread's sink: build every trace, insert once per table."""
        runs: List[List[Any]] = []
        nodes: List[List[Any]] = []
        rollups: List[List[Any]] = []
        for trace in traces:
            try:
                _, run, n, r = self.build(trace)
            except Exception as exc:  # noqa: BLE001 — one odd run never sinks the batch
                self._built_errors += 1
                if self._built_errors == 1:
                    LOGGER.warning(
                        "clickhouse: could not turn trace %s into rows (%s: %s); skipped",
                        getattr(trace, "trace_id", "?"),
                        type(exc).__name__,
                        exc,
                    )
                continue
            runs.append(run)
            nodes.extend(n)
            rollups.extend(r)
        self._insert_all(runs, nodes, rollups)

    def flush(self, timeout: Optional[float] = None) -> bool:
        """Wait for queued runs to be written (or dropped)."""
        return self.writer.flush(timeout)

    def _settle(self) -> None:
        """Reads see this process's own runs: wait for the queue first."""
        self.writer.flush(self.read_timeout)

    # -- read --------------------------------------------------------------------

    def _where(self, f: Optional[RunFilter]) -> Tuple[str, Dict[str, Any]]:
        if f is None:
            return "", {}
        clauses: List[str] = []
        params: Dict[str, Any] = {}

        def bind(value: Any, kind: str) -> str:
            name = f"p{len(params)}"
            params[name] = value
            return f"{{{name}:{kind}}}"

        for col in ("origin", "name", "status", "version", "job_run", "runbook_run"):
            value = getattr(f, col)
            if value:
                clauses.append(f"{col} = {bind(str(value), 'String')}")
        if f.since is not None:
            clauses.append(f"started_at >= {bind(float(f.since), 'Float64')}")
        if f.until is not None:
            clauses.append(f"started_at < {bind(float(f.until), 'Float64')}")
        if f.trace_ids is not None:
            ids = [str(i) for i in f.trace_ids]
            clauses.append(f"trace_id IN {bind(ids, 'Array(String)')}" if ids else "0")
        for key, value in (f.metadata or {}).items():
            k, v = bind(str(key), "String"), bind(str(value), "String")
            clauses.append(
                f"(JSONExtractString(metadata, {k}) = {v} OR JSONExtractRaw(metadata, {k}) = {v})"
            )
        if f.search:
            q = bind(str(f.search), "String")
            clauses.append(
                f"positionCaseInsensitiveUTF8(concat(trace_id, ' ', ifNull(key, ''), ' ', metadata), {q}) > 0"
            )
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def list_runs(
        self,
        where: Optional[RunFilter] = None,
        order: str = "started_desc",
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Page:
        if order not in _ORDER_SQL:
            raise ValueError(f"unknown order {order!r}; one of {', '.join(_ORDER_SQL)}")
        self._settle()
        limit = max(1, min(int(limit), 5000))
        offset = int(cursor) if cursor and str(cursor).isdigit() else 0
        sql, params = self._where(where)
        runs = f"{self.database}.runs FINAL"
        total = int(self._query(f"SELECT count() FROM {runs}{sql}", params)[0][0])
        rows = self._query(
            f"SELECT {', '.join(_SUMMARY_READ)} FROM {runs}{sql} "
            f"ORDER BY {_ORDER_SQL[order]} LIMIT {limit} OFFSET {offset}",
            params,
        )
        items = [summary_of(r) for r in rows]
        nxt = offset + len(items)
        return Page(items=items, next_cursor=str(nxt) if nxt < total else None, total=total)

    def count(self, where: Optional[RunFilter] = None) -> int:
        self._settle()
        sql, params = self._where(where)
        return int(
            self._query(f"SELECT count() FROM {self.database}.runs FINAL{sql}", params)[0][0]
        )

    def get_run(self, trace_id: str) -> Optional[RunRecord]:
        self._settle()
        db = self.database
        found = self._query(
            f"SELECT {', '.join(_SUMMARY_READ)}, meta FROM {db}.runs FINAL "
            f"WHERE trace_id = {{t:String}} LIMIT 1",
            {"t": str(trace_id)},
        )
        if not found:
            return None
        row = found[0]
        nodes = self._query(
            f"SELECT {', '.join(_NODE_READ)} FROM {db}.nodes FINAL "
            f"WHERE trace_id = {{t:String}} ORDER BY seq",
            {"t": str(trace_id)},
        )
        root = getattr(self.media, "root", None)
        return RunRecord(
            summary=summary_of(row[:-1]),
            nodes=[node_of(n) for n in nodes],
            meta=_loads(row[-1]) or {},
            media_root=str(root) if root is not None and Path(root).is_dir() else None,
        )

    def rollups(self, where: Optional[RunFilter] = None) -> List[OpRollup]:
        self._settle()
        sql, params = self._where(where)
        db = self.database
        rows = self._query(
            f"SELECT {', '.join(_ROLLUP_READ)} FROM {db}.op_rollups FINAL "
            f"WHERE trace_id IN (SELECT trace_id FROM {db}.runs FINAL{sql})",
            params,
        )
        out = []
        for row in rows:
            d = dict(zip(_ROLLUP_READ, row))
            d["samples"] = list(d["samples"] or [])
            d["exact"] = bool(d["exact"])
            out.append(OpRollup(**d))
        return out

    def op_stats(self, where: Optional[RunFilter] = None) -> List[OpStats]:
        """Native: sums in ClickHouse, percentiles with
        ``quantilesExactInclusive`` over the kept samples — the same
        linear-between-ranks rule :func:`percentile` uses."""
        self._settle()
        sql, params = self._where(where)
        db = self.database
        rows = self._query(
            "SELECT op, anyIf(op_type, op_type != ''), uniqExact(trace_id), sum(count), "
            "sum(total_ms), max(max_ms), sum(errors), "
            "if(countIf(cost_usd IS NOT NULL) > 0, sum(cost_usd), NULL), sum(unpriced), "
            "sum(tokens_in), sum(tokens_out), "
            "quantilesExactInclusiveArray(0.5, 0.95, 0.99)(samples), min(exact) "
            f"FROM {db}.op_rollups FINAL "
            f"WHERE trace_id IN (SELECT trace_id FROM {db}.runs FINAL{sql}) "
            "GROUP BY op ORDER BY sum(total_ms) DESC",
            params,
        )
        out = []
        for r in rows:
            q = [0.0 if x != x else float(x) for x in (r[11] or [0.0, 0.0, 0.0])]  # NaN → 0
            count, total = int(r[3]), float(r[4])
            out.append(
                OpStats(
                    op=r[0],
                    op_type=r[1] or "",
                    runs=int(r[2]),
                    count=count,
                    total_ms=total,
                    avg_ms=total / count if count else 0.0,
                    p50_ms=q[0],
                    p95_ms=q[1],
                    p99_ms=q[2],
                    max_ms=float(r[5]),
                    errors=int(r[6]),
                    cost_usd=r[7],
                    unpriced=int(r[8]),
                    tokens_in=int(r[9]),
                    tokens_out=int(r[10]),
                    exact=bool(r[12]),
                )
            )
        return out

    def groups(
        self, where: Optional[RunFilter] = None, by: Sequence[str] = ("origin", "name")
    ) -> List[Dict[str, Any]]:
        by = _check_by(by)
        self._settle()
        sql, params = self._where(where)
        cols = ", ".join(by)
        rows = self._query(
            f"SELECT {cols}, count(), countIf(status = 'error'), min(started_at), "
            "max(started_at), if(countIf(cost_usd IS NOT NULL) > 0, sum(cost_usd), NULL), "
            f"sum(duration_ms) FROM {self.database}.runs FINAL{sql} "
            f"GROUP BY {cols} ORDER BY max(started_at) DESC",
            params,
        )
        n = len(by)
        return [
            {
                **dict(zip(by, row[:n])),
                "runs": int(row[n]),
                "errors": int(row[n + 1] or 0),
                "first_started": row[n + 2],
                "last_started": row[n + 3],
                "cost_usd": row[n + 4],
                "duration_ms": float(row[n + 5] or 0.0),
            }
            for row in rows
        ]

    # -- housekeeping ----------------------------------------------------------------

    def delete_runs(self, where: RunFilter) -> int:
        """Lightweight ``DELETE`` of the matching runs from every table.
        Blobs stay (other runs may share them): see :meth:`prune_media`."""
        self._settle()
        sql, params = self._where(where)
        db = self.database
        ids = [r[0] for r in self._query(f"SELECT trace_id FROM {db}.runs FINAL{sql}", params)]
        for i in range(0, len(ids), 5000):
            chunk = ids[i : i + 5000]
            for table in ("runs", "op_rollups", "nodes"):
                self._command(
                    f"DELETE FROM {db}.{table} WHERE trace_id IN {{ids:Array(String)}}",
                    {"ids": chunk},
                )
        return len(ids)

    def prune_media(self, older_than_s: float = 86400.0) -> int:
        """Delete blobs no stored execution references any more, written
        more than *older_than_s* ago (younger ones may belong to runs still
        on their way in). Returns how many were deleted."""
        self._settle()
        rows = self._query(
            "SELECT DISTINCT arrayJoin(extractAll(concat(inputs, ' ', outputs), {pat:String})) "
            f"FROM {self.database}.nodes",
            {"pat": r'"\$media":\s*"([0-9a-f]{64})"'},
        )
        keep = {r[0] for r in rows}
        cutoff = time.time() - float(older_than_s)
        gone = 0
        for sha, written in list(self.media.keys()):
            if sha not in keep and written < cutoff and self.media.delete(sha):
                gone += 1
        return gone

    def close(self) -> None:
        self.writer.close()
        ch, self._ch = self._ch, None
        if ch is not None and self._given_client is None:
            try:
                ch.close()
            except Exception:  # noqa: BLE001
                pass
