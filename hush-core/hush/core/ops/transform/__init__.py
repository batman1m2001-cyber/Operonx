"""Transform ops for data processing.

- FuncOp: Execute a Python function as an op.
- ParserOp: Parse and extract structured data from text.
"""

from .func_op import FuncOp, op
from .parser_op import ParserOp, ParserType

__all__ = [
    "FuncOp",
    "op",
    "ParserOp",
    "ParserType",
]
