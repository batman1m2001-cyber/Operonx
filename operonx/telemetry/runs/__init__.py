"""Run stores — where finished runs live, and how they are asked for.

A :class:`RunStore` keeps each run twice over: in full (every execution
with its values) and as a summary with per-op rollups, so one run opens
fully and a month of runs summarises without opening any. It is a trace
consumer, so the engine writes into it like into any other; it is a
resource (``run_store:`` in ``resources.yaml``), so a project picks its
backend in configuration:

* ``files`` — run directories plus a SQLite index (the default)
* ``sqlite`` — one file
* ``langfuse`` — read-only, over runs a LangfuseConsumer shipped

The contract is five methods on purpose; see :mod:`.base`.
"""

from operonx.core.registry import REGISTRY

from .base import GROUP_FIELDS, ORDERS, RunStore, combine_rollups
from .config import BACKENDS, RunStoreConfig, create_run_store, open_run_store
from .model import (
    OpRollup,
    OpStats,
    Page,
    RunFilter,
    RunRecord,
    RunSummary,
    percentile,
    summarize,
)
from .retention import DEFAULT_RETENTION, apply_retention

__all__ = [
    "BACKENDS",
    "DEFAULT_RETENTION",
    "GROUP_FIELDS",
    "ORDERS",
    "OpRollup",
    "OpStats",
    "Page",
    "RunFilter",
    "RunRecord",
    "RunStore",
    "RunStoreConfig",
    "RunSummary",
    "apply_retention",
    "combine_rollups",
    "create_run_store",
    "open_run_store",
    "percentile",
    "summarize",
]

REGISTRY.register(RunStoreConfig, create_run_store)
