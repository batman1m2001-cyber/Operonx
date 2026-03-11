# Embedding Provider

## Overview

`BaseEmbedder` là abstract class cho text embedding providers (TEI, vLLM, HuggingFace, ONNX).

Location: `hush-providers/hush/providers/embeddings/base.py`

## Class Definition

```python
class BaseEmbedder(ABC):
    """Abstract base class for text embedding."""

    __slots__ = []  # No instance attributes in base

    @abstractmethod
    async def run(
        self,
        texts: Union[str, List[str]],
        **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Embed texts into vectors.

        Returns:
            Dict với key "embeddings" chứa list of vectors
        """
        pass

    def run_sync(
        self,
        texts: Union[str, List[str]],
        **kwargs: Any
    ) -> Dict[str, Any]:
        """Synchronous wrapper cho run()."""
        pass

    @abstractmethod
    def get_output_dim(self) -> int:
        """Return embedding dimension."""
        pass
```

## Configuration

```python
class EmbeddingType(Enum):
    OPENAI = "openai"
    AZURE = "azure"
    GEMINI = "gemini"
    TEXT_EMBEDDING_INFERENCE = "tei"
    VLLM = "vllm"
    HF = "hf"      # HuggingFace Transformers
    ONNX = "onnx"  # ONNX Runtime

class EmbeddingConfig(YamlModel):
    _type: ClassVar[str] = "embedding"
    _category: ClassVar[str] = "embedding"

    api_type: EmbeddingType = EmbeddingType.VLLM
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    embed_batch_size: Optional[int] = None
    model: Optional[str] = None
    dimensions: Optional[int] = None
```

## Factory Pattern

```python
class EmbeddingFactory:
    @staticmethod
    def create(config: EmbeddingConfig) -> BaseEmbedder:
        if config.api_type == EmbeddingType.TEXT_EMBEDDING_INFERENCE:
            return TEIEmbedding(config)
        elif config.api_type == EmbeddingType.VLLM:
            return VLLMEmbedding(config)
        elif config.api_type == EmbeddingType.HF:
            return HFEmbedding(config)
        elif config.api_type == EmbeddingType.ONNX:
            return ONNXEmbedding(config)
        else:
            raise ValueError(f"Unsupported: {config.api_type}")
```

## Core Methods

### run()

Async embedding generation:

```python
async def run(
    self,
    texts: Union[str, List[str]],
    **kwargs
) -> Dict[str, Any]:
    """
    Parameters:
        texts: Single string hoặc list of strings

    Returns:
        {
            "embeddings": [[0.1, 0.2, ...], [0.3, 0.4, ...], ...]
        }
    """
```

### run_sync()

Synchronous wrapper cho environments không có async:

```python
def run_sync(self, texts, **kwargs) -> Dict[str, Any]:
    """
    Handles:
    - Running event loop already exists → ThreadPoolExecutor
    - No event loop → asyncio.run()
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Run in new thread
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, self.run(texts, **kwargs))
                return future.result()
        else:
            return loop.run_until_complete(self.run(texts, **kwargs))
    except RuntimeError:
        return asyncio.run(self.run(texts, **kwargs))
```

### get_output_dim()

Return embedding dimension cho vector DB:

```python
def get_output_dim(self) -> int:
    """
    Returns dimensionality of embeddings.
    Example: 1024 for BGE-M3, 1536 for OpenAI ada-002
    """
```

## Supported Providers

| Provider | Type | Use Case |
|----------|------|----------|
| TEI | `tei` | HuggingFace Text Embedding Inference server |
| vLLM | `vllm` | vLLM OpenAI-compatible server |
| HuggingFace | `hf` | Local transformers models |
| ONNX | `onnx` | Optimized ONNX runtime |

## Usage

### Direct Factory

```python
from hush.providers.embeddings.factory import EmbeddingFactory
from hush.providers.embeddings.config import EmbeddingConfig, EmbeddingType

config = EmbeddingConfig(
    api_type=EmbeddingType.VLLM,
    base_url="http://localhost:8000/v1/embeddings",
    model="BAAI/bge-m3",
    dimensions=1024
)

embedder = EmbeddingFactory.create(config)

# Async
result = await embedder.run(["Hello world", "How are you?"])
vectors = result["embeddings"]  # [[0.1, ...], [0.2, ...]]

# Sync
result = embedder.run_sync("Hello world")
```

### Via ResourceHub

```python
from hush.providers.registry import EmbeddingPlugin
from hush.core.registry import get_hub

EmbeddingPlugin.register()

hub = get_hub()
embedder = hub.embedding("bge-m3")  # Loads configs/embedding/bge-m3.yaml
```

## YAML Configuration

```yaml
# configs/embedding/bge-m3.yaml
api_type: vllm
base_url: http://localhost:8000/v1/embeddings
model: BAAI/bge-m3
dimensions: 1024
embed_batch_size: 32
```

## Batching

Hầu hết implementations tự động batch requests:

```python
# Internal batching
async def run(self, texts: List[str], **kwargs):
    batch_size = self.config.embed_batch_size or 32
    results = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        batch_result = await self._embed_batch(batch)
        results.extend(batch_result)
    return {"embeddings": results}
```
