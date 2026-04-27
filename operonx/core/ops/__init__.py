"""Core op types and markers for the Operon workflow engine.

- ``BaseOp``        — base class for all workflow ops
- ``DummyOp``       — placeholder for START/END markers
- ``GraphOp``       — container managing a sub-graph with parallel execution
- ``BranchOp``      — conditional routing with precompiled conditions
- ``FuncOp``        — wraps a Python function (supports generators for streaming)
- ``ParserOp``      — extracts structured data from text (XML/JSON/etc.)

Markers: ``START``, ``END``, ``PARENT``, ``PENDING``.
Decorators: ``op``, ``graph``, ``if_``.
"""

from operonx.core.configs.op_config import OpType

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


class _PendingSentinel:
    """Sentinel returned by ops that absorb input without producing output."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "PENDING"

    def __bool__(self) -> bool:
        return False


PENDING = _PendingSentinel()

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
    "PENDING",
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
    # Shorthand helpers
    "shorthand",
    "split_shorthand_kwargs",
]
