"""Core nodes cho hush workflow engine.

Bao gồm:
- BaseOp: Base class cho tất cả workflow nodes
- DummyOp: Node placeholder cho START/END markers
- GraphOp: Container quản lý graph với thực thi song song
- BranchOp: Conditional routing với precompiled conditions
- FuncOp: Thực thi Python functions (supports generators for streaming)
- ParserOp: Extract structured data từ text
"""

from hush.core.configs.op_config import OpType

from .base import (
    END,
    PARENT,
    START,
    BaseOp,
    DummyOp,
    SoftEdge,
    shorthand,
    split_shorthand_kwargs,
)
from .flow.branch_op import Branch, BranchOp, if_
from .graph.graph_op import GraphOp, graph
from .transform.func_op import FuncOp, op
from .transform.parser_op import ParserOp, ParserType

__all__ = [
    # Base
    "BaseOp",
    "DummyOp",
    "SoftEdge",
    "OpType",
    # Markers
    "START",
    "END",
    "PARENT",
    # Utilities
    "shorthand",
    "split_shorthand_kwargs",
    # Graph
    "GraphOp",
    "graph",
    # Flow control
    "BranchOp",
    "Branch",
    "if_",
    # Transform
    "FuncOp",
    "op",
    "ParserOp",
    "ParserType",
]
