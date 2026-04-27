"""Factory function for creating reranking backends.

Each backend is **lazy-imported** inside its dispatch branch so that
``import operonx.providers.rerankers`` doesn't pull in heavy optional
dependencies (numpy, onnxruntime, transformers, torch). A user who only
installed ``operonx[anthropic]`` can still import the rerankers package
without crashing — they only hit the missing-dep error if they actually
try to instantiate the corresponding backend.
"""

from operonx.providers.rerankers.base import BaseReranker
from operonx.providers.rerankers.config import RerankingConfig, RerankingType


def create_reranking(config: RerankingConfig) -> BaseReranker:
    """Create a reranking backend from config.

    Args:
        config: RerankingConfig with api_type determining which backend to create.

    Returns:
        BaseReranker instance.

    Raises:
        ValueError: If api_type is unsupported.
        ImportError: With a helpful pointer to the right ``operonx[<extra>]``
            install when an optional dependency is missing.
    """
    if config.api_type == RerankingType.TEXT_EMBEDDING_INFERENCE:
        try:
            from operonx.providers.rerankers.tei import TEIReranker
        except ImportError as e:
            raise ImportError(_missing_extra_message("TEIReranker", "providers", e)) from e
        return TEIReranker(config)
    if config.api_type == RerankingType.VLLM:
        try:
            from operonx.providers.rerankers.vllm import VLLMReranker
        except ImportError as e:
            raise ImportError(_missing_extra_message("VLLMReranker", "providers", e)) from e
        return VLLMReranker(config)
    if config.api_type == RerankingType.PINECONE:
        try:
            from operonx.providers.rerankers.pinecone import PineconeReranker
        except ImportError as e:
            raise ImportError(_missing_extra_message("PineconeReranker", "providers", e)) from e
        return PineconeReranker(config)
    if config.api_type == RerankingType.HF:
        try:
            from operonx.providers.rerankers.huggingface import HFReranker
        except ImportError as e:
            raise ImportError(_missing_extra_message("HFReranker", "huggingface", e)) from e
        return HFReranker(config)
    if config.api_type == RerankingType.ONNX:
        try:
            from operonx.providers.rerankers.onnx import ONNXReranker
        except ImportError as e:
            raise ImportError(_missing_extra_message("ONNXReranker", "onnx", e)) from e
        return ONNXReranker(config)
    raise ValueError(f"Unsupported Model: {config.api_type}")


def _missing_extra_message(backend: str, extra: str, exc: ImportError) -> str:
    return (
        f"{backend} requires additional packages.\n"
        f"  Install with: pip install operonx[{extra}]\n"
        f"  Original error: {exc}"
    )
