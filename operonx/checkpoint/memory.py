"""In-memory checkpointer — the default for local dev + Phase 2 testing."""

from typing import Any, Dict, List, Tuple

from operonx.checkpoint.base import (
    CellWriteEvent,
    Checkpointer,
    StepEvent,
    StepNotFound,
)

__all__ = ["InMemoryCheckpointer"]


CellKey = Tuple[str, str, Tuple[str, ...]]


class InMemoryCheckpointer(Checkpointer):
    """Records per-step cell-write deltas in memory.

    Design:
        - ``_deltas[step_id]`` = dict of ``(op, var, ctx) -> value`` writes
          committed at that step
        - ``get_state(step)`` folds all deltas from step 0 up to ``step``
        - ``get_updates(step)`` returns just the delta at that step
        - Optional ``sample_every=N`` keeps only every Nth step's delta
          (useful for very long runs where full-fidelity storage costs
          too much)

    Args:
        sample_every: If set, only step_ids where ``step % sample_every == 0``
            are recorded. Non-sampled steps drop their writes silently.
            Default: None (record every step).

    Example::

        cp = InMemoryCheckpointer()
        engine.run(inputs, checkpointer=cp)

        cp.list_steps()        # [0, 1, 2, ...]
        cp.get_updates(step=3) # {(op, var, ctx): value, ...}
        cp.get_state(step=3)   # full state at step 3
    """

    __slots__ = ("_deltas", "_cancels", "_sample_every", "_current_step")

    def __init__(self, *, sample_every: int = None) -> None:
        # step_id → {(op, var, ctx): value}
        self._deltas: Dict[int, Dict[CellKey, Any]] = {}
        # (ctx) → step_id when cancel was recorded (audit trail; state unchanged)
        self._cancels: Dict[Tuple[str, ...], int] = {}
        self._sample_every = sample_every
        self._current_step: int = 0

    # ------------------------------------------------------------------
    # Observer callbacks — called by the funnel/scheduler
    # ------------------------------------------------------------------

    def on_cell_write(self, event: CellWriteEvent) -> None:
        """Record a single cell write at the event's step_id."""
        if self._sample_every and event.step_id % self._sample_every != 0:
            return
        step_deltas = self._deltas.setdefault(event.step_id, {})
        step_deltas[(event.op, event.var, event.ctx)] = event.value
        self._current_step = max(self._current_step, event.step_id)

    def on_step(self, event: StepEvent) -> None:
        """Advance the recorded step counter (no-op if no writes)."""
        # Ensure the step exists in _deltas even if empty (list_steps sees it).
        if self._sample_every and event.step_id % self._sample_every != 0:
            return
        self._deltas.setdefault(event.step_id, {})
        self._current_step = max(self._current_step, event.step_id)

    def on_cancel(self, ctx: Tuple[str, ...]) -> None:
        """Record a cancellation event at the current step."""
        self._cancels[ctx] = self._current_step

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def list_steps(self) -> List[int]:
        """Recorded step ids, ascending."""
        return sorted(self._deltas)

    def get_updates(self, step: int) -> Dict[CellKey, Any]:
        """Return the delta recorded at ``step`` — empty dict if none."""
        if step not in self._deltas:
            if step > self._current_step:
                raise StepNotFound(step=step, max_step=self._current_step)
            return {}
        return dict(self._deltas[step])

    def get_state(self, step: int) -> Dict[CellKey, Any]:
        """Fold deltas from step 0 up to ``step`` into a full snapshot.

        Raises:
            StepNotFound: if ``step`` exceeds the last recorded step.
        """
        if step > self._current_step:
            raise StepNotFound(step=step, max_step=self._current_step)
        merged: Dict[CellKey, Any] = {}
        for s in sorted(self._deltas):
            if s > step:
                break
            merged.update(self._deltas[s])
        return merged

    def list_cancels(self) -> Dict[Tuple[str, ...], int]:
        """Cancelled ctxs and the step at which they were cancelled."""
        return dict(self._cancels)
