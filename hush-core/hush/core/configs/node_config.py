"""Các kiểu và class config node cho hush-core."""

from typing import Literal


NodeType = Literal[
    # Node dữ liệu
    "data",
    # Node AI/ML
    "llm", "embedding", "rerank",
    # Node điều khiển luồng
    "branch", "for", "while", "stream",
    # Node xử lý
    "code", "lambda", "parser", "prompt", "doc-processor",
    # Node database/storage
    "milvus", "mongo", "s3",
    # Node đặc biệt
    "graph",
    "default",
    "dummy",
    "tool-executor",
    "mcp"
]
"""Các loại node được hỗ trợ trong workflow graph."""