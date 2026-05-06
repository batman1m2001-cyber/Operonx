"""Built-in processors for the trace pipeline.

Each is a callable ``Iterable[TraceEvent] -> Iterable[TraceEvent]``.
Compose by ordering them in ``TracePipeline(processors=[...])`` — execution
goes left-to-right, each event passing through every processor before the
next event is processed (lazy, generator-driven).

See ``docs/TRACING_REDESIGN_PLAN.md`` §3.3.
"""

from operonx.core.tracing.processors.drop import (
    DropEmpty,
    DropKinds,
    DropOps,
    KeepOps,
)
from operonx.core.tracing.processors.group import Aggregate, GroupBy
from operonx.core.tracing.processors.redact import RedactKeys
from operonx.core.tracing.processors.sample import Sample
from operonx.core.tracing.processors.truncate import TruncateIO

__all__ = [
    "Aggregate",
    "DropEmpty",
    "DropKinds",
    "DropOps",
    "GroupBy",
    "KeepOps",
    "RedactKeys",
    "Sample",
    "TruncateIO",
]
