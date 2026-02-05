"""Resource registry plugins for hush-providers.

This module provides plugins that extend hush-core's ResourceHub
to support LLM, embedding, reranking, and auth resources.
"""

from .auth_plugin import AuthPlugin
from .embedding_plugin import EmbeddingPlugin
from .llm_plugin import LLMPlugin
from .rerank_plugin import RerankPlugin

__all__ = [
    "LLMPlugin",
    "EmbeddingPlugin",
    "RerankPlugin",
    "AuthPlugin",
]
