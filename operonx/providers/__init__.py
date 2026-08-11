"""Operonx providers — LLM, embedding, reranker, and auth integrations.

Includes:
- LLM providers: OpenAI, Azure, Gemini, Anthropic, vLLM (via OpenAI SDK)
- Embedding providers: vLLM, TEI, HuggingFace, ONNX
- Reranking providers: vLLM, TEI, HuggingFace, ONNX, Pinecone
- Auth: Keycloak token provider with background refresh
- Workflow ops: LLMOp, EmbeddingOp, RerankOp, OnnxOp, TritonOp, ask

Plugin registration to the core ResourceHub happens automatically on import.

**All public names are lazy-loaded** via module-level ``__getattr__``
(PEP 562). ``import operonx.providers`` only runs the plugin
registration step — config / factory / op / backend symbols are not
materialised until they are actually accessed. A tier-1 install
(``pip install operonx``) can ``import operonx.providers`` without
crashing on missing ``openai`` / ``anthropic`` / ``httpx`` /
``numpy``; missing-dep ``ImportError`` surfaces only when the user
actually touches a backend or factory that needs it.
"""

# Auto-register plugins to operonx.core.registry on import. The plugin
# modules import their respective config classes (light) and factory
# functions (light — heavy backends are deferred inside each factory).
import operonx.providers.registry  # noqa: F401

# ── Lazy-loaded public surface ──────────────────────────────────────────
# Each entry maps a public name to the dotted submodule that owns it.
# On first access via __getattr__, the module is imported, the attr is
# fetched, and the result is cached in module globals.
#
# Light symbols (config classes, factories, base classes, ops) sit
# alongside heavy backend classes here so a tier-1 install can do
# ``import operonx.providers`` without pulling in optional deps. The
# real cost is only paid on first attribute access.
_LAZY_BACKENDS = {
    # Auth
    "KeycloakTokenConfig": "operonx.providers.auth",
    "KeycloakTokenProvider": "operonx.providers.auth",
    "create_auth": "operonx.providers.auth",
    # Embeddings (light surface)
    "BaseEmbedder": "operonx.providers.embeddings",
    "EmbeddingConfig": "operonx.providers.embeddings",
    "EmbeddingType": "operonx.providers.embeddings",
    "create_embedding": "operonx.providers.embeddings",
    # Embedding backends (heavy — defining modules pull numpy/onnx/torch)
    "VLLMEmbedding": "operonx.providers.embeddings",
    "TEIEmbedding": "operonx.providers.embeddings",
    "HFEmbedding": "operonx.providers.embeddings",
    "ONNXEmbedding": "operonx.providers.embeddings",
    # LLMs (light surface)
    "BaseLLM": "operonx.providers.llms",
    "LLMConfig": "operonx.providers.llms",
    "LLMType": "operonx.providers.llms",
    "OpenAIConfig": "operonx.providers.llms",
    "AzureConfig": "operonx.providers.llms",
    "GeminiConfig": "operonx.providers.llms",
    "AnthropicConfig": "operonx.providers.llms",
    "create_llm": "operonx.providers.llms",
    # LLM backends (heavy)
    "OpenAISDKModel": "operonx.providers.llms",
    "AzureSDKModel": "operonx.providers.llms",
    "AnthropicModel": "operonx.providers.llms",
    "GeminiOpenAISDKModel": "operonx.providers.llms",
    # Rerankers (light surface)
    "BaseReranker": "operonx.providers.rerankers",
    "RerankingConfig": "operonx.providers.rerankers",
    "RerankingType": "operonx.providers.rerankers",
    "create_reranking": "operonx.providers.rerankers",
    # Reranker backends (heavy)
    "VLLMReranker": "operonx.providers.rerankers",
    "TEIReranker": "operonx.providers.rerankers",
    "HFReranker": "operonx.providers.rerankers",
    "ONNXReranker": "operonx.providers.rerankers",
    "PineconeReranker": "operonx.providers.rerankers",
    # Vector stores
    "BaseVectorStore": "operonx.providers.vector_stores",
    "VectorStoreType": "operonx.providers.vector_stores",
    "VectorStoreMetric": "operonx.providers.vector_stores",
    "VectorStoreConfig": "operonx.providers.vector_stores",
    "create_vector_store": "operonx.providers.vector_stores",
    "FaissVectorStore": "operonx.providers.vector_stores",
    "PgVectorStore": "operonx.providers.vector_stores",
    "QdrantVectorStore": "operonx.providers.vector_stores",
    # Doc stores
    "BaseDocStore": "operonx.providers.doc_stores",
    "DocStoreType": "operonx.providers.doc_stores",
    "DocStoreConfig": "operonx.providers.doc_stores",
    "create_doc_store": "operonx.providers.doc_stores",
    "reorder_by_ids": "operonx.providers.doc_stores",
    "partition_by_ids": "operonx.providers.doc_stores",
    "PostgresDocStore": "operonx.providers.doc_stores",
    "MemoryDocStore": "operonx.providers.doc_stores",
    # Workflow ops
    "LLMOp": "operonx.providers.ops",
    "EmbeddingOp": "operonx.providers.ops",
    "RerankOp": "operonx.providers.ops",
    "OnnxOp": "operonx.providers.ops",
    "VectorSearchOp": "operonx.providers.ops",
    "DocFetchOp": "operonx.providers.ops",
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
    "VectorSearchOp",
    "DocFetchOp",
    "TritonOp",
    # Vector stores
    "BaseVectorStore",
    "VectorStoreType",
    "VectorStoreMetric",
    "VectorStoreConfig",
    "create_vector_store",
    "FaissVectorStore",
    "PgVectorStore",
    "QdrantVectorStore",
    # Doc stores
    "BaseDocStore",
    "DocStoreType",
    "DocStoreConfig",
    "create_doc_store",
    "reorder_by_ids",
    "partition_by_ids",
    "PostgresDocStore",
    # LLM
    "BaseLLM",
    "LLMType",
    "LLMConfig",
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
