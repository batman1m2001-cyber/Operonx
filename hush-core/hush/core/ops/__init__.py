"""Core nodes cho hush workflow engine.

Bao gồm:
- BaseOp: Base class cho tất cả workflow nodes
- DummyOp: Node placeholder cho START/END markers
- GraphOp: Container quản lý graph với thực thi song song
- BranchOp: Conditional routing với precompiled conditions
- ForOp: Iterate qua collection tuần tự (sequential)
- MapOp: Iterate qua collection song song (parallel)
- WhileOp: Iterate khi condition còn true
- AIterOp: Xử lý streaming data với ordered output
- FuncOp: Thực thi Python functions
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
from .iteration.aiter_op import AIterOp
from .iteration.base import Each
from .iteration.for_op import ForOp
from .iteration.map_op import MapOp
from .iteration.while_op import WhileOp
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
    "Each",
    "ForOp",
    "MapOp",
    "WhileOp",
    "AIterOp",
    # Transform
    "FuncOp",
    "op",
    "ParserOp",
    "ParserType",
]
