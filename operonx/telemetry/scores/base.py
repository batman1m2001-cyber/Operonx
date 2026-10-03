"""The score store contract — where experiments, their items and scores live.

Its own small contract, beside the run store's five methods rather than a
sixth through tenth on it: the two share backends and configuration (one
ClickHouse database holds both), not a class. Like the run store it has
no query language — filters are data objects (:class:`ExperimentFilter`,
:class:`ScoreFilter`) every backend implements natively — and its methods
are synchronous; async code wraps a call in ``asyncio.to_thread``.

Writes are idempotent: an experiment is upserted by id (a running row,
then the finished one), an item by (experiment, case, repeat), a score by
``score_id`` — the row written last wins.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

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

__all__ = ["ONLINE_TTL_DAYS", "ScoreStore"]

#: How long an online score (one with a ``rule``) is kept. Longer than a
#: service's traces (30 days): the score's ``snapshot`` outlives them.
#: Eval and human scores are kept forever.
ONLINE_TTL_DAYS = 365


class ScoreStore(ABC):
    """See the module docstring."""

    # -- experiments -------------------------------------------------------

    @abstractmethod
    def put_experiment(self, experiment: Experiment) -> None:
        """Write *experiment*, replacing any earlier row of its id."""

    @abstractmethod
    def put_items(self, items: Sequence[ExperimentItem]) -> None:
        """Write *items*, each replacing its (experiment, case, repeat)."""

    @abstractmethod
    def list_experiments(
        self,
        where: Optional[ExperimentFilter] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> ExperimentPage:
        """Experiments matching *where*, newest first, one page at a time."""

    @abstractmethod
    def get_experiment(self, experiment_id: str) -> Optional[ExperimentRecord]:
        """One experiment with its items, or ``None``."""

    # -- scores ------------------------------------------------------------

    @abstractmethod
    def put_scores(self, scores: Sequence[Score]) -> None:
        """Write *scores*, each replacing any earlier row of its ``score_id``."""

    @abstractmethod
    def scores(self, where: Optional[ScoreFilter] = None, limit: int = 10000) -> List[Score]:
        """Scores matching *where*, oldest first (``created_at``, ``score_id``).
        An online score past its retention is not returned."""

    @abstractmethod
    def score_series(self, where: Optional[ScoreFilter], bucket_s: float) -> List[Bucket]:
        """Scores matching *where* in buckets of *bucket_s* seconds of
        ``created_at``, per score name: oldest bucket first."""

    # -- the judge cache -----------------------------------------------------

    @abstractmethod
    def cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        """The verdict cached under *key*, or ``None`` (missing or expired)."""

    @abstractmethod
    def cache_put(self, key: str, verdict: Dict[str, Any], ttl_s: Optional[float] = None) -> None:
        """Cache *verdict* under *key*; ``ttl_s`` ``None`` keeps it until replaced."""

    # -- housekeeping --------------------------------------------------------

    def refresh(self) -> int:
        """Pick up what other writers left (the files backend reads lines
        other processes appended). Returns how many rows; most backends
        have nothing to do."""
        return 0

    def close(self) -> None:
        """Release connections. Idempotent."""
