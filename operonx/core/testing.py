"""Public test helpers for unit tests that bypass the scheduler.

Use ``scratch_active(state)`` to bind ``SCRATCH`` to a state instance when
calling op bodies directly (e.g. via ``op.__wrapped__(...)``). Without this
the SCRATCH accessor's ContextVar is unbound and reads return ``ScratchRef``
markers instead of live values.
"""

from contextlib import contextmanager

from operonx.core.states._scratch_var import _reset_state, _set_state

__all__ = ["scratch_active"]


@contextmanager
def scratch_active(state):
    """Bind ``state`` as the active MemoryState for the duration of the block.

    Example::

        state = engine._schema.create_state()
        with scratch_active(state):
            state.scratch["coord:phase"] = "MAIN"
            result = my_op.__wrapped__(text)   # SCRATCH reads from `state`
    """
    token = _set_state(state)
    try:
        yield state
    finally:
        _reset_state(token)
