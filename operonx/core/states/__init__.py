"""State management for workflows — simple Cell-based design.

Architecture:
    StateSchema  - Defines structure with O(1) index-based resolution.
    MemoryState  - In-memory Cell-based storage.
    Ref          - Zero-copy references with chainable operations.
    Cell         - Multi-context value storage.

Example:
    from operonx.core.states import StateSchema, MemoryState

    schema = StateSchema(graph)
    state = MemoryState(schema, inputs={"x": 5})

    # Access values (2-tuple for default context, 3-tuple for explicit)
    state["node", "var"] = "value"
    value = state["node", "var"]
    state["node", "var", "[0]"] = "iteration value"

    # Debug
    schema.show()
    state.show()
"""

from operonx.core.states.cell import Cell
from operonx.core.states.ref import Ref
from operonx.core.states.schema import StateSchema
from operonx.core.states.state import MemoryState

__all__ = [
    "Ref",
    "StateSchema",
    "MemoryState",
    "Cell",
]
