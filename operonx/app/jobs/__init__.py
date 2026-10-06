"""Jobs: running Operons over data that does not talk back.

A service puts work into a graph from a listener. A :class:`Job` puts it in
from its ``items`` — a list, a function that yields them, a ``.jsonl`` file
— one run per item, keeps every result, and leaves a record saying what
happened to each item::

    from operonx.app.jobs import Job

    greet = Job("greet", graph=greet_flow, items="people.jsonl", key="id")
    run = greet.run_sync()
    print(run.summary())        # greet 2026…  ok=98 failed=2 empty=0 skipped=0
    run.results                 # {key: result}

``reduce=`` runs one graph over every result; ``steps=[...]`` runs jobs in
order as one command. Design: ``docs/JOBS_AND_GUIDES_PLAN.md``.
"""

from .items import iter_items
from .job import ON_ERROR, Job, default_record_dir
from .record import (
    ITEM_EMPTY,
    ITEM_FAILED,
    ITEM_OK,
    ITEM_SKIPPED,
    ITEM_TIMEOUT,
    RUN_FAILED,
    RUN_OK,
    RUN_STOPPED,
    ItemResult,
    JobRun,
    RunRecord,
    done_keys,
    last_run,
    load_results,
    runs_of,
)
from .runner import run_job, run_steps
from .session import JobSession

__all__ = [
    "Job",
    "JobRun",
    "JobSession",
    "ItemResult",
    "RunRecord",
    "ON_ERROR",
    "default_record_dir",
    "iter_items",
    "run_job",
    "run_steps",
    "ITEM_OK",
    "ITEM_FAILED",
    "ITEM_EMPTY",
    "ITEM_SKIPPED",
    "ITEM_TIMEOUT",
    "RUN_OK",
    "RUN_FAILED",
    "RUN_STOPPED",
    "done_keys",
    "last_run",
    "load_results",
    "runs_of",
]
