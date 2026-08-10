"""Checkpointing + observability event bus for Phase 2.

Subscribe an :class:`InMemoryCheckpointer` to a run to record every cell
write and inspect state per-step post-run::

    from operonx.checkpoint import InMemoryCheckpointer

    cp = InMemoryCheckpointer()
    engine = Operon(graph)
    result = await engine.run(inputs, checkpointer=cp)

    for step in cp.list_steps():
        print(f"step {step}: {cp.get_updates(step)}")
    full_state_at_3 = cp.get_state(step=3)
"""

from operonx.checkpoint.base import (
    CellWriteEvent,
    Checkpointer,
    CustomEvent,
    InterruptEvent,
    ObserveBudgetExceeded,
    ScratchWriteEvent,
    StepEvent,
    StepNotFound,
)
from operonx.checkpoint.bridge import (
    bind_checkpointer,
    bind_custom_bus,
    bind_interrupt_bus,
)
from operonx.checkpoint.memory import InMemoryCheckpointer

__all__ = [
    "Checkpointer",
    "InMemoryCheckpointer",
    "CellWriteEvent",
    "ScratchWriteEvent",
    "StepEvent",
    "InterruptEvent",
    "CustomEvent",
    "StepNotFound",
    "ObserveBudgetExceeded",
    "bind_checkpointer",
    "bind_custom_bus",
    "bind_interrupt_bus",
]
