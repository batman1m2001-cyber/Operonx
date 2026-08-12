"""Bridge helpers: adapt ``MemoryState`` observer callbacks to checkpointer events.

``MemoryState.subscribe_writes`` receives ``(idx, ctx_key, value)``. The
checkpointer wants ``CellWriteEvent(step_id, op, var, ctx, value)``. The
bridge translates one to the other, resolving op/var names from the
schema and consulting a scheduler-provided ``step_id_getter``.

The bridge also honours per-op :func:`@op(exclude=/include=/observe_max=)
<operonx.core.ops.transform.func_op.op>` filters when an ``op_registry``
mapping is provided — the channel filter here is fixed to
``"checkpoint"``. See :class:`ObserveBudgetExceeded` for the runaway-op
circuit breaker.

Typical wiring inside the engine::

    def _step_id_getter():
        return scheduler.current_step

    unsubscribe = bind_checkpointer(
        state, checkpointer, _step_id_getter, op_registry=graph._all_ops
    )
    ...  # run
    unsubscribe()
"""

from typing import Callable, Dict, Optional, Tuple

from operonx.checkpoint.base import (
    CellWriteEvent,
    Checkpointer,
    CustomEvent,
    InterruptEvent,
    ObserveBudgetExceeded,
    ScratchWriteEvent,
)
from operonx.core.ops.base import should_emit_for_channel
from operonx.core.states.state import MemoryState

__all__ = [
    "bind_checkpointer",
    "bind_observe_budget",
    "should_emit_for_channel",
    "bind_custom_bus",
    "bind_interrupt_bus",
]


# Re-exported from core: the V3 tracer consults the same predicate, and
# core cannot import this package. Kept importable here because this was
# the original home and callers reference ``bridge.should_emit_for_channel``.


def bind_checkpointer(
    state: MemoryState,
    checkpointer: Checkpointer,
    step_id_getter: Optional[Callable[[], int]] = None,
    op_registry: Optional[Dict[str, object]] = None,
) -> Callable[[], None]:
    """Subscribe ``checkpointer`` to every cell write on ``state``.

    Returns an ``unsubscribe`` callable — call it when the run finishes to
    detach the observer.

    Args:
        state: the MemoryState of an active run
        checkpointer: any object satisfying the :class:`Checkpointer` protocol
        step_id_getter: zero-arg function returning the current ``step_id``.
            Typically bound to the scheduler's monotonic step counter.
        op_registry: optional ``{full_name: op_instance}`` map so the bridge
            can honour per-op ``exclude``/``include``/``observe_max`` filters.
            Without it, every write is captured unconditionally.

    Returns:
        A no-arg ``unsubscribe()`` function.
    """
    # Reverse index — resolve idx → (op, var) once per subscription setup.
    idx_to_key: dict[int, Tuple[str, str]] = {}
    for (op, var), idx in state.schema._var_to_idx.items():
        idx_to_key[idx] = (op, var)

    # Default step_id: read the state's monotonic counter directly.
    if step_id_getter is None:
        step_id_getter = lambda: state._current_step

    op_registry = op_registry or {}

    def _observer(idx: int, ctx_key: tuple, value) -> None:
        op_name, var = idx_to_key.get(idx, ("?", "?"))
        op_instance = op_registry.get(op_name)

        if not should_emit_for_channel(op_instance, var, "checkpoint"):
            return

        # The observe_max circuit breaker used to live here. It counted
        # only while a checkpointer was bound, so a runaway generator under
        # a plain ``engine.run()`` was uncapped — see bind_observe_budget,
        # which the engine binds on every run instead.
        checkpointer.on_cell_write(
            CellWriteEvent(
                step_id=step_id_getter(),
                op=op_name,
                var=var,
                ctx=ctx_key,
                value=value,
            )
        )

    state.subscribe_writes(_observer)

    # Phase 2b3 B1: also route SCRATCH mutations through the checkpointer.
    # The checkpointer records them under the synthetic key
    # ("__scratch__", key, DEFAULT_CONTEXT) so replay/inspection is uniform
    # with cell writes.
    def _scratch_observer(key: str, value) -> None:
        checkpointer.on_cell_write(
            CellWriteEvent(
                step_id=step_id_getter(),
                op="__scratch__",
                var=key,
                ctx=("main",),
                value=value,
            )
        )

    state.subscribe_scratch(_scratch_observer)

    def _unsubscribe() -> None:
        state.unsubscribe_writes(_observer)
        state.unsubscribe_scratch(_scratch_observer)

    return _unsubscribe


def bind_observe_budget(
    state: MemoryState,
    op_registry: Dict[str, object],
) -> Optional[Callable[[], None]]:
    """Enforce ``@op(observe_max=N)`` for the duration of a run.

    Returns an unsubscribe callable, or ``None`` when no op in the graph
    declares a budget — in which case nothing is subscribed and the run
    pays nothing.

    This is separate from :func:`bind_checkpointer`, where the counter
    used to live. That closure only exists when a checkpointer is passed
    to ``engine.start()``, so ``observe_max`` silently did nothing under
    ``engine.run()`` — the cheap path, and the one a runaway generator is
    most likely to be running in. Measured before the split: 50 frames
    emitted against a budget of 5, no error.

    :class:`ObserveBudgetExceeded` is a ``BaseException``, so it bypasses
    ``BaseOp.run``'s ``except Exception`` and halts the run rather than
    being recorded as an op error.
    """
    budgets: Dict[str, int] = {}
    for name, op_instance in op_registry.items():
        limit = getattr(op_instance, "observe_max", None)
        if limit is not None:
            budgets[name] = limit
    if not budgets:
        return None

    # Only vars that reach at least one observer count against the budget.
    # ``ObserveBudgetExceeded``'s own message offers ``@op(exclude=[...])``
    # as a remedy, so a fully-silenced var has to be free — otherwise the
    # advice in the error is wrong.
    counted_idx: Dict[int, str] = {}
    for (op_name, var), idx in state.schema._var_to_idx.items():
        if op_name not in budgets:
            continue
        op_instance = op_registry[op_name]
        if should_emit_for_channel(op_instance, var, "checkpoint") or should_emit_for_channel(
            op_instance, var, "trace"
        ):
            counted_idx[idx] = op_name

    counts: Dict[str, int] = {}

    def _observer(idx: int, _ctx_key: tuple, _value) -> None:
        op_name = counted_idx.get(idx)
        if op_name is None:
            return
        count = counts.get(op_name, 0) + 1
        counts[op_name] = count
        if count > budgets[op_name]:
            raise ObserveBudgetExceeded(op=op_name, count=count, limit=budgets[op_name])

    state.subscribe_writes(_observer)
    return lambda: state.unsubscribe_writes(_observer)


def bind_custom_bus(
    state: MemoryState,
    sink: Callable[[CustomEvent], None],
    step_id_getter: Optional[Callable[[], int]] = None,
    op_registry: Optional[Dict[str, object]] = None,
    channel: str = "custom",
) -> Callable[[], None]:
    """Subscribe ``sink`` to ``EmitOp`` events on ``state``.

    The sink receives fully-formed :class:`CustomEvent` instances; step_id
    is filled from ``state._current_step`` unless overridden.

    When ``op_registry`` is supplied, per-op ``@op(exclude=..., include=...)``
    filters are honoured against the ``channel`` argument (default
    ``"custom"``) — so an op declared with ``include={"trace": ["x"]}``
    can silence its EmitOp payloads from custom-mode subscribers. This
    closes the T7 gap from the Phase 2b3 review.

    Returns an ``unsubscribe`` callable.
    """
    if step_id_getter is None:
        step_id_getter = lambda: state._current_step
    op_registry = op_registry or {}

    def _observer(op_name: str, ctx: tuple, evt_channel: str, payload) -> None:
        op_instance = op_registry.get(op_name)
        # For EmitOp we filter on the EMITTED channel name, not var name —
        # match against `channel` (the observer's channel kind) using the
        # per-op filter's ``payload`` bucket.
        if not should_emit_for_channel(op_instance, "payload", channel):
            return
        sink(
            CustomEvent(
                step_id=step_id_getter(),
                op=op_name,
                ctx=ctx,
                channel=evt_channel,
                payload=payload,
            )
        )

    state.subscribe_custom(_observer)
    return lambda: state.unsubscribe_custom(_observer)


def bind_interrupt_bus(
    state: MemoryState,
    sink: Callable[[InterruptEvent], None],
    step_id_getter: Optional[Callable[[], int]] = None,
    op_registry: Optional[Dict[str, object]] = None,
    channel: str = "trace",
) -> Callable[[], None]:
    """Subscribe ``sink`` to ``InterruptOp`` events on ``state``.

    Sink receives fully-formed :class:`InterruptEvent` instances. Caller
    is responsible for arranging the resume value via
    ``state.resume_interrupt(interrupt_id, value)``.

    When ``op_registry`` is supplied, per-op filters are honoured against
    the ``channel`` argument (default ``"trace"``). An InterruptOp declared
    with ``include=[]`` will not surface its InterruptEvents at all — the
    caller-side HITL flow becomes a no-op for that op.
    """
    if step_id_getter is None:
        step_id_getter = lambda: state._current_step
    op_registry = op_registry or {}

    def _observer(op_name: str, ctx: tuple, payload, interrupt_id: str) -> None:
        op_instance = op_registry.get(op_name)
        if not should_emit_for_channel(op_instance, "payload", channel):
            return
        sink(
            InterruptEvent(
                step_id=step_id_getter(),
                op=op_name,
                ctx=ctx,
                payload=payload,
                interrupt_id=interrupt_id,
            )
        )

    state.subscribe_interrupt(_observer)
    return lambda: state.unsubscribe_interrupt(_observer)
