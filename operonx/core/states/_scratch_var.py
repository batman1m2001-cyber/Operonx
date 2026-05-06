"""ContextVar that binds the active MemoryState to the running call.

Set once by the scheduler at `_run_once` entry; read by `SCRATCH[k]` and the
post-resolve pass in `BaseOp.get_inputs()`. Inherited across
`asyncio.create_task` and `asyncio.to_thread` via PEP 567 — each call gets
its own task tree with an independent ContextVar value, so N concurrent
calls do not cross-pollinate.

Standalone — does not depend on the tracing module.
"""

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operonx.core.states.state import MemoryState


_current_state_var: contextvars.ContextVar = contextvars.ContextVar("operonx_current_state")


def _set_state(state: "MemoryState") -> contextvars.Token:
    return _current_state_var.set(state)


def _reset_state(token: contextvars.Token) -> None:
    _current_state_var.reset(token)
