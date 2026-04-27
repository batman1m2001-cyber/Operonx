"""Factory function for creating embedding backends.

Each backend is **lazy-imported** inside its dispatch branch so that
``import operonx.providers.embeddings`` doesn't pull in heavy optional
dependencies (numpy, onnxruntime, transformers, torch). A user who only
installed ``operonx[anthropic]`` can still import the embeddings package
without crashing — they only hit the missing-dep error if they actually
try to instantiate the corresponding backend.
"""

from operonx.providers.embeddings.base import BaseEmbedder
from operonx.providers.embeddings.config import EmbeddingConfig, EmbeddingType


def create_embedding(config: EmbeddingConfig) -> BaseEmbedder:
    """Create an embedding backend from config.

    Args:
        config: EmbeddingConfig with api_type determining which backend to create.

    Returns:
        BaseEmbedder instance.

    Raises:
        ValueError: If api_type is unsupported.
        ImportError: With a helpful pointer to the right ``operonx[<extra>]``
            install when an optional dependency is missing.
    """
    if config.api_type == EmbeddingType.TEXT_EMBEDDING_INFERENCE:
        try:
            from operonx.providers.embeddings.tei import TEIEmbedding
        except ImportError as e:
            raise ImportError(_missing_extra_message("TEIEmbedding", "providers", e)) from e
        return TEIEmbedding(config)
    if config.api_type in (EmbeddingType.VLLM, EmbeddingType.OPENAI, EmbeddingType.AZURE):
        try:
            from operonx.providers.embeddings.vllm import VLLMEmbedding
        except ImportError as e:
            raise ImportError(_missing_extra_message("VLLMEmbedding", "providers", e)) from e
        return VLLMEmbedding(config)
    if config.api_type == EmbeddingType.HF:
        try:
            from operonx.providers.embeddings.huggingface import HFEmbedding
        except ImportError as e:
            raise ImportError(_missing_extra_message("HFEmbedding", "huggingface", e)) from e
        return HFEmbedding(config)
    if config.api_type == EmbeddingType.ONNX:
        try:
            from operonx.providers.embeddings.onnx import ONNXEmbedding
        except ImportError as e:
            raise ImportError(_missing_extra_message("ONNXEmbedding", "onnx", e)) from e
        return ONNXEmbedding(config)
    raise ValueError(f"Unsupported Model: {config.api_type}")


def _missing_extra_message(backend: str, extra: str, exc: ImportError) -> str:
    return (
        f"{backend} requires additional packages.\n"
        f"  Install with: pip install operonx[{extra}]\n"
        f"  Original error: {exc}"
    )
