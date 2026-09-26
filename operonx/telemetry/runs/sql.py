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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .model import OpRollup, Page, RunFilter, RunSummary

__all__ = ["SUMMARY_COLUMNS", "SqlIndex"]

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


def _coltype(col: str) -> str:
    if col in _TEXT:
        return "TEXT"
    if col in _REAL:
        return "REAL"
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
    ):
        self._connect = connect
        self.ph = ph
        self._json_get = json_get
        self.runs = f"{prefix}runs"
        self.ops = f"{prefix}op_rollups"

    # -- schema --------------------------------------------------------------

    def create(self) -> None:
        cols = ",\n  ".join(
            f"{c} {_coltype(c)}" + (" PRIMARY KEY" if c == "trace_id" else "")
            for c in SUMMARY_COLUMNS
        )
        ops = ",\n  ".join(
            f"{c} {'TEXT' if c in ('trace_id', 'op', 'op_type', 'samples') else ('REAL' if c in ('total_ms', 'max_ms', 'cost_usd') else 'INTEGER')}"
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
