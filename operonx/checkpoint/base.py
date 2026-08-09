"""Checkpointer protocol + event types for Phase 2 observability.

The checkpointer sits on the ``MemoryState._write_cell`` funnel as an
observer. Every cell write emits a :class:`CellWriteEvent`; the scheduler
also emits :class:`StepEvent` at write-batch boundaries, and
:class:`InterruptEvent` when an :class:`~operonx.InterruptOp` suspends.

A ``Checkpointer`` implementation subscribes to these events, records
deltas, and exposes replay/inspection APIs. See
:class:`operonx.checkpoint.memory.InMemoryCheckpointer`.

Design invariants (see docs/design/STATE_LOOP_REFACTOR_PLAN.md §Phase 2):
    - opt-in: no checkpointer bound → zero overhead on the write path
    - captures every emitted cell write (filter is at emission source via
      ``@op(exclude=..., include=...)``, not at the checkpointer)
    - delta storage, not full snapshots — replay folds deltas
    - step_id is monotonic per write batch (batch = one op invocation)
    - reducers apply BEFORE checkpoint capture (post-merge value recorded)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

__all__ = [
    "Checkpointer",
    "CellWriteEvent",
    "StepEvent",
    "InterruptEvent",
    "CustomEvent",
    "StepNotFound",
    "ObserveBudgetExceeded",
]


# ---------------------------------------------------------------------------
# Event types — plain dataclasses; observers receive these instances.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CellWriteEvent:
    """A single cell write, post-reducer.

    Attributes:
        step_id: monotonic step counter — increments per write batch
        op: full name of the op whose write triggered this event
        var: cell variable name
        ctx: context tuple (DEFAULT_CONTEXT for shared cells)
        value: post-reducer value now in the cell
    """

    step_id: int
    op: str
    var: str
    ctx: Tuple[str, ...]
    value: Any


@dataclass(frozen=True)
class StepEvent:
    """A write-batch boundary. Emitted when an op completes.

    Attributes:
        step_id: the batch that just finished
        op: the op whose invocation batch this represents
    """

    step_id: int
    op: str


@dataclass(frozen=True)
class InterruptEvent:
    """An :class:`~operonx.InterruptOp` suspended and is awaiting resume.

    Attributes:
        step_id: step at which the suspension happened
        op: full name of the InterruptOp
        ctx: context tuple of the suspended invocation
        payload: what the InterruptOp emits for the caller to inspect
        interrupt_id: unique id (for resume matching across parallel interrupts)
    """

    step_id: int
    op: str
    ctx: Tuple[str, ...]
    payload: Any
    interrupt_id: str


@dataclass(frozen=True)
class CustomEvent:
    """An :class:`~operonx.EmitOp` fired.

    Fire-and-forget — no wait, no back-pressure. If no subscriber is
    listening on ``mode="custom"``, the payload is dropped.

    Attributes:
        step_id: step at which the emission happened
        op: full name of the EmitOp
        ctx: context tuple
        channel: user-chosen channel string for filtering
        payload: user-supplied event payload
    """

    step_id: int
    op: str
    ctx: Tuple[str, ...]
    channel: str
    payload: Any


# ---------------------------------------------------------------------------
# Checkpointer protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Checkpointer(Protocol):
    """Observer that records per-step state deltas.

    Implementations subscribe to ``MemoryState``'s cell-write funnel.
    Every emitted :class:`CellWriteEvent` is passed to ``on_cell_write``;
    :class:`StepEvent` boundaries advance the step counter and
    ``get_updates`` / ``get_state`` reconstruct state from stored deltas.

    ``get_state(step)`` folds all deltas from step 0 up to ``step`` and
    returns the full materialized state. ``get_updates(step)`` returns
    just the delta at that step. ``list_steps()`` enumerates recorded steps.

    Optional ``on_cancel(ctx)`` records a cancellation event when a
    speculative chain is torn down. Prior deltas are preserved (audit
    trail); a synthetic marker is added at the current step.
    """

    def on_cell_write(self, event: CellWriteEvent) -> None:
        """Record a cell write. Called once per cell mutation."""
        ...

    def on_step(self, event: StepEvent) -> None:
        """Boundary marker at the end of a write batch."""
        ...

    def on_cancel(self, ctx: Tuple[str, ...]) -> None:
        """Speculative chain at ``ctx`` was cancelled by the scheduler."""
        ...

    def get_state(self, step: int) -> Dict[Tuple[str, str, Tuple[str, ...]], Any]:
        """Full state at ``step`` — fold deltas from step 0 up to ``step``.

        Raises:
            StepNotFound: if ``step`` is beyond the recorded range.
        """
        ...

    def get_updates(self, step: int) -> Dict[Tuple[str, str, Tuple[str, ...]], Any]:
        """Delta at ``step`` only. Empty dict if the step recorded no writes."""
        ...

    def list_steps(self) -> List[int]:
        """Recorded step ids in order."""
        ...


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class StepNotFound(KeyError):
    """Raised when ``get_state``/``get_updates`` asked for an unrecorded step."""

    def __init__(self, step: int, max_step: int) -> None:
        super().__init__(f"step {step} not found; max recorded step is {max_step}")
        self.step = step
        self.max_step = max_step


class ObserveBudgetExceeded(RuntimeError):
    """Raised when an op emits more events than its ``observe_max`` cap.

    Circuit breaker for runaway generator ops (e.g. ``frame_source`` yielding
    faster than expected). Silent sampling would hide the runaway bug; loud
    failure forces users to either raise the cap, add ``exclude=[...]`` for
    the noisy vars, or fix the underlying issue.

    Attributes:
        op: full name of the op that exceeded its budget
        count: number of events already emitted
        limit: the configured ``observe_max`` cap
    """

    def __init__(self, op: str, count: int, limit: int) -> None:
        super().__init__(
            f"op {op!r} emitted {count} events, exceeding observe_max={limit}. "
            f"Raise the cap, filter noisy vars via @op(exclude=[...]), or "
            f"investigate the runaway."
        )
        self.op = op
        self.count = count
        self.limit = limit
