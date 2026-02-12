"""Package chứa các config type cho workflow graph.

Module này export các type và class config chính:
    - OpType: Các loại node được hỗ trợ trong workflow
    - EdgeConfig: Config cho edge kết nối giữa các node
    - EdgeType: Các loại edge (normal, lookback, condition)
"""

from .edge_config import EdgeConfig, EdgeType
from .op_config import OpType

__all__ = [
    "OpType",
    "EdgeConfig",
    "EdgeType",
]
