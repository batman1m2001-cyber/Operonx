"""Config types for the workflow graph.

- ``OpType``     — enum of op categories (func, branch, graph, llm, ...)
- ``EdgeConfig`` — config for edges between ops
- ``EdgeType``   — edge kinds (normal, lookback, condition)
"""

from .edge_config import EdgeConfig, EdgeType
from .op_config import OpType

__all__ = [
    "OpType",
    "EdgeConfig",
    "EdgeType",
]
