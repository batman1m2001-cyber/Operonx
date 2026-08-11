"""Resource registry plugins for operonx-providers.

This module provides plugins that extend operonx's ResourceHub
to support LLM, embedding, reranking, vector store, doc store,
and auth resources.

All plugins auto-register on import via module-level register() calls.
"""

from . import (  # noqa: F401
    auth_plugin,
    doc_store_plugin,
    embedding_plugin,
    llm_plugin,
    onnx_plugin,
    rerank_plugin,
    vector_store_plugin,
)

__all__ = [
    "llm_plugin",
    "embedding_plugin",
    "rerank_plugin",
    "onnx_plugin",
    "auth_plugin",
    "vector_store_plugin",
    "doc_store_plugin",
]
