"""
Hush Providers - LLM, embedding, and reranking providers for hush workflows.

This package provides AI provider integrations for the Hush workflow engine:
- LLM providers (OpenAI, Azure, Gemini, vLLM)
- Embedding providers (vLLM, TEI, HuggingFace, ONNX)
- Reranking providers (vLLM, TEI, HuggingFace, ONNX, Pinecone)
- Workflow nodes for integrating providers into workflows
"""

# Auth exports
# Auto-register plugins on import
import hush.providers.registry  # noqa: F401
from hush.providers.auth import (
    KeycloakTokenConfig,
    KeycloakTokenProvider,
    create_auth,
)

# Embedding exports
from hush.providers.embeddings import (
    BaseEmbedder,
    EmbeddingConfig,
    EmbeddingType,
    HFEmbedding,
    ONNXEmbedding,
    TEIEmbedding,
    VLLMEmbedding,
    create_embedding,
)

# LLM exports
from hush.providers.llms import (
    AzureConfig,
    AzureSDKModel,
    # GeminiOpenAISDKModel - lazy loaded, access via hush.providers.llms.GeminiOpenAISDKModel
    BaseLLM,
    GeminiConfig,
    LLMConfig,
    LLMGenerator,
    LLMType,
    OpenAIConfig,
    OpenAISDKModel,
    create_llm,
)

# Node exports
from hush.providers.ops import (
    EmbeddingOp,
    LLMOp,
    OnnxOp,
    PromptOp,
    RerankOp,
    ask,
    chat,
)

# Reranking exports
from hush.providers.rerankers import (
    BaseReranker,
    HFReranker,
    ONNXReranker,
    PineconeReranker,
    RerankingConfig,
    RerankingType,
    TEIReranker,
    VLLMReranker,
    create_reranking,
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
    "create_llm",
    "LLMGenerator",
    "OpenAISDKModel",
    "AzureSDKModel",
    # Embedding
    "BaseEmbedder",
    "EmbeddingType",
    "EmbeddingConfig",
    "create_embedding",
    "VLLMEmbedding",
    "TEIEmbedding",
    "HFEmbedding",
    "ONNXEmbedding",
    # Reranking
    "BaseReranker",
    "RerankingType",
    "RerankingConfig",
    "create_reranking",
    "VLLMReranker",
    "TEIReranker",
    "HFReranker",
    "ONNXReranker",
    "PineconeReranker",
    # Nodes
    "LLMOp",
    "EmbeddingOp",
    "OnnxOp",
    "RerankOp",
    "PromptOp",
    "chat",
    "ask",
    # Auth
    "KeycloakTokenConfig",
    "KeycloakTokenProvider",
    "create_auth",
]
