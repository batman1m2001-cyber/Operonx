"""Jobs: running Operons over data that does not talk back.

`[[serve]]` puts work into a graph from a listener. A :class:`Job` puts
it in from a source — a file, a table, a Python iterable — one run per
item, writes what `egress` sends to a sink, and leaves a record saying
what happened to every item. The graph is the same graph; see
:mod:`operonx.app.jobs.session` for why.

::

    from operonx.app.jobs import Job

    score = Job("score_calls", graph=score_call,
                source="data/calls.jsonl", sink="out/scores.jsonl",
                key="call_id", on_error="skip")
    run = score.run_sync()
    print(run.summary())            # score_calls 2026…  ok=98 failed=2 empty=0 skipped=0

Design: ``docs/JOB_PLAN.md``.
"""

from operonx.core.registry import REGISTRY

from .job import SESSION_MODES, Job
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
    runs_of,
)
from .runbook import NodeReport, Parallel, Runbook, RunbookRun, Sequential
from .runner import ErrorPolicy, parse_on_error, run_job, run_per_item, run_stream
from .session import JobSession
from .sinks import (
    CsvSink,
    JsonlSink,
    ListSink,
    NullSink,
    PythonSink,
    Sink,
    SinkConfig,
    as_sink,
    create_sink,
    open_sink,
)
from .sources import (
    CsvSource,
    JsonlSource,
    PythonSource,
    Source,
    SourceConfig,
    as_source,
    create_source,
    open_source,
)

__all__ = [
    "Job",
    "JobRun",
    "JobSession",
    "ItemResult",
    "RunRecord",
    "Runbook",
    "RunbookRun",
    "Sequential",
    "Parallel",
    "NodeReport",
    "SESSION_MODES",
    "ErrorPolicy",
    "parse_on_error",
    "run_job",
    "run_per_item",
    "run_stream",
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
    "runs_of",
    "Source",
    "JsonlSource",
    "CsvSource",
    "PythonSource",
    "SourceConfig",
    "as_source",
    "create_source",
    "open_source",
    "Sink",
    "JsonlSink",
    "CsvSink",
    "ListSink",
    "PythonSink",
    "NullSink",
    "SinkConfig",
    "as_sink",
    "create_sink",
    "open_sink",
    "register",
]


def register() -> None:
    """Make ``source:`` and ``sink:`` resource categories known to the hub.

    Idempotent, and safe to call again after a registry reset — the two
    ``as_*`` resolvers call it before a hub lookup so a test that cleared
    the registry still resolves.
    """
    REGISTRY.register(SourceConfig, create_source)
    REGISTRY.register(SinkConfig, create_sink)


register()
