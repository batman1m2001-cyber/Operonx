"""A trace consumer that keeps every run's trace in memory, for counting op runs.

The incremental gates are measured from traces ("re-ingest of an unchanged
corpus runs 0 parse ops"), not from counters inside the code under test.
"""

from __future__ import annotations

from typing import Any, List

from operonx.core.workflow_trace import WorkflowTrace
from operonx.telemetry.consumer import Consumer

__all__ = ["RecordingConsumer"]


class RecordingConsumer(Consumer):
    """Collects traces; :meth:`runs` counts executions of an op by name."""

    def __init__(self) -> None:
        super().__init__()
        self.traces: List[WorkflowTrace] = []

    def consume(self, trace: WorkflowTrace) -> Any:
        self.traces.append(trace)
        return None

    def runs(self, op_name: str) -> int:
        return sum(1 for t in self.traces for n in t.nodes if n.op_name == op_name and not n.is_yield)

    def clear(self) -> None:
        self.traces.clear()
