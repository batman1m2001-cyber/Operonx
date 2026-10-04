"""The summaries and rollups as SQL — one schema, shared by every SQL backend.

The files backend keeps this index in a SQLite file beside the run
directories; the SQLite backend keeps it next to the records; the
Postgres backend runs the same statements with its own placeholder. So
"which runs, sorted how, summed how" is written once.

A :class:`SqlIndex` is handed a connection factory and a placeholder and
never holds a connection between calls: each call opens, works, commits,
closes — safe across the engine's consumer threads and across the
worker processes of one service writing the same file.
"""

from __future__ import annotations

import json
import shutil
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from operonx.telemetry.writer import BackgroundWriter

from .base import RunStore, _check_by
from .model import (
    OpRollup,
    Page,
    RunFilter,
    RunRecord,
    RunSummary,
    meta_of_trace,
    row_of,
    summarize,
)

__all__ = ["SUMMARY_COLUMNS", "SqlIndex", "SqlRunStore"]

SUMMARY_COLUMNS: Tuple[str, ...] = (
    "trace_id",
    "workflow",
    "origin",
    "name",
    "status",
    "started_at",
    "duration_ms",
    "executions",
    "ops",
    "errors",
    "first_error",
    "cost_usd",
    "unpriced",
    "llm_calls",
    "tokens_in",
    "tokens_out",
    "tokens_cached",
    "version",
    "version_dirty",
    "service",
    "transport",
    "variant",
    "job",
    "job_run",
    "key",
    "runbook",
    "runbook_run",
    "project",
    "session_id",
    "user_id",
    "request_id",
    "metadata",
    "location",
)
_TEXT = {
    "trace_id", "workflow", "origin", "name", "status", "first_error", "version",
    "service", "transport", "variant", "job", "job_run", "key", "runbook",
    "runbook_run", "project", "session_id", "user_id", "request_id", "metadata",
    "location",
}  # fmt: skip
_REAL = {"started_at", "duration_ms", "cost_usd"}
ROLLUP_COLUMNS: Tuple[str, ...] = (
    "trace_id", "op", "op_type", "count", "total_ms", "max_ms", "errors",
    "cost_usd", "unpriced", "tokens_in", "tokens_out", "samples", "exact",
)  # fmt: skip

_ORDER_SQL = {
    "started_desc": "started_at DESC, trace_id DESC",
    "started_asc": "started_at ASC, trace_id ASC",
    "duration_desc": "duration_ms DESC, trace_id DESC",
    "cost_desc": "(cost_usd IS NULL) ASC, cost_usd DESC, trace_id DESC",
    "errors_desc": "errors DESC, started_at DESC",
}


def _coltype(col: str, real: str = "REAL") -> str:
    if col in _TEXT:
        return "TEXT"
    if col in _REAL:
        return real
    return "INTEGER"


class SqlIndex:
    """Summaries and rollups in two tables. ``ph`` is the driver's
    placeholder (``?`` for sqlite3, ``%s`` for psycopg); ``json_get`` turns
    ``(column, key)`` into the dialect's JSON field access."""

    def __init__(
        self,
        connect: Callable[[], Any],
        *,
        ph: str = "?",
        json_get: Callable[[str, str], str] = lambda col, key: f"json_extract({col}, '$.{key}')",
        prefix: str = "",
        real_type: str = "REAL",
    ):
        self._connect = connect
        self.ph = ph
        self._json_get = json_get
        #: SQLite's REAL is 8 bytes; Postgres's is 4, which would round an
        #: epoch-seconds start time to minutes — it passes DOUBLE PRECISION
        self.real_type = real_type
        self.runs = f"{prefix}runs"
        self.ops = f"{prefix}op_rollups"

    # -- schema --------------------------------------------------------------

    def create(self) -> None:
        cols = ",\n  ".join(
            f"{c} {_coltype(c, self.real_type)}" + (" PRIMARY KEY" if c == "trace_id" else "")
            for c in SUMMARY_COLUMNS
        )
        ops = ",\n  ".join(
            f"{c} {'TEXT' if c in ('trace_id', 'op', 'op_type', 'samples') else (self.real_type if c in ('total_ms', 'max_ms', 'cost_usd') else 'INTEGER')}"
            for c in ROLLUP_COLUMNS
        )
        statements = [
            f"CREATE TABLE IF NOT EXISTS {self.runs} (\n  {cols}\n)",
            f"CREATE INDEX IF NOT EXISTS {self.runs}_by_origin ON {self.runs} (origin, name, started_at)",
            f"CREATE INDEX IF NOT EXISTS {self.runs}_by_time ON {self.runs} (started_at)",
            f"CREATE INDEX IF NOT EXISTS {self.runs}_by_job_run ON {self.runs} (job_run)",
            f"CREATE TABLE IF NOT EXISTS {self.ops} (\n  {ops},\n  PRIMARY KEY (trace_id, op)\n)",
        ]
        with self._tx() as cur:
            for sql in statements:
                cur.execute(sql)

    # -- write ---------------------------------------------------------------

    def put(self, summary: RunSummary, rollups: Sequence[OpRollup]) -> None:
        self.put_many([(summary, rollups)])

    def put_many(self, items: Sequence[Tuple[RunSummary, Sequence[OpRollup]]]) -> None:
        """Write many runs in ONE transaction. Indexing a directory of
        runs one transaction each spent ~20 ms per run opening and
        closing the database (a WAL checkpoint on every close) against
        ~3 ms reading the run: 376 callbot runs took 8.9 s that way and
        0.54 s batched."""
        if not items:
            return
        marks = ", ".join([self.ph] * len(SUMMARY_COLUMNS))
        rmarks = ", ".join([self.ph] * len(ROLLUP_COLUMNS))
        with self._tx() as cur:
            for summary, rollups in items:
                cur.execute(
                    f"DELETE FROM {self.ops} WHERE trace_id = {self.ph}", (summary.trace_id,)
                )
                cur.execute(
                    f"DELETE FROM {self.runs} WHERE trace_id = {self.ph}", (summary.trace_id,)
                )
                cur.execute(
                    f"INSERT INTO {self.runs} ({', '.join(SUMMARY_COLUMNS)}) VALUES ({marks})",
                    [self._summary_value(summary, c) for c in SUMMARY_COLUMNS],
                )
                for r in rollups:
                    cur.execute(
                        f"INSERT INTO {self.ops} ({', '.join(ROLLUP_COLUMNS)}) VALUES ({rmarks})",
                        [self._rollup_value(r, c) for c in ROLLUP_COLUMNS],
                    )

    def delete(self, where: RunFilter) -> List[Dict[str, Any]]:
        """Delete matching runs; return their ``trace_id`` and ``location``."""
        sql, params = self._where(where)
        with self._tx() as cur:
            cur.execute(f"SELECT trace_id, location FROM {self.runs}{sql}", params)
            gone = [{"trace_id": r[0], "location": r[1]} for r in cur.fetchall()]
            for row in gone:
                cur.execute(
                    f"DELETE FROM {self.ops} WHERE trace_id = {self.ph}", (row["trace_id"],)
                )
                cur.execute(
                    f"DELETE FROM {self.runs} WHERE trace_id = {self.ph}", (row["trace_id"],)
                )
        return gone

    # -- read ----------------------------------------------------------------

    def list(
        self,
        where: Optional[RunFilter],
        order: str,
        limit: int,
        cursor: Optional[str],
    ) -> Page:
        if order not in _ORDER_SQL:
            raise ValueError(f"unknown order {order!r}; one of {', '.join(_ORDER_SQL)}")
        limit = max(1, min(int(limit), 5000))
        offset = int(cursor) if cursor and str(cursor).isdigit() else 0
        sql, params = self._where(where)
        with self._tx() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {self.runs}{sql}", params)
            total = int(cur.fetchone()[0])
            cur.execute(
                f"SELECT {', '.join(SUMMARY_COLUMNS)} FROM {self.runs}{sql} "
                f"ORDER BY {_ORDER_SQL[order]} LIMIT {limit} OFFSET {offset}",
                params,
            )
            rows = cur.fetchall()
        items = [self._summary_of(row) for row in rows]
        nxt = offset + len(items)
        return Page(items=items, next_cursor=str(nxt) if nxt < total else None, total=total)

    def get(self, trace_id: str) -> Optional[RunSummary]:
        page = self.list(RunFilter(trace_ids=[trace_id]), "started_desc", 1, None)
        return page.items[0] if page.items else None

    def rollups(self, where: Optional[RunFilter]) -> List[OpRollup]:
        sql, params = self._where(where)
        cols = ", ".join(f"o.{c}" for c in ROLLUP_COLUMNS)
        sub = f"SELECT trace_id FROM {self.runs}{sql}"
        with self._tx() as cur:
            cur.execute(f"SELECT {cols} FROM {self.ops} o WHERE o.trace_id IN ({sub})", params)
            rows = cur.fetchall()
        out = []
        for row in rows:
            d = dict(zip(ROLLUP_COLUMNS, row))
            d["samples"] = json.loads(d["samples"] or "[]")
            d["exact"] = bool(d["exact"])
            out.append(OpRollup(**d))
        return out

    def groups(self, where: Optional[RunFilter], by: Sequence[str]) -> List[Dict[str, Any]]:
        """Counts per group — the columns in *by* are checked by the caller."""
        sql, params = self._where(where)
        cols = ", ".join(by)
        with self._tx() as cur:
            cur.execute(
                f"SELECT {cols}, COUNT(*), "
                f"SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END), "
                f"MIN(started_at), MAX(started_at), SUM(cost_usd), SUM(duration_ms) "
                f"FROM {self.runs}{sql} GROUP BY {cols} ORDER BY MAX(started_at) DESC",
                params,
            )
            rows = cur.fetchall()
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

    def locations(self) -> Dict[str, str]:
        """``location → trace_id`` for every indexed run that has one."""
        with self._tx() as cur:
            cur.execute(f"SELECT location, trace_id FROM {self.runs} WHERE location IS NOT NULL")
            return {r[0]: r[1] for r in cur.fetchall()}

    # -- helpers -------------------------------------------------------------

    def _where(self, f: Optional[RunFilter]) -> Tuple[str, List[Any]]:
        if f is None:
            return "", []
        clauses: List[str] = []
        params: List[Any] = []
        ph = self.ph
        for col in ("origin", "name", "status", "version", "job_run", "runbook_run"):
            value = getattr(f, col)
            if value:
                clauses.append(f"{col} = {ph}")
                params.append(value)
        if f.since is not None:
            clauses.append(f"started_at >= {ph}")
            params.append(float(f.since))
        if f.until is not None:
            clauses.append(f"started_at < {ph}")
            params.append(float(f.until))
        if f.trace_ids is not None:
            ids = list(f.trace_ids)
            if not ids:
                clauses.append("1 = 0")
            else:
                clauses.append(f"trace_id IN ({', '.join([ph] * len(ids))})")
                params.extend(ids)
        for key, value in (f.metadata or {}).items():
            safe = "".join(ch for ch in str(key) if ch.isalnum() or ch in "_-")
            clauses.append(f"{self._json_get('metadata', safe)} = {ph}")
            params.append(str(value))
        if f.search:
            clauses.append(
                f"LOWER(trace_id || ' ' || COALESCE(key, '') || ' ' || COALESCE(metadata, '')) LIKE {ph}"
            )
            params.append(f"%{f.search.lower()}%")
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    @staticmethod
    def _summary_value(s: RunSummary, col: str) -> Any:
        v = getattr(s, col)
        if col == "metadata":
            return json.dumps(v or {}, default=str, sort_keys=True)
        if col == "version_dirty":
            return None if v is None else int(bool(v))
        return v

    @staticmethod
    def _rollup_value(r: OpRollup, col: str) -> Any:
        v = getattr(r, col)
        if col == "samples":
            return json.dumps([round(x, 3) for x in v])
        if col == "exact":
            return int(bool(v))
        return v

    @staticmethod
    def _summary_of(row: Sequence[Any]) -> RunSummary:
        d = dict(zip(SUMMARY_COLUMNS, row))
        d["metadata"] = json.loads(d["metadata"] or "{}")
        if d.get("version_dirty") is not None:
            d["version_dirty"] = bool(d["version_dirty"])
        return RunSummary.from_dict(d)

    def _tx(self):
        return _Tx(self._connect)


class _Tx:
    """``with index._tx() as cur``: connect, work, commit, close."""

    def __init__(self, connect: Callable[[], Any]):
        self._connect = connect
        self._conn = None

    def __enter__(self):
        self._conn = self._connect()
        return self._conn.cursor()

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()
        return False


# ── a run store over SqlIndex ───────────────────────────────────────────


class SqlRunStore(RunStore):
    """A run store on a SQL database — what the SQLite and Postgres stores are.

    Summaries and rollups in :class:`SqlIndex`'s two tables; each finished
    run's full record (``meta`` and its rows, compressed) in ``records``.
    Large payloads go to ``media_dir/<trace_id>/media`` as the local
    consumer writes them, so a record's refs read the same way.

    **Live.** :meth:`on_start` and :meth:`on_execution` queue onto a
    :class:`~operonx.telemetry.writer.BackgroundWriter`, whose thread
    inserts the run's summary with status ``running`` and each execution's
    row into ``live (trace_id, seq, row)``. :meth:`consume` flushes that
    queue, then writes the record and deletes the run's live rows. A run
    whose process died stays listed as ``running``, and :meth:`get_run`
    reads it from its live rows. ``live=False`` writes only finished runs.

    A subclass hands in the connection factory, the placeholder, the JSON
    accessor, the table prefix and the type of a float column.
    """

    #: How long :meth:`consume` waits for a live run's queued rows first.
    live_flush_timeout = 10.0
    #: Live items (a run's start, one execution) waiting at most; past it
    #: they are dropped and counted (``live_writer.stats``) — the final
    #: write still has every row.
    live_queue_size = 100_000

    def __init__(
        self,
        connect: Callable[[], Any],
        *,
        media_dir: Any,
        media_threshold: int = 1024,
        ph: str = "?",
        json_get: Callable[[str, str], str] = lambda col, key: f"json_extract({col}, '$.{key}')",
        prefix: str = "",
        real_type: str = "REAL",
        blob_type: str = "BLOB",
        live: bool = True,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config=config or {})
        self.media_dir = Path(media_dir)
        self.media_threshold = int(media_threshold)
        self.ph = ph
        self.records = f"{prefix}records"
        self.live_table = f"{prefix}live"
        self.index = SqlIndex(connect, ph=ph, json_get=json_get, prefix=prefix, real_type=real_type)
        self.index.create()
        with self.index._tx() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {self.records} "
                f"(trace_id TEXT PRIMARY KEY, meta TEXT, nodes {blob_type})"
            )
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {self.live_table} "
                "(trace_id TEXT, seq INTEGER, row TEXT, PRIMARY KEY (trace_id, seq))"
            )
        self._live_runs: set = set()
        self.live_writer: Optional[BackgroundWriter] = None
        if live:
            self.live_writer = BackgroundWriter(
                self._write_live,
                name=f"sql-live:{self.index.runs}",
                max_queue=self.live_queue_size,
                batch_size=1000,
                flush_interval=0.25,
            )

    # -- write -------------------------------------------------------------

    def _rows(self, trace: Any, nodes: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """Rows for *trace* (or some of its *nodes*), media offloaded under
        the run's own folder, as ``"media/<sha>.<ext>"`` refs."""
        run_media = self.media_dir / str(trace.trace_id)
        media = run_media / "media"
        rows = []
        for node in trace.nodes if nodes is None else nodes:
            row = row_of(node, trace)
            for key in ("inputs", "outputs", "attrs"):
                if key in row:
                    value = self.sanitize(row[key])
                    row[key] = self.offload_media(value, media, self.media_threshold)
            rows.append(row)
        if media.is_dir() and not any(media.iterdir()):
            shutil.rmtree(run_media, ignore_errors=True)
        return rows

    def consume(self, trace: Any) -> RunSummary:
        """The finished run: wait for its live rows, then store it whole."""
        tid = str(trace.trace_id)
        if tid in self._live_runs:
            self.live_writer.flush(self.live_flush_timeout)
            self._live_runs.discard(tid)
        return self.put_trace(trace)

    def put_trace(self, trace: Any) -> RunSummary:
        rows = self._rows(trace)
        meta = meta_of_trace(trace)
        summary, rollups = summarize(str(trace.trace_id), rows, meta, location=None)
        blob = zlib.compress(json.dumps(rows, default=str).encode("utf-8"))
        ph = self.ph
        self.index.put(summary, rollups)
        with self.index._tx() as cur:
            cur.execute(f"DELETE FROM {self.records} WHERE trace_id = {ph}", (summary.trace_id,))
            cur.execute(
                f"INSERT INTO {self.records} (trace_id, meta, nodes) VALUES ({ph}, {ph}, {ph})",
                (summary.trace_id, json.dumps(meta, default=str), blob),
            )
            cur.execute(f"DELETE FROM {self.live_table} WHERE trace_id = {ph}", (summary.trace_id,))
        return summary

    # -- live --------------------------------------------------------------

    @property
    def live(self) -> bool:
        return self.live_writer is not None

    def on_start(self, trace: Any) -> None:
        """Live: queue the run's ``running`` summary row."""
        self._live_runs.add(str(trace.trace_id))
        self.live_writer.submit(("start", trace, None))

    def on_execution(self, trace: Any, execution: Any) -> None:
        """Live: queue one execution's row at its index in ``trace.nodes``."""
        self.live_writer.submit(("node", trace, (len(trace.nodes) - 1, execution)))

    def _write_live(self, items: List[tuple]) -> None:
        """The live writer's sink. A run already stored whole (its record
        exists) is left alone — its final write has every row. A running
        run's summary counts the executions landed so far."""
        ph = self.ph
        starts: Dict[str, RunSummary] = {}
        rows: List[tuple] = []
        for kind, trace, payload in items:
            tid = str(trace.trace_id)
            if kind == "start":
                summary, _ = summarize(tid, [], meta_of_trace(trace, running=True))
                starts[tid] = summary
            else:
                seq, execution = payload
                (row,) = self._rows(trace, [execution])
                rows.append((tid, seq, json.dumps(row, default=str)))
        tids = list({*starts, *(r[0] for r in rows)})
        with self.index._tx() as cur:
            marks = ", ".join([ph] * len(tids))
            cur.execute(f"SELECT trace_id FROM {self.records} WHERE trace_id IN ({marks})", tids)
            done = {r[0] for r in cur.fetchall()}
            cur.execute(f"SELECT trace_id FROM {self.index.runs} WHERE trace_id IN ({marks})", tids)
            listed = {r[0] for r in cur.fetchall()}
        self.index.put_many([(s, []) for t, s in starts.items() if t not in listed])
        rows = [r for r in rows if r[0] not in done]
        if rows:
            with self.index._tx() as cur:
                for row in rows:
                    cur.execute(
                        f"INSERT INTO {self.live_table} (trace_id, seq, row) "
                        f"VALUES ({ph}, {ph}, {ph}) ON CONFLICT DO NOTHING",
                        row,
                    )
                for tid in {r[0] for r in rows}:
                    cur.execute(
                        f"UPDATE {self.index.runs} SET executions = "
                        f"(SELECT COUNT(*) FROM {self.live_table} WHERE trace_id = {ph}) "
                        f"WHERE trace_id = {ph} AND status = 'running'",
                        (tid, tid),
                    )

    # -- read --------------------------------------------------------------

    def list_runs(
        self,
        where: Optional[RunFilter] = None,
        order: str = "started_desc",
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Page:
        return self.index.list(where, order, limit, cursor)

    def get_run(self, trace_id: str) -> Optional[RunRecord]:
        summary = self.index.get(trace_id)
        if summary is None:
            return None
        ph = self.ph
        with self.index._tx() as cur:
            cur.execute(
                f"SELECT meta, nodes FROM {self.records} WHERE trace_id = {ph}", (trace_id,)
            )
            row = cur.fetchone()
            live = None
            if row is None:
                cur.execute(
                    f"SELECT row FROM {self.live_table} WHERE trace_id = {ph} ORDER BY seq",
                    (trace_id,),
                )
                live = [json.loads(r[0]) for r in cur.fetchall()]
        run_media = self.media_dir / trace_id
        media_root = str(run_media) if run_media.is_dir() else None
        if row is not None:
            return RunRecord(
                summary=summary,
                nodes=json.loads(zlib.decompress(bytes(row[1])).decode("utf-8")),
                meta=json.loads(row[0] or "{}"),
                media_root=media_root,
            )
        # still running, or its process died: what it finished so far
        meta = {
            "trace_id": trace_id,
            "workflow_name": summary.workflow,
            "wall_started_at": summary.started_at,
            "metadata": summary.metadata,
            "status": "running",
        }
        summary, _ = summarize(trace_id, live, meta)
        return RunRecord(summary=summary, nodes=live, meta=meta, media_root=media_root)

    def groups(
        self, where: Optional[RunFilter] = None, by: Sequence[str] = ("origin", "name")
    ) -> List[Dict[str, Any]]:
        return self.index.groups(where, _check_by(by))

    def rollups(self, where: Optional[RunFilter] = None) -> List[OpRollup]:
        return self.index.rollups(where)

    # -- housekeeping ------------------------------------------------------

    def delete_runs(self, where: RunFilter) -> int:
        gone = self.index.delete(where)
        ph = self.ph
        with self.index._tx() as cur:
            for row in gone:
                cur.execute(f"DELETE FROM {self.records} WHERE trace_id = {ph}", (row["trace_id"],))
                cur.execute(
                    f"DELETE FROM {self.live_table} WHERE trace_id = {ph}", (row["trace_id"],)
                )
        for row in gone:
            shutil.rmtree(self.media_dir / row["trace_id"], ignore_errors=True)
        return len(gone)

    def close(self) -> None:
        if self.live_writer is not None:
            self.live_writer.close()
