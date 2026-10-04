"""The ClickHouse score store — experiments, items, scores and the judge
cache beside the runs, in the same database.

The tables are schema version 3 of the run store's migration chain
(:data:`operonx.telemetry.runs.clickhouse.MIGRATIONS`): whichever store
opens the database first brings it to the newest version, one
``schema_version`` table records it, and a user granted only tables in an
existing database never runs ``CREATE DATABASE``. So a team that already
traces into ClickHouse needs no new server, database or grant — an eval
in CI writes its experiment where the studio already reads the runs.

Writes are synchronous inserts (an eval writes through its own
background writer). Every table is ``ReplacingMergeTree(written_at)``:
experiments and items read ``FINAL``; scores are deduplicated by
``score_id`` on read (latest ``written_at``), because their ORDER BY
holds ``created_at`` and a re-written score — a human's edit — can sit in
another partition, which no merge collapses. Online scores (with a
``rule``) expire after ``online_ttl_days``; eval and human scores never.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx.telemetry.runs.clickhouse import FOREVER, ClickHouseConnection, _loads

from .base import ONLINE_TTL_DAYS, ScoreStore
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
from .sql import EXPERIMENT_COLUMNS, ITEM_COLUMNS, SCORE_COLUMNS

__all__ = ["ClickHouseScoreStore"]

_JSON = {"metrics", "gate", "metadata", "output", "snapshot"}
#: Non-null String columns: ``None`` is written as ``""``.
_TEXT = {
    "experiment_id", "project", "eval", "dataset", "dataset_version", "graph", "graph_hash",
    "config_hash", "evaluators_hash", "operonx_version", "status", "case_id", "case_hash",
    "score_id", "target", "origin", "name", "score_name", "evaluator_version", "source",
    "data_type", "reason",
}  # fmt: skip
_COUNTS = {"repeats", "cases", "errored", "tokens_in", "tokens_out"}
_FLOATS = {
    "started_at", "ended_at", "cost_usd", "judge_cost_usd", "p50_ms", "p95_ms", "ms", "value",
    "created_at",
}  # fmt: skip
_SCORE_INSERT = SCORE_COLUMNS + ("expires_at",)


#: Strings an item requires that a score may lack (a trace's score has no case).
_SCORE_NULLABLE = {"experiment_id", "case_id"}


def _cell(column: str, value: Any, nullable: Any = ()) -> Any:
    if column in _JSON:
        return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    if column in _TEXT and column not in nullable:
        return "" if value is None else str(value)
    if column in _COUNTS:
        return int(value or 0)
    if column == "tags":
        return [str(t) for t in value or ()]
    if column == "repeat" and value is not None:
        return int(value)
    if column in ("version_dirty", "passed") and value is not None:
        return bool(value)
    if column in _FLOATS and value is not None:
        return float(value)
    return value


def _field(column: str, value: Any) -> Any:
    if column in _JSON:
        return _loads(value)
    if column == "tags":
        return list(value or [])
    if column in ("version_dirty", "passed") and value is not None:
        return bool(value)
    if column in _COUNTS or (column == "repeat" and value is not None):
        return int(value)
    return value


def _row(obj: Any, columns: Sequence[str], nullable: Any = ()) -> List[Any]:
    return [_cell(c, getattr(obj, c), nullable) for c in columns]


def _of(cls: Any, columns: Sequence[str], row: Sequence[Any]) -> Any:
    return cls(**{c: _field(c, v) for c, v in zip(columns, row)})


class ClickHouseScoreStore(ClickHouseConnection, ScoreStore):
    """See the module docstring. The connection fields are the run store's
    (``host``, ``port``, ``user``, ``password``, ``database``, ``secure``,
    ``timeout``); ``client=`` takes a ready client (tests hand in a fake)."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 0,
        user: str = "default",
        password: str = "",
        database: str = "operonx",
        secure: bool = False,
        timeout: float = 10.0,
        online_ttl_days: float = ONLINE_TTL_DAYS,
        client: Any = None,
    ):
        self._connect_init(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            secure=secure,
            timeout=timeout,
            client=client,
        )
        self.online_ttl_days = float(online_ttl_days)

    # -- write -------------------------------------------------------------

    def put_experiment(self, experiment: Experiment) -> None:
        self._insert("experiments", [_row(experiment, EXPERIMENT_COLUMNS)], EXPERIMENT_COLUMNS)

    def put_items(self, items: Sequence[ExperimentItem]) -> None:
        self._insert("experiment_items", [_row(i, ITEM_COLUMNS) for i in items], ITEM_COLUMNS)

    def _expires(self, score: Score) -> int:
        if not score.rule:
            return FOREVER
        return int(min(FOREVER, score.created_at + self.online_ttl_days * 86400.0))

    def put_scores(self, scores: Sequence[Score]) -> None:
        rows = [_row(s, SCORE_COLUMNS, _SCORE_NULLABLE) + [self._expires(s)] for s in scores]
        self._insert("scores", rows, _SCORE_INSERT)

    def cache_put(self, key: str, verdict: Dict[str, Any], ttl_s: Optional[float] = None) -> None:
        expires = FOREVER if ttl_s is None else int(max(0.0, time.time() + float(ttl_s)))
        self._insert(
            "judge_cache",
            [[str(key), json.dumps(verdict, default=str), expires]],
            ("key", "verdict", "expires_at"),
        )

    # -- read --------------------------------------------------------------

    @staticmethod
    def _where(
        f: Any,
        equal: Sequence[str],
        times: Optional[str],
        ids: Tuple[str, Any],
        params: Dict[str, Any],
    ) -> List[str]:
        """*f* as clauses, its values bound into *params*."""

        def bind(value: Any, kind: str) -> str:
            name = f"p{len(params)}"
            params[name] = value
            return f"{{{name}:{kind}}}"

        clauses: List[str] = []
        if f is None:
            return clauses
        for col in equal:
            value = getattr(f, col)
            if value is not None:
                clauses.append(f"{col} = {bind(str(value), 'String')}")
        if times is not None:
            if f.since is not None:
                clauses.append(f"{times} >= {bind(float(f.since), 'Float64')}")
            if f.until is not None:
                clauses.append(f"{times} < {bind(float(f.until), 'Float64')}")
        col, values = ids
        if values is not None:
            values = [str(v) for v in values]
            clauses.append(f"{col} IN {bind(values, 'Array(String)')}" if values else "0")
        return clauses

    def list_experiments(
        self,
        where: Optional[ExperimentFilter] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> ExperimentPage:
        limit = max(1, min(int(limit), 5000))
        offset = int(cursor) if cursor and str(cursor).isdigit() else 0
        params: Dict[str, Any] = {}
        clauses = self._where(
            where,
            ExperimentFilter.EQUAL,
            "started_at",
            ("experiment_id", where.experiment_ids if where else None),
            params,
        )
        sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        table = f"{self.database}.experiments FINAL"
        total = int(self._query(f"SELECT count() FROM {table}{sql}", params)[0][0])
        rows = self._query(
            f"SELECT {', '.join(EXPERIMENT_COLUMNS)} FROM {table}{sql} "
            f"ORDER BY started_at DESC, experiment_id DESC LIMIT {limit} OFFSET {offset}",
            params,
        )
        items = [_of(Experiment, EXPERIMENT_COLUMNS, r) for r in rows]
        nxt = offset + len(items)
        return ExperimentPage(items, str(nxt) if nxt < total else None, total)

    def get_experiment(self, experiment_id: str) -> Optional[ExperimentRecord]:
        db = self.database
        found = self._query(
            f"SELECT {', '.join(EXPERIMENT_COLUMNS)} FROM {db}.experiments FINAL "
            "WHERE experiment_id = {e:String} LIMIT 1",
            {"e": str(experiment_id)},
        )
        if not found:
            return None
        items = self._query(
            f"SELECT {', '.join(ITEM_COLUMNS)} FROM {db}.experiment_items FINAL "
            "WHERE experiment_id = {e:String} ORDER BY case_id, repeat",
            {"e": str(experiment_id)},
        )
        return ExperimentRecord(
            _of(Experiment, EXPERIMENT_COLUMNS, found[0]),
            [_of(ExperimentItem, ITEM_COLUMNS, r) for r in items],
        )

    def _latest(self, where: Optional[ScoreFilter]) -> Tuple[str, Dict[str, Any]]:
        """Each score's latest row matching *where*: identity filters inside
        the dedupe, time and retention outside (an edit can move a score's
        ``created_at``; it cannot move what it judges)."""
        params: Dict[str, Any] = {}
        ids = ("score_id", where.score_ids if where else None)
        inner = self._where(where, ScoreFilter.EQUAL, None, ids, params)
        outer = self._where(where, (), "created_at", ("score_id", None), params)
        cols = ", ".join(SCORE_COLUMNS)
        where_inner = (" WHERE " + " AND ".join(inner)) if inner else ""
        sub = (
            f"SELECT {cols}, expires_at FROM {self.database}.scores{where_inner} "
            "ORDER BY score_id, written_at DESC LIMIT 1 BY score_id"
        )
        cond = " AND ".join(["expires_at > now()", *outer])
        return f"(SELECT * FROM ({sub}) WHERE {cond})", params

    def scores(self, where: Optional[ScoreFilter] = None, limit: int = 10000) -> List[Score]:
        sub, params = self._latest(where)
        rows = self._query(
            f"SELECT {', '.join(SCORE_COLUMNS)} FROM {sub} "
            f"ORDER BY created_at, score_id LIMIT {max(1, int(limit))}",
            params,
        )
        return [_of(Score, SCORE_COLUMNS, r) for r in rows]

    def score_series(self, where: Optional[ScoreFilter], bucket_s: float) -> List[Bucket]:
        if bucket_s <= 0:
            raise ValueError(f"bucket_s is {bucket_s}; it must be > 0")
        sub, params = self._latest(where)
        params["bucket"] = float(bucket_s)
        rows = self._query(
            "SELECT floor(created_at / {bucket:Float64}) * {bucket:Float64} AS b, score_name, "
            "count(), avgOrNull(value), avgOrNull(toFloat64(passed)) "
            f"FROM {sub} GROUP BY b, score_name ORDER BY b, score_name",
            params,
        )
        return [Bucket(float(r[0]), r[1], int(r[2]), r[3], r[4]) for r in rows]

    def cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        rows = self._query(
            f"SELECT verdict, toUnixTimestamp(expires_at) FROM {self.database}.judge_cache "
            "WHERE key = {k:String} ORDER BY written_at DESC LIMIT 1",
            {"k": str(key)},
        )
        if not rows or int(rows[0][1]) <= time.time():
            return None
        return _loads(rows[0][0])

    def close(self) -> None:
        self._close_client()
