"""Resource registry plugins for hush-providers.

This module provides plugins that extend hush-icore's ResourceHub
to support LLM, embedding, reranking, and auth resources.

All plugins auto-register on import via module-level register() calls.
"""

from . import auth_plugin, embedding_plugin, llm_plugin, rerank_plugin  # noqa: F401

__all__ = [
    "llm_plugin",
    "embedding_plugin",
    "rerank_plugin",
    "auth_plugin",
]
