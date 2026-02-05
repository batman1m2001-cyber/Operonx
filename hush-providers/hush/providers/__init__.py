"""
Hush Providers - LLM, embedding, and reranking providers for hush workflows.

This package provides AI provider integrations for the Hush workflow engine:
- LLM providers (OpenAI, Azure, Gemini, vLLM)
- Embedding providers (vLLM, TEI, HuggingFace, ONNX)
- Reranking providers (vLLM, TEI, HuggingFace, ONNX, Pinecone)
- Workflow nodes for integrating providers into workflows
"""

# LLM exports
from hush.providers.llms import (
    BaseLLM,
    LLMType,
    LLMConfig,
    OpenAIConfig,
    AzureConfig,
    GeminiConfig,
    LLMFactory,
    LLMGenerator,
    OpenAISDKModel,
    AzureSDKModel,
    # GeminiOpenAISDKModel - lazy loaded, access via hush.providers.llms.GeminiOpenAISDKModel
)

# Embedding exports
from hush.providers.embeddings import (
    BaseEmbedder,
    EmbeddingType,
    EmbeddingConfig,
    EmbeddingFactory,
    VLLMEmbedding,
    TEIEmbedding,
    HFEmbedding,
    ONNXEmbedding,
)

# Reranking exports
from hush.providers.rerankers import (
    BaseReranker,
    RerankingType,
    RerankingConfig,
    RerankingFactory,
    VLLMReranker,
    TEIReranker,
    HFReranker,
    ONNXReranker,
    PineconeReranker,
)

# Node exports
from hush.providers.nodes import (
    LLMNode,
    llm_,
    EmbeddingNode,
    embedding_,
    RerankNode,
    rerank_,
    PromptNode,
    prompt_,
    LLMChainNode,
    llmchain_,
)

# Auth exports
from hush.providers.auth import (
    KeycloakTokenConfig,
    KeycloakTokenProvider,
    AuthFactory,
)

# Registry plugin exports
from hush.providers.registry import (
    LLMPlugin,
    EmbeddingPlugin,
    RerankPlugin,
    AuthPlugin,
)

__version__ = "0.1.0"

__all__ = [
    # LLM
    "BaseLLM",
    "LLMType",
    "LLMConfig",
    "OpenAIConfig",
    "AzureConfig",
    "GeminiConfig",
    "LLMFactory",
    "LLMGenerator",
    "OpenAISDKModel",
    "AzureSDKModel",
    # Embedding
    "BaseEmbedder",
    "EmbeddingType",
    "EmbeddingConfig",
    "EmbeddingFactory",
    "VLLMEmbedding",
    "TEIEmbedding",
    "HFEmbedding",
    "ONNXEmbedding",
    # Reranking
    "BaseReranker",
    "RerankingType",
    "RerankingConfig",
    "RerankingFactory",
    "VLLMReranker",
    "TEIReranker",
    "HFReranker",
    "ONNXReranker",
    "PineconeReranker",
    # Nodes
    "LLMNode",
    "llm_",
    "EmbeddingNode",
    "embedding_",
    "RerankNode",
    "rerank_",
    "PromptNode",
    "prompt_",
    "LLMChainNode",
    "llmchain_",
    # Auth
    "KeycloakTokenConfig",
    "KeycloakTokenProvider",
    "AuthFactory",
    # Registry Plugins
    "LLMPlugin",
    "EmbeddingPlugin",
    "RerankPlugin",
    "AuthPlugin",
]
