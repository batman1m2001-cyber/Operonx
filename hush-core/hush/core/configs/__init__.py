"""Package chứa các config type cho workflow graph.

Module này export các type và class config chính:
    - NodeType: Các loại node được hỗ trợ trong workflow
    - EdgeConfig: Config cho edge kết nối giữa các node
    - EdgeType: Các loại edge (normal, lookback, condition)
"""

from .node_config import NodeType
from .edge_config import EdgeConfig, EdgeType

__all__ = [
    "NodeType",
    "EdgeConfig",
    "EdgeType",
]
