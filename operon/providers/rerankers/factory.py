"""Factory function for creating reranking backends."""

from operon.providers.rerankers.base import BaseReranker
from operon.providers.rerankers.config import RerankingConfig, RerankingType
from operon.providers.rerankers.huggingface import HFReranker
from operon.providers.rerankers.onnx import ONNXReranker
from operon.providers.rerankers.pinecone import PineconeReranker
from operon.providers.rerankers.tei import TEIReranker
from operon.providers.rerankers.vllm import VLLMReranker


def create_reranking(config: RerankingConfig) -> BaseReranker:
    """Create a reranking backend from config.

    Args:
        config: RerankingConfig with api_type determining which backend to create.

    Returns:
        BaseReranker instance.

    Raises:
        ValueError: If api_type is unsupported.
    """
    if config.api_type == RerankingType.TEXT_EMBEDDING_INFERENCE:
        model_class = TEIReranker
    elif config.api_type == RerankingType.VLLM:
        model_class = VLLMReranker
    elif config.api_type == RerankingType.PINECONE:
        model_class = PineconeReranker
    elif config.api_type == RerankingType.HF:
        model_class = HFReranker
    elif config.api_type == RerankingType.ONNX:
        model_class = ONNXReranker
    else:
        raise ValueError(f"Unsupported Model: {config.api_type}")
    return model_class(config)
