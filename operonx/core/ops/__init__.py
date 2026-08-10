"""Core op types and markers for the Operon workflow engine.

- ``BaseOp``        — base class for all workflow ops
- ``DummyOp``       — placeholder for START/END markers
- ``GraphOp``       — container managing a sub-graph with parallel execution
- ``BranchOp``      — conditional routing with precompiled conditions
- ``FuncOp``        — wraps a Python function (supports generators for streaming)

Text→structured-output parsing is done inline by ``LLMOp(fields=..., parser=...,
validators=...)`` — the standalone ``ParserOp`` was removed in 1.0.0. Its
pure functions live in ``operonx.providers.parsing`` for callers that need
text parsing without an LLM call.

Markers: ``START``, ``END``, ``PARENT``, ``PENDING``.
Decorators: ``op``, ``graph``, ``if_``.
"""

from operonx.core.configs.op_config import OpType

from ._events import EOF, Frame, Interrupt
from .base import (
    END,
    PARENT,
    SCRATCH,
    START,
    BaseOp,
    DummyOp,
    ScratchAccessor,
    SoftEdge,
    shorthand,
    split_shorthand_kwargs,
)
from .flow.branch_op import Branch, BranchOp, if_
from .flow.emit_op import EmitOp
from .flow.interrupt_op import InterruptOp
from .graph.graph_op import GraphOp, graph
from .transform.func_op import FuncOp, op


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
    "SCRATCH",
    "ScratchAccessor",
    # Scheduler events
    "Frame",
    "EOF",
    "Interrupt",
    # Graph
    "GraphOp",
    "graph",
    # Flow control
    "BranchOp",
    "Branch",
    "if_",
    "EmitOp",
    "InterruptOp",
    # Transform
    "FuncOp",
    "op",
    # Shorthand helpers
    "shorthand",
    "split_shorthand_kwargs",
]
