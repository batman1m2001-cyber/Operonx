"""Reranking providers for operonx workflows.

Backend classes are lazy-loaded via module-level ``__getattr__`` so this
package can be imported without pulling numpy / onnxruntime /
transformers / torch / aiohttp. Light symbols stay eager.
"""

from operonx.providers.rerankers.base import BaseReranker
from operonx.providers.rerankers.config import RerankingConfig, RerankingType
from operonx.providers.rerankers.factory import create_reranking

_LAZY_BACKENDS = {
    "TEIReranker": "operonx.providers.rerankers.tei",
    "VLLMReranker": "operonx.providers.rerankers.vllm",
    "HFReranker": "operonx.providers.rerankers.huggingface",
    "ONNXReranker": "operonx.providers.rerankers.onnx",
    "PineconeReranker": "operonx.providers.rerankers.pinecone",
}


def __getattr__(name: str):
    if name in _LAZY_BACKENDS:
        import importlib

        module = importlib.import_module(_LAZY_BACKENDS[name])
        cls = getattr(module, name)
        globals()[name] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseReranker",
    "RerankingType",
    "RerankingConfig",
    "create_reranking",
    "VLLMReranker",
    "TEIReranker",
    "HFReranker",
    "ONNXReranker",
    "PineconeReranker",
]
