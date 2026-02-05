"""Các node iteration cho loop và streaming.

Bao gồm:
- ForLoopNode: Lặp qua collection tuần tự (sequential)
- MapNode: Lặp qua collection song song (parallel)
- WhileLoopNode: Lặp khi điều kiện còn đúng
- AsyncIterNode: Xử lý streaming data theo thứ tự
- Each: Marker wrapper để đánh dấu nguồn iteration
- BaseIterationNode: Base class cho các iteration nodes
"""

from hush.core.nodes.iteration.base import Each, BaseIterationNode
from hush.core.nodes.iteration.for_loop_node import ForLoopNode, for_
from hush.core.nodes.iteration.map_node import MapNode, map_
from hush.core.nodes.iteration.while_loop_node import WhileLoopNode, while_
from hush.core.nodes.iteration.async_iter_node import AsyncIterNode, aiter_

__all__ = [
    "Each",
    "BaseIterationNode",
    "ForLoopNode",
    "for_",
    "MapNode",
    "map_",
    "WhileLoopNode",
    "while_",
    "AsyncIterNode",
    "aiter_",
]
