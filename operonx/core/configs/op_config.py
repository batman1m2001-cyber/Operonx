"""Các kiểu và class config node cho operonx."""

from typing import Literal

OpType = Literal[
    # Node dữ liệu
    "data",
    # Node AI/ML
    "llm",
    "embedding",
    "rerank",
    "vector-search",
    "doc-fetch",
    # Node điều khiển luồng
    "branch",
    "interrupt",
    "emit",
    # Node xử lý
    "code",
    "lambda",
    "prompt",
    "doc-processor",
    # Node đặc biệt
    "graph",
    "default",
    "dummy",
    "tool-executor",
    "mcp",
]
"""Các loại node được hỗ trợ trong workflow graph.

Removed in 1.2.0:
    ``for`` / ``while`` / ``stream`` — superseded in 1.0.0 by back-edge
        loops, generator ops and ``Ref.parallel()``.
    ``parser`` — ``ParserOp`` went away in 1.0.0; parsing lives inside
        ``LLMOp(fields=...)``.
    ``milvus`` / ``mongo`` / ``s3`` — named backends, not semantics, and
        never had ops behind them. Storage is reached through
        ``vector-search`` and ``doc-fetch``.
    ``onnx`` / ``triton`` — assigned by the ops deleted in 1.2.0 (they
        were never in this Literal, which is the drift this cleanup ends).

Added in 1.2.0:
    ``interrupt`` / ``emit`` — set by ``InterruptOp`` / ``EmitOp`` since
        1.0.0 but missing here, so the Literal disagreed with the code.
"""
