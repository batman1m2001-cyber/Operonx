"""Resource registry plugins for operonx-providers.

This module provides plugins that extend operonx's ResourceHub
to support LLM, embedding, reranking, and auth resources.

All plugins auto-register on import via module-level register() calls.
"""

from . import auth_plugin, embedding_plugin, llm_plugin, onnx_plugin, rerank_plugin  # noqa: F401

__all__ = [
    "llm_plugin",
    "embedding_plugin",
    "rerank_plugin",
    "onnx_plugin",
    "auth_plugin",
]
