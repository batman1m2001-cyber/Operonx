"""Quản lý state cho workflow v2 - thiết kế đơn giản dựa trên Cell.

Kiến trúc:
    StateSchema  - Định nghĩa cấu trúc với độ phân giải O(1) dựa trên index.
    MemoryState  - Lưu trữ trong bộ nhớ dựa trên Cell.
    Ref          - Tham chiếu với khả năng chain các operation.
    Cell         - Lưu trữ giá trị đa context.

Example:
    from hush.core.states import StateSchema, MemoryState

    # Từ graph
    schema = StateSchema(graph)
    state = MemoryState(schema, inputs={"x": 5})

    # Truy cập giá trị
    state["node", "var", None] = "value"
    value = state["node", "var", None]

    # Debug
    schema.show()
    state.show()
"""

from hush.core.states.ref import Ref
from hush.core.states.schema import StateSchema
from hush.core.states.state import MemoryState
from hush.core.states.cell import Cell

__all__ = [
    "Ref",
    "StateSchema",
    "MemoryState",
    "Cell",
]
