"""Experiments, items, scores and the judge cache as SQLite tables.

The files store keeps this index beside its JSONL; the sqlite store is
this index alone. Like the run index, it holds no connection between
calls — each call opens, works, commits, closes — so the writer thread of
an eval and the reader of a studio share one file safely.

Every row carries ``written_at``; an upsert keeps, per id, the row written
last, whatever order the rows arrive in — which is what lets the files
store re-read lines it has already indexed.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .model import (
    Bucket,
    Experiment,
    ExperimentFilter,
    ExperimentItem,
    ExperimentPage,
    ExperimentRecord,
    Score,
    ScoreFilter,
)

__all__ = ["ScoreIndex", "row_of", "stamped", "value_of"]

EXPERIMENT_COLUMNS = tuple(f.name for f in fields(Experiment))
ITEM_COLUMNS = tuple(f.name for f in fields(ExperimentItem))
SCORE_COLUMNS = tuple(f.name for f in fields(Score))
#: Columns held as JSON text.
_JSON = {"metrics", "gate", "metadata", "tags", "output", "snapshot"}
#: Columns held as 0/1.
_BOOL = {"version_dirty", "passed"}
_REAL = {
    "started_at", "ended_at", "cost_usd", "judge_cost_usd", "p50_ms", "p95_ms", "ms",
    "value", "created_at",
}  # fmt: skip
_INT = {"repeats", "cases", "errored", "repeat", "tokens_in", "tokens_out"}

_TABLES = {
    "experiments": (EXPERIMENT_COLUMNS, ("experiment_id",)),
    "experiment_items": (ITEM_COLUMNS, ("experiment_id", "case_id", "repeat")),
    "scores": (SCORE_COLUMNS + ("expires_at",), ("score_id",)),
}


def value_of(column: str, value: Any) -> Any:
    """A field's value as its column holds it."""
    if column in _JSON:
        return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    if column in _BOOL:
        return None if value is None else int(bool(value))
    return value


def row_of(obj: Any, columns: Sequence[str]) -> List[Any]:
    return [value_of(c, getattr(obj, c)) for c in columns]


def _field(column: str, value: Any) -> Any:
    if column in _JSON:
        return json.loads(value) if value is not None else None
    if column in _BOOL:
        return None if value is None else bool(value)
    return value


def _coltype(column: str) -> str:
    if column in _REAL or column in ("expires_at", "written_at"):
        return "REAL"
    if column in _INT or column in _BOOL:
        return "INTEGER"
    return "TEXT"


class ScoreIndex:
    """See the module docstring. ``path`` is the SQLite file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.create()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _tx(self):
        from operonx.telemetry.runs.sql import _Tx

        return _Tx(self._connect)

    def create(self) -> None:
        statements = []
        for table, (columns, key) in _TABLES.items():
            cols = ",\n  ".join(f"{c} {_coltype(c)}" for c in columns + ("written_at",))
            statements.append(
                f"CREATE TABLE IF NOT EXISTS {table} (\n  {cols},\n  PRIMARY KEY ({', '.join(key)})\n)"
            )
        statements += [
            "CREATE INDEX IF NOT EXISTS experiments_by_time ON experiments (started_at)",
            "CREATE INDEX IF NOT EXISTS scores_by_time ON scores (created_at)",
            "CREATE INDEX IF NOT EXISTS scores_by_experiment ON scores (experiment_id)",
            "CREATE INDEX IF NOT EXISTS scores_by_trace ON scores (trace_id)",
            "CREATE TABLE IF NOT EXISTS judge_cache "
            "(key TEXT PRIMARY KEY, verdict TEXT, expires_at REAL, written_at REAL)",
            "CREATE TABLE IF NOT EXISTS read_offsets (path TEXT PRIMARY KEY, offset INTEGER)",
        ]
        with self._tx() as cur:
            for sql in statements:
                cur.execute(sql)

    # -- write -----------------------------------------------------------------

    def upsert(self, table: str, rows: Iterable[Sequence[Any]]) -> int:
        """Rows in the table's column order, ``written_at`` last. Each
        replaces its key's row unless that row was written later."""
        columns, key = _TABLES[table]
        cols = columns + ("written_at",)
        update = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in key)
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {update} "
            f"WHERE excluded.written_at >= {table}.written_at"
        )
        rows = list(rows)
        if rows:
            with self._tx() as cur:
                cur.executemany(sql, rows)
        return len(rows)

    def cache_put(self, key: str, verdict: Dict[str, Any], expires_at: Optional[float]) -> None:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO judge_cache (key, verdict, expires_at, written_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET verdict = excluded.verdict, "
                "expires_at = excluded.expires_at, written_at = excluded.written_at",
                (key, json.dumps(verdict, default=str), expires_at, time.time()),
            )

    # -- read ------------------------------------------------------------------

    @staticmethod
    def _where(f: Any, equal: Sequence[str], time_col: str, extra: Sequence[str] = ()) -> Tuple[str, List[Any]]:
        clauses: List[str] = list(extra)
        params: List[Any] = []
        if f is not None:
            for col in equal:
                value = getattr(f, col)
                if value is not None:
                    clauses.append(f"{col} = ?")
                    params.append(value)
            if f.since is not None:
                clauses.append(f"{time_col} >= ?")
                params.append(float(f.since))
            if f.until is not None:
                clauses.append(f"{time_col} < ?")
                params.append(float(f.until))
            ids_col, ids = (
                ("experiment_id", f.experiment_ids)
                if isinstance(f, ExperimentFilter)
                else ("score_id", f.score_ids)
            )
            if ids is not None:
                ids = list(ids)
                clauses.append(f"{ids_col} IN ({', '.join('?' * len(ids))})" if ids else "0")
                params.extend(ids)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def list_experiments(
        self, where: Optional[ExperimentFilter], limit: int, cursor: Optional[str]
    ) -> ExperimentPage:
        limit = max(1, min(int(limit), 5000))
        offset = int(cursor) if cursor and str(cursor).isdigit() else 0
        sql, params = self._where(where, ExperimentFilter.EQUAL, "started_at")
        with self._tx() as cur:
            cur.execute(f"SELECT COUNT(*) FROM experiments{sql}", params)
            total = int(cur.fetchone()[0])
            cur.execute(
                f"SELECT {', '.join(EXPERIMENT_COLUMNS)} FROM experiments{sql} "
                f"ORDER BY started_at DESC, experiment_id DESC LIMIT {limit} OFFSET {offset}",
                params,
            )
            rows = cur.fetchall()
        items = [self._experiment(r) for r in rows]
        nxt = offset + len(items)
        return ExperimentPage(items, str(nxt) if nxt < total else None, total)

    def get_experiment(self, experiment_id: str) -> Optional[ExperimentRecord]:
        with self._tx() as cur:
            cur.execute(
                f"SELECT {', '.join(EXPERIMENT_COLUMNS)} FROM experiments WHERE experiment_id = ?",
                (experiment_id,),
            )
            found = cur.fetchone()
            if found is None:
                return None
            cur.execute(
                f"SELECT {', '.join(ITEM_COLUMNS)} FROM experiment_items "
                "WHERE experiment_id = ? ORDER BY case_id, repeat",
                (experiment_id,),
            )
            items = cur.fetchall()
        return ExperimentRecord(
            self._experiment(found),
            [ExperimentItem(**{c: _field(c, v) for c, v in zip(ITEM_COLUMNS, r)}) for r in items],
        )

    def _live(self) -> str:
        return f"(expires_at IS NULL OR expires_at > {time.time()!r})"

    def scores(self, where: Optional[ScoreFilter], limit: int) -> List[Score]:
        limit = max(1, int(limit))
        sql, params = self._where(where, ScoreFilter.EQUAL, "created_at", [self._live()])
        with self._tx() as cur:
            cur.execute(
                f"SELECT {', '.join(SCORE_COLUMNS)} FROM scores{sql} "
                f"ORDER BY created_at, score_id LIMIT {limit}",
                params,
            )
            rows = cur.fetchall()
        return [Score(**{c: _field(c, v) for c, v in zip(SCORE_COLUMNS, r)}) for r in rows]

    def score_series(self, where: Optional[ScoreFilter], bucket_s: float) -> List[Bucket]:
        if bucket_s <= 0:
            raise ValueError(f"bucket_s is {bucket_s}; it must be > 0")
        sql, params = self._where(where, ScoreFilter.EQUAL, "created_at", [self._live()])
        b = float(bucket_s)
        with self._tx() as cur:
            cur.execute(
                f"SELECT CAST(created_at / {b!r} AS INTEGER) * {b!r} AS bucket, score_name, "
                f"COUNT(*), AVG(value), AVG(passed) FROM scores{sql} "
                "GROUP BY bucket, score_name ORDER BY bucket, score_name",
                params,
            )
            rows = cur.fetchall()
        return [Bucket(float(r[0]), r[1], int(r[2]), r[3], r[4]) for r in rows]

    def cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._tx() as cur:
            cur.execute(
                f"SELECT verdict FROM judge_cache WHERE key = ? AND {self._live()}", (key,)
            )
            row = cur.fetchone()
        return json.loads(row[0]) if row else None

    # -- the files store's read positions ---------------------------------------

    def offsets(self) -> Dict[str, int]:
        with self._tx() as cur:
            cur.execute("SELECT path, offset FROM read_offsets")
            return {r[0]: int(r[1]) for r in cur.fetchall()}

    def set_offset(self, path: str, offset: int) -> None:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO read_offsets (path, offset) VALUES (?, ?) "
                "ON CONFLICT (path) DO UPDATE SET offset = excluded.offset",
                (path, int(offset)),
            )

    @staticmethod
    def _experiment(row: Sequence[Any]) -> Experiment:
        return Experiment(**{c: _field(c, v) for c, v in zip(EXPERIMENT_COLUMNS, row)})


def stamped(obj: Any, table: str, written_at: float, expires_at: Any = None) -> List[Any]:
    """*obj* as an index row of *table*, ``written_at`` last."""
    columns, _ = _TABLES[table]
    own = [c for c in columns if c != "expires_at"]
    row = row_of(obj, own)
    if table == "scores":
        row.append(expires_at)
    return row + [written_at]
