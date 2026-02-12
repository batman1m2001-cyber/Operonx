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

from hush.core.configs.node_config import NodeType

from .base import (
    END,
    PARENT,
    START,
    BaseNode,
    DummyNode,
    SoftEdge,
    shorthand,
    split_shorthand_kwargs,
)
from .flow.branch_node import Branch, BranchNode, if_
from .graph.graph_node import GraphNode, subgraph
from .iteration.async_iter_node import AsyncIterNode
from .iteration.base import Each
from .iteration.for_loop_node import ForLoopNode
from .iteration.map_node import MapNode
from .iteration.while_loop_node import WhileLoopNode
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
    "shorthand",
    "split_shorthand_kwargs",
    # Graph
    "GraphNode",
    "subgraph",
    # Flow control
    "BranchNode",
    "Branch",
    "if_",
    "Each",
    "ForLoopNode",
    "MapNode",
    "WhileLoopNode",
    "AsyncIterNode",
    # Transform
    "CodeNode",
    "code_node",
    "ParserNode",
    "ParserType",
]
