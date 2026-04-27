"""Embedding providers for operonx workflows.

Light symbols (``BaseEmbedder``, ``EmbeddingConfig``, ``EmbeddingType``,
``create_embedding``) are imported eagerly because they have no optional
dependencies. Backend classes are **lazy-loaded** via module-level
``__getattr__`` so importing this package does not require numpy /
onnxruntime / transformers / torch unless the user actually accesses
the corresponding backend.
"""

from operonx.providers.embeddings.base import BaseEmbedder
from operonx.providers.embeddings.config import EmbeddingConfig, EmbeddingType
from operonx.providers.embeddings.factory import create_embedding

# Map of public attribute name -> dotted module path. Each entry is
# imported on first access via __getattr__ below.
_LAZY_BACKENDS = {
    "TEIEmbedding": "operonx.providers.embeddings.tei",
    "VLLMEmbedding": "operonx.providers.embeddings.vllm",
    "HFEmbedding": "operonx.providers.embeddings.huggingface",
    "ONNXEmbedding": "operonx.providers.embeddings.onnx",
}


def __getattr__(name: str):
    """Lazy attribute loading for backend classes.

    See PEP 562. Falls back to ``AttributeError`` for unknown names so
    typos surface normally.
    """
    if name in _LAZY_BACKENDS:
        import importlib

        module = importlib.import_module(_LAZY_BACKENDS[name])
        cls = getattr(module, name)
        globals()[name] = cls  # cache for subsequent access
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseEmbedder",
    "EmbeddingType",
    "EmbeddingConfig",
    "create_embedding",
    "VLLMEmbedding",
    "TEIEmbedding",
    "HFEmbedding",
    "ONNXEmbedding",
]
