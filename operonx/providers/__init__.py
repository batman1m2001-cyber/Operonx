"""Operonx providers — LLM, embedding, reranker, and auth integrations.

Includes:
- LLM providers: OpenAI, Azure, Gemini, Anthropic, vLLM (via OpenAI SDK)
- Embedding providers: vLLM, TEI, HuggingFace, ONNX
- Reranking providers: vLLM, TEI, HuggingFace, ONNX, Pinecone
- Auth: Keycloak token provider with background refresh
- Workflow ops: LLMOp, EmbeddingOp, RerankOp, OnnxOp, TritonOp, PromptOp, chat, ask

Plugin registration to the core ResourceHub happens automatically on import.

Backend classes (``OpenAISDKModel``, ``AnthropicModel``, ``HFEmbedding``,
etc.) are **lazy-loaded** through module-level ``__getattr__`` so that
``import operonx.providers`` works with only the dependencies of the
installed extra. A user with just ``operonx[anthropic]`` can do
``from operonx.providers.llms.anthropic import AnthropicModel`` without
the import chain crashing on a missing ``numpy`` or ``onnxruntime``.
"""

# Auto-register plugins to operonx.core.registry on import. The plugin
# modules only import the *factories* (which are themselves lazy
# dispatchers), not the heavy backend modules.
import operonx.providers.registry  # noqa: F401
from operonx.providers.auth import (
    KeycloakTokenConfig,
    KeycloakTokenProvider,
    create_auth,
)
from operonx.providers.embeddings import (
    BaseEmbedder,
    EmbeddingConfig,
    EmbeddingType,
    create_embedding,
)
from operonx.providers.llms import (
    AnthropicConfig,
    AzureConfig,
    BaseLLM,
    GeminiConfig,
    LLMConfig,
    LLMGenerator,
    LLMType,
    OpenAIConfig,
    create_llm,
)
from operonx.providers.ops import (
    EmbeddingOp,
    LLMOp,
    OnnxOp,
    PromptOp,
    RerankOp,
    ask,
    chat,
)
# TritonOp is intentionally NOT in the eager import list — its module
# pulls numpy. It's accessible lazily via __getattr__ below.
from operonx.providers.rerankers import (
    BaseReranker,
    RerankingConfig,
    RerankingType,
    create_reranking,
)

# ── Lazy-loaded backend classes ─────────────────────────────────────────
# Each entry maps a public name to the dotted submodule that owns it.
# On first access via __getattr__, the module is imported, the class is
# fetched, and the result is cached in module globals.
#
# These classes are kept out of the eager imports above because their
# defining modules pull optional deps (numpy, onnxruntime, transformers,
# torch, google-cloud-aiplatform). Eager-importing here would force every
# user to install every optional extra.
_LAZY_BACKENDS = {
    # LLMs
    "OpenAISDKModel": "operonx.providers.llms",
    "AzureSDKModel": "operonx.providers.llms",
    "AnthropicModel": "operonx.providers.llms",
    "GeminiOpenAISDKModel": "operonx.providers.llms",
    # Embeddings
    "VLLMEmbedding": "operonx.providers.embeddings",
    "TEIEmbedding": "operonx.providers.embeddings",
    "HFEmbedding": "operonx.providers.embeddings",
    "ONNXEmbedding": "operonx.providers.embeddings",
    # Rerankers
    "VLLMReranker": "operonx.providers.rerankers",
    "TEIReranker": "operonx.providers.rerankers",
    "HFReranker": "operonx.providers.rerankers",
    "ONNXReranker": "operonx.providers.rerankers",
    "PineconeReranker": "operonx.providers.rerankers",
    # Ops with heavy module-level deps (numpy, etc.)
    "TritonOp": "operonx.providers.ops",
}


def __getattr__(name: str):
    """Lazy attribute loading — see PEP 562."""
    if name in _LAZY_BACKENDS:
        import importlib

        module = importlib.import_module(_LAZY_BACKENDS[name])
        attr = getattr(module, name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    "GeminiOpenAISDKModel",
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
