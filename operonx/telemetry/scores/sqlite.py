"""The sqlite score store — experiments, items, scores and the judge cache in one file."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from operonx.telemetry.consumers.local import resolve_root

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
from .sql import ScoreIndex, stamped

__all__ = ["SqliteScoreStore"]


class SqliteScoreStore(ScoreStore):
    """See the module docstring. ``path`` defaults to ``scores.sqlite``
    under the runs root (``<project>/.operonx/runs``); an online score
    (one with a ``rule``) is kept ``online_ttl_days``."""

    def __init__(self, path: Any = "", online_ttl_days: float = ONLINE_TTL_DAYS):
        self.path = Path(path) if path else resolve_root("") / "scores.sqlite"
        if not self.path.is_absolute():
            self.path = resolve_root("") / self.path
        self.online_ttl_days = float(online_ttl_days)
        self.index = ScoreIndex(self.path)

    # -- write -------------------------------------------------------------

    def _expires(self, score: Score) -> Optional[float]:
        return score.created_at + self.online_ttl_days * 86400.0 if score.rule else None

    def _rows(self, table: str, objs: Sequence[Any], written_at: float) -> List[List[Any]]:
        if table == "scores":
            return [stamped(s, table, written_at, self._expires(s)) for s in objs]
        return [stamped(o, table, written_at) for o in objs]

    def _put(self, table: str, objs: Sequence[Any]) -> None:
        if objs:
            self.index.upsert(table, self._rows(table, objs, time.time()))

    def put_experiment(self, experiment: Experiment) -> None:
        self._put("experiments", [experiment])

    def put_items(self, items: Sequence[ExperimentItem]) -> None:
        self._put("experiment_items", list(items))

    def put_scores(self, scores: Sequence[Score]) -> None:
        self._put("scores", list(scores))

    def cache_put(self, key: str, verdict: Dict[str, Any], ttl_s: Optional[float] = None) -> None:
        self.index.cache_put(key, verdict, None if ttl_s is None else time.time() + float(ttl_s))

    # -- read --------------------------------------------------------------

    def _before_read(self) -> None:
        """The files store reads what other writers appended first."""

    def list_experiments(
        self,
        where: Optional[ExperimentFilter] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> ExperimentPage:
        self._before_read()
        return self.index.list_experiments(where, limit, cursor)

    def get_experiment(self, experiment_id: str) -> Optional[ExperimentRecord]:
        self._before_read()
        return self.index.get_experiment(experiment_id)

    def scores(self, where: Optional[ScoreFilter] = None, limit: int = 10000) -> List[Score]:
        self._before_read()
        return self.index.scores(where, limit)

    def score_series(self, where: Optional[ScoreFilter], bucket_s: float) -> List[Bucket]:
        self._before_read()
        return self.index.score_series(where, bucket_s)

    def cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.index.cache_get(key)
