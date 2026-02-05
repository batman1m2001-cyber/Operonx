"""
Hush Providers - LLM, embedding, and reranking providers for hush workflows.

This package provides AI provider integrations for the Hush workflow engine:
- LLM providers (OpenAI, Azure, Gemini, vLLM)
- Embedding providers (vLLM, TEI, HuggingFace, ONNX)
- Reranking providers (vLLM, TEI, HuggingFace, ONNX, Pinecone)
- Workflow nodes for integrating providers into workflows
"""

# LLM exports
# Auth exports
from hush.providers.auth import (
    AuthFactory,
    KeycloakTokenConfig,
    KeycloakTokenProvider,
)

# Embedding exports
from hush.providers.embeddings import (
    BaseEmbedder,
    EmbeddingConfig,
    EmbeddingFactory,
    EmbeddingType,
    HFEmbedding,
    ONNXEmbedding,
    TEIEmbedding,
    VLLMEmbedding,
)
from hush.providers.llms import (
    AzureConfig,
    AzureSDKModel,
    # GeminiOpenAISDKModel - lazy loaded, access via hush.providers.llms.GeminiOpenAISDKModel
    BaseLLM,
    GeminiConfig,
    LLMConfig,
    LLMFactory,
    LLMGenerator,
    LLMType,
    OpenAIConfig,
    OpenAISDKModel,
)

# Node exports
from hush.providers.nodes import (
    EmbeddingNode,
    LLMChainNode,
    LLMNode,
    PromptNode,
    RerankNode,
    embedding_,
    llm_,
    llmchain_,
    prompt_,
    rerank_,
)

# Registry plugin exports
from hush.providers.registry import (
    AuthPlugin,
    EmbeddingPlugin,
    LLMPlugin,
    RerankPlugin,
)

# Reranking exports
from hush.providers.rerankers import (
    BaseReranker,
    HFReranker,
    ONNXReranker,
    PineconeReranker,
    RerankingConfig,
    RerankingFactory,
    RerankingType,
    TEIReranker,
    VLLMReranker,
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
