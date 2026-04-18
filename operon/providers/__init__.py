"""Operon providers — LLM, embedding, reranker, and auth integrations.

Includes:
- LLM providers: OpenAI, Azure, Gemini, Anthropic, vLLM (via OpenAI SDK)
- Embedding providers: vLLM, TEI, HuggingFace, ONNX
- Reranking providers: vLLM, TEI, HuggingFace, ONNX, Pinecone
- Auth: Keycloak token provider with background refresh
- Workflow ops: LLMOp, EmbeddingOp, RerankOp, OnnxOp, TritonOp, PromptOp, chat, ask

Plugin registration to the core ResourceHub happens automatically on import.
"""

# Auto-register plugins to operon.core.registry on import
import operon.providers.registry  # noqa: F401

from operon.providers.auth import (
    KeycloakTokenConfig,
    KeycloakTokenProvider,
    create_auth,
)
from operon.providers.embeddings import (
    BaseEmbedder,
    EmbeddingConfig,
    EmbeddingType,
    HFEmbedding,
    ONNXEmbedding,
    TEIEmbedding,
    VLLMEmbedding,
    create_embedding,
)
from operon.providers.llms import (
    AnthropicConfig,
    AnthropicModel,
    AzureConfig,
    AzureSDKModel,
    BaseLLM,
    GeminiConfig,
    LLMConfig,
    LLMGenerator,
    LLMType,
    OpenAIConfig,
    OpenAISDKModel,
    create_llm,
)
from operon.providers.ops import (
    EmbeddingOp,
    LLMOp,
    OnnxOp,
    PromptOp,
    RerankOp,
    TritonOp,
    ask,
    chat,
)
from operon.providers.rerankers import (
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

# Note: GeminiOpenAISDKModel is lazy-loaded via operon.providers.llms.__getattr__
# to avoid requiring google-cloud-aiplatform when unused.

__all__ = [
    # Ops
    "LLMOp",
    "EmbeddingOp",
    "RerankOp",
    "OnnxOp",
    "TritonOp",
    "PromptOp",
    "chat",
    "ask",
    # LLM
    "BaseLLM",
    "LLMType",
    "LLMConfig",
    "LLMGenerator",
    "OpenAIConfig",
    "OpenAISDKModel",
    "AzureConfig",
    "AzureSDKModel",
    "GeminiConfig",
    "AnthropicConfig",
    "AnthropicModel",
    "create_llm",
    # Embedding
    "BaseEmbedder",
    "EmbeddingType",
    "EmbeddingConfig",
    "VLLMEmbedding",
    "TEIEmbedding",
    "HFEmbedding",
    "ONNXEmbedding",
    "create_embedding",
    # Reranking
    "BaseReranker",
    "RerankingType",
    "RerankingConfig",
    "VLLMReranker",
    "TEIReranker",
    "HFReranker",
    "ONNXReranker",
    "PineconeReranker",
    "create_reranking",
    # Auth
    "KeycloakTokenConfig",
    "KeycloakTokenProvider",
    "create_auth",
]
