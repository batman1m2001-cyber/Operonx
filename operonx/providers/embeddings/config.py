from enum import Enum
from typing import ClassVar, Optional

from operonx.core.utils import YamlModel


class EmbeddingType(Enum):
    OPENAI = "openai"
    AZURE = "azure"
    GEMINI = "gemini"
    TEXT_EMBEDDING_INFERENCE = "tei"
    VLLM = "vllm"
    HF = "hf"  # HuggingFace Transformers
    ONNX = "onnx"  # ONNX Runtime
    TRITON = "triton"  # Triton Inference Server (gRPC)


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
        max_length (Optional[int]): Truncation length, in tokens. Used by
            ONNX and by Triton in token-id mode. **Inert when the server
            tokenises** (see ``input_name``) — it is the server's decision
            there, and a value left in the resource means nothing.
        output_name (Optional[str]): Which output tensor to read. Matters
            for models exporting more than one: BGE-M3 emits both
            ``token_embeddings`` and ``sentence_embedding``, and
            mean-pooling the first gives a different vector from the
            second. Pin it rather than trusting a default — an index built
            with one cannot be queried with the other.
        tokenizer_path (Optional[str]): Path to ``tokenizer.json``.
            Required by Triton in token-id mode, where the client
            tokenises. Cannot be derived from ``model``, which for Triton
            is a *served model name*, not a directory.
        input_name (Optional[str]): Triton only. Set it to the name of the
            model's text input (e.g. ``"TEXT"``) when the deployment has
            absorbed the tokeniser; the client then sends utf-8 strings as
            BYTES and loads no tokeniser. Leave unset for the original
            contract, where the client sends ``input_ids`` /
            ``attention_mask`` as INT64.
        ssl (bool): Triton only. Open the gRPC channel with TLS. Selected
            here rather than by a scheme on ``base_url``, which is a bare
            ``host:port``.
    """

    _category: ClassVar[str] = "embedding"

    api_type: EmbeddingType = EmbeddingType.VLLM
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    embed_batch_size: Optional[int] = None
    model: Optional[str] = None
    dimensions: Optional[int] = None
    max_length: Optional[int] = None
    output_name: Optional[str] = None
    tokenizer_path: Optional[str] = None
    input_name: Optional[str] = None
    ssl: bool = False
    # Triton only. `timeout` was hardcoded at 30s inside the op, so a
    # deployment that needed longer had to edit operonx. `retries` covers
    # the failure that a longer timeout cannot: a deadline that expired
    # with the budget spent somewhere other than inference — a cold
    # channel's TLS handshake, or a response that landed while the event
    # loop was busy. Both succeed immediately on a second attempt.
    timeout: float = 30.0
    max_retries: int = 2
    retry_base_delay: float = 0.5
    retry_max_delay: float = 8.0

    @classmethod
    def default(cls) -> "EmbeddingConfig":
        """Load default config with hardcoded values"""
        return cls(
            api_type=EmbeddingType.VLLM,
            api_key="your-api-key",
            base_url="http://localhost:8000/v1/embeddings",
            embed_batch_size=None,
            model="BAAI/bge-m3",
            dimensions=1024,
        )
