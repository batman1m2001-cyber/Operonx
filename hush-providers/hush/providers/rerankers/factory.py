from hush.providers.rerankers.base import BaseReranker
from hush.providers.rerankers.config import RerankingConfig, RerankingType
from hush.providers.rerankers.tei import TEIReranker
from hush.providers.rerankers.vllm import VLLMReranker
from hush.providers.rerankers.pinecone import PineconeReranker
from hush.providers.rerankers.huggingface import HFReranker
from hush.providers.rerankers.onnx import ONNXReranker


class RerankingFactory:
    r"""Factory class for reranking model backends

    Raises:
        ValueError: in case the provided model type is unknown.
    """

    @staticmethod
    def create(
        config: RerankingConfig
    ) -> BaseReranker:
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
