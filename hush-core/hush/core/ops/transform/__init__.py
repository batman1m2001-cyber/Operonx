"""Các node transform cho workflow.

Bao gồm:
- FuncOp: Thực thi function Python
- ParserOp: Parse và trích xuất dữ liệu từ text (JSON, XML, YAML, regex, key-value)
"""

from .func_op import FuncOp, op
from .parser_op import ParserOp, ParserType

__all__ = [
    "FuncOp",
    "op",
    "ParserOp",
    "ParserType",
]
