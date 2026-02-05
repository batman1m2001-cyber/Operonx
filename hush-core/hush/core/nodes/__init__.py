"""Core nodes cho hush workflow engine.

Bao gồm:
- BaseNode: Base class cho tất cả workflow nodes
- DummyNode: Node placeholder cho START/END markers
- GraphNode: Container quản lý subgraph với thực thi song song
- BranchNode: Conditional routing với precompiled conditions
- ForLoopNode: Iterate qua collection tuần tự (sequential)
- MapNode: Iterate qua collection song song (parallel)
- WhileLoopNode: Iterate khi condition còn true
- AsyncIterNode: Xử lý streaming data với ordered output
- CodeNode: Thực thi Python functions
- ParserNode: Extract structured data từ text
"""

from .base import (
    BaseNode,
    DummyNode,
    SoftEdge,
    START,
    END,
    PARENT,
    split_shorthand_kwargs,
)
from hush.core.configs.node_config import NodeType
from .graph.graph_node import GraphNode
from .flow.branch_node import BranchNode, Branch, if_
from .iteration.base import Each
from .iteration.for_loop_node import ForLoopNode, for_
from .iteration.map_node import MapNode, map_
from .iteration.while_loop_node import WhileLoopNode, while_
from .iteration.async_iter_node import AsyncIterNode, aiter_
from .transform.code_node import CodeNode, code_node
from .transform.parser_node import ParserNode, ParserType

__all__ = [
    # Base
    "BaseNode",
    "DummyNode",
    "SoftEdge",
    "NodeType",
    # Markers
    "START",
    "END",
    "PARENT",
    # Utilities
    "split_shorthand_kwargs",
    # Graph
    "GraphNode",
    # Flow control
    "BranchNode",
    "Branch",
    "if_",
    "Each",
    "ForLoopNode",
    "for_",
    "MapNode",
    "map_",
    "WhileLoopNode",
    "while_",
    "AsyncIterNode",
    "aiter_",
    # Transform
    "CodeNode",
    "code_node",
    "ParserNode",
    "ParserType",
]
