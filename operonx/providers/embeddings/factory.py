"""Factory function for creating embedding backends."""

from operonx.providers.embeddings.base import BaseEmbedder
from operonx.providers.embeddings.huggingface import HFEmbedding
from operonx.providers.embeddings.onnx import ONNXEmbedding
from operonx.providers.embeddings.tei import TEIEmbedding
from operonx.providers.embeddings.vllm import VLLMEmbedding

from .config import EmbeddingConfig, EmbeddingType


def create_embedding(config: EmbeddingConfig) -> BaseEmbedder:
    """Create an embedding backend from config.

    Args:
        config: EmbeddingConfig with api_type determining which backend to create.

    Returns:
        BaseEmbedder instance.

    Raises:
        ValueError: If api_type is unsupported.
    """
    if config.api_type == EmbeddingType.TEXT_EMBEDDING_INFERENCE:
        model_class = TEIEmbedding
    elif config.api_type in (EmbeddingType.VLLM, EmbeddingType.OPENAI, EmbeddingType.AZURE):
        model_class = VLLMEmbedding
    elif config.api_type == EmbeddingType.HF:
        model_class = HFEmbedding
    elif config.api_type == EmbeddingType.ONNX:
        model_class = ONNXEmbedding
    else:
        raise ValueError(f"Unsupported Model: {config.api_type}")
    return model_class(config)


async def main():
    embed = create_embedding(config=EmbeddingConfig.default())

    # Test with sample text
    test_text = "What is machine learning and how does it work?"
    vectors = await embed.run(test_text)
    print(f"Generated embedding vectors: {vectors}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
