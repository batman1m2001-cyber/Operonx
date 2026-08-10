"""Transform ops for data processing.

- FuncOp: Execute a Python function as an op.

Note (1.0.0): ``ParserOp`` was removed. Its parsing/validation logic lives
as pure functions in ``operonx.providers.parsing`` and is used inline by
``LLMOp(fields=..., parser=..., validators=...)``.
"""

from .func_op import FuncOp, op

__all__ = [
    "FuncOp",
    "op",
]
