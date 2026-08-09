"""Bridge helpers: adapt ``MemoryState`` observer callbacks to checkpointer events.

``MemoryState.subscribe_writes`` receives ``(idx, ctx_key, value)``. The
checkpointer wants ``CellWriteEvent(step_id, op, var, ctx, value)``. The
bridge translates one to the other, resolving op/var names from the
schema and consulting a scheduler-provided ``step_id_getter``.

Typical wiring inside the engine::

    def _step_id_getter():
        return scheduler.current_step

    unsubscribe = bind_checkpointer(state, checkpointer, _step_id_getter)
    ...  # run
    unsubscribe()
"""

from typing import Callable, Tuple

from operonx.checkpoint.base import CellWriteEvent, Checkpointer
from operonx.core.states.state import MemoryState

__all__ = ["bind_checkpointer"]


def bind_checkpointer(
    state: MemoryState,
    checkpointer: Checkpointer,
    step_id_getter: Callable[[], int],
) -> Callable[[], None]:
    """Subscribe ``checkpointer`` to every cell write on ``state``.

    Returns an ``unsubscribe`` callable — call it when the run finishes to
    detach the observer.

    Args:
        state: the MemoryState of an active run
        checkpointer: any object satisfying the :class:`Checkpointer` protocol
        step_id_getter: zero-arg function returning the current ``step_id``.
            Typically bound to the scheduler's monotonic step counter.

    Returns:
        A no-arg ``unsubscribe()`` function.
    """
    # Reverse index — resolve idx → (op, var) once per subscription setup.
    idx_to_key: dict[int, Tuple[str, str]] = {}
    for (op, var), idx in state.schema._var_to_idx.items():
        idx_to_key[idx] = (op, var)

    def _observer(idx: int, ctx_key: tuple, value) -> None:
        op, var = idx_to_key.get(idx, ("?", "?"))
        checkpointer.on_cell_write(
            CellWriteEvent(
                step_id=step_id_getter(),
                op=op,
                var=var,
                ctx=ctx_key,
                value=value,
            )
        )

    state.subscribe_writes(_observer)

    def _unsubscribe() -> None:
        state.unsubscribe_writes(_observer)

    return _unsubscribe
