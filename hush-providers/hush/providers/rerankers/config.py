from enum import Enum
from typing import ClassVar, Optional
from hush.core.utils import YamlModel


class RerankingType(Enum):
    COHERE = "cohere"
    TEXT_EMBEDDING_INFERENCE = "tei"
    VLLM = "vllm"
    PINECONE = "pinecone"
    HF = "hf"  # HuggingFace Transformers
    ONNX = "onnx"  # ONNX Runtime


class RerankingConfig(YamlModel):
    """
    Configuration for text reranking services.

    This class defines the configuration parameters for text reranking APIs,
    which are used to improve the relevance of search results or document rankings.

    Attributes:
        api_key (str): The API key for authenticating with the reranking service.
            Default is "YOUR_RERANK_API_KEY". This must be set to a valid API key.

        base_url (Optional[str]): The base URL for the API endpoint.
            Default is None. If not provided, the default URL of the service will be used.

        model (Optional[str]): The specific model to use for reranking.
            Default is None. If not provided, the default model of the service will be used.

    The class includes a field validator for the api_key to ensure it's properly set.

    Examples:
        Basic configuration:
        ```yaml
        api_key: "your_actual_api_key_here"
        ```

        Advanced configuration:
        ```yaml
        api_key: "your_actual_api_key_here"
        base_url: "https://api.rerank-service.com/v1"
        model: "rerank-v2"
        ```

    Note:
        The api_key must be set to a valid key. If it's not set or set to the default value,
        an error will be raised with instructions on how to properly set it in the configuration file.
    """
    _category: ClassVar[str] = "reranking"

    api_type: RerankingType = RerankingType.VLLM
    api_key: Optional[str] = None
    api_version: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None

    @classmethod
    def default(cls) -> 'RerankingConfig':
        """Load default config"""
        return RerankingConfig(
            api_type=RerankingType.PINECONE,
            model="bge-reranker-v2-m3",
            base_url="https://api.pinecone.io/rerank",
            api_key="your-api-key"
        )
