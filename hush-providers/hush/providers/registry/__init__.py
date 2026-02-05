"""Resource registry plugins for hush-providers.

This module provides plugins that extend hush-core's ResourceHub
to support LLM, embedding, reranking, and auth resources.
"""

from .llm_plugin import LLMPlugin
from .embedding_plugin import EmbeddingPlugin
from .rerank_plugin import RerankPlugin
from .auth_plugin import AuthPlugin

__all__ = [
    "LLMPlugin",
    "EmbeddingPlugin",
    "RerankPlugin",
    "AuthPlugin",
]
