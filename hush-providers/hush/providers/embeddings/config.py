from enum import Enum
from typing import ClassVar, Optional
from hush.core.utils import YamlModel


class EmbeddingType(Enum):
    OPENAI = "openai"
    AZURE = "azure"
    GEMINI = "gemini"
    TEXT_EMBEDDING_INFERENCE = "tei"
    VLLM = "vllm"
    HF = "hf"  # HuggingFace Transformers
    ONNX = "onnx"  # ONNX Runtime


class EmbeddingConfig(YamlModel):
    """Configuration for text embedding services.

    This class defines the configuration parameters for various text embedding APIs,
    supporting different providers such as OpenAI, Azure, Gemini, and TEI.

    Attributes:
        api_type (Optional[EmbeddingType]): The type of embedding API to use.
            Options are defined in the EmbeddingType enum.
            Default is None.
        api_key (Optional[str]): The API key for authenticating with the embedding service.
            Required for OpenAI, Azure, and Gemini. Default is None.
        base_url (Optional[str]): The base URL for the API endpoint.
            Required for Azure and TEI. Default is None.
        api_version (Optional[str]): The version of the API to use, if applicable.
            Required for Azure. Default is None.
        model (Optional[str]): The specific model to use for embedding.
            Required for TEI. Default is None.
        embed_batch_size (Optional[int]): The batch size for embedding requests.
            Default is None.
        dimensions (Optional[int]): The dimensionality of the generated embeddings.
            Required for OpenAI, Azure, and TEI. Default is None.
    """
    _category: ClassVar[str] = "embedding"

    api_type: EmbeddingType = EmbeddingType.VLLM
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    embed_batch_size: Optional[int] = None
    model: Optional[str] = None
    dimensions: Optional[int] = None

    @classmethod
    def default(cls) -> 'EmbeddingConfig':
        """Load default config with hardcoded values"""
        return cls(
            api_type=EmbeddingType.VLLM,
            api_key="your-api-key",
            base_url="http://localhost:8000/v1/embeddings",
            embed_batch_size=None,
            model="BAAI/bge-m3",
            dimensions=1024
        )
