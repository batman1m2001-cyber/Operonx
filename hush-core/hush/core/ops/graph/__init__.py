"""Graph node cho workflow.

Bao gồm:
- GraphOp: Container quản lý graph các node với thực thi song song
"""

from .graph_op import GraphOp, OpFlowType

__all__ = [
    "GraphOp",
    "OpFlowType",
]
