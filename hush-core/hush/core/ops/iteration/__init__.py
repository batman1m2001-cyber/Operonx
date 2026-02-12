"""Iteration ops for loops and streaming.

- ForOp: Sequential iteration over a collection.
- MapOp: Parallel iteration with concurrency control.
- WhileOp: Conditional loop until a stop condition is met.
- AIterOp: Async-iterable / streaming data processing.
- Each: Marker that designates an iteration source.
- BaseIterationOp: Shared base class for all iteration ops.
"""

from hush.core.ops.iteration.aiter_op import AIterOp
from hush.core.ops.iteration.base import BaseIterationOp, Each
from hush.core.ops.iteration.for_op import ForOp
from hush.core.ops.iteration.map_op import MapOp
from hush.core.ops.iteration.while_op import WhileOp

__all__ = [
    "Each",
    "BaseIterationOp",
    "ForOp",
    "MapOp",
    "WhileOp",
    "AIterOp",
]
