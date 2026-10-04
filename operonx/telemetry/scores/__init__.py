"""Score stores — where experiments, their items, and every score live.

An eval run is an **experiment**; each case × repeat is an **item**; each
judgement — an eval's check, a judge, a human review, an online rule, a
pairwise preference — is a **score**, one row type with a target (item,
trace, op, session, pair) and an id derived from what it judges, so the
same judgement written twice is one row. A :class:`ScoreStore` keeps them
on the run stores' backends:

* ``files`` — JSONL files plus a SQLite index (the default, zero setup)
* ``sqlite`` — one file
* ``clickhouse`` — the runs' database, schema version 3

``score_store:`` in ``resources.yaml`` declares one; :func:`open_score_store`
opens one from a mapping; :func:`project_score_store` says which one a
project uses (``[evals]``, else its ``[tracing]`` sinks). The contract is in :mod:`.base`.
"""

from operonx.core.registry import REGISTRY

from .base import ONLINE_TTL_DAYS, ScoreStore
from .config import BACKENDS, ScoreStoreConfig, create_score_store, open_score_store
from .model import (
    DATA_TYPES,
    SOURCES,
    TARGETS,
    Bucket,
    Experiment,
    ExperimentFilter,
    ExperimentItem,
    ExperimentPage,
    ExperimentRecord,
    Score,
    ScoreFilter,
    score_id_of,
)
from .project import ScoreStoreSource, project_score_store

__all__ = [
    "BACKENDS",
    "DATA_TYPES",
    "ONLINE_TTL_DAYS",
    "SOURCES",
    "TARGETS",
    "Bucket",
    "Experiment",
    "ExperimentFilter",
    "ExperimentItem",
    "ExperimentPage",
    "ExperimentRecord",
    "Score",
    "ScoreFilter",
    "ScoreStore",
    "ScoreStoreConfig",
    "ScoreStoreSource",
    "create_score_store",
    "open_score_store",
    "project_score_store",
    "score_id_of",
]

REGISTRY.register(ScoreStoreConfig, create_score_store)
