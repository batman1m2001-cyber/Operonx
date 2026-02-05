# Reranker Provider

## Overview

`BaseReranker` là abstract class cho reranking providers (TEI, vLLM, Pinecone, HuggingFace, ONNX).

Reranker dùng để re-score và sort documents theo relevance với query.

Location: `hush-providers/hush/providers/rerankers/base.py`

## Class Definition

```python
class BaseReranker(ABC):
    """Abstract base class for reranking."""

    @abstractmethod
    async def run(
        query: str,
        texts: List[str],
        top_k: int = 3,
        threshold: float = 0.0,
        **kwargs
    ) -> List[Dict]:
        """
        Rerank texts based on relevance to query.

        Parameters:
            query: Search query
            texts: List of texts to rerank
            top_k: Return top K results
            threshold: Minimum score threshold

        Returns:
            List of dicts với text, score, index
        """
        pass
```

## Configuration

```python
class RerankingType(Enum):
    COHERE = "cohere"
    TEXT_EMBEDDING_INFERENCE = "tei"
    VLLM = "vllm"
    PINECONE = "pinecone"
    HF = "hf"      # HuggingFace Transformers
    ONNX = "onnx"  # ONNX Runtime

class RerankingConfig(YamlModel):
    _type: ClassVar[str] = "reranking"
    _category: ClassVar[str] = "reranking"

    api_type: RerankingType = RerankingType.VLLM
    api_key: Optional[str] = None
    api_version: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
```

## Factory Pattern

```python
class RerankingFactory:
    @staticmethod
    def create(config: RerankingConfig) -> BaseReranker:
        if config.api_type == RerankingType.TEXT_EMBEDDING_INFERENCE:
            return TEIReranker(config)
        elif config.api_type == RerankingType.VLLM:
            return VLLMReranker(config)
        elif config.api_type == RerankingType.PINECONE:
            return PineconeReranker(config)
        elif config.api_type == RerankingType.HF:
            return HFReranker(config)
        elif config.api_type == RerankingType.ONNX:
            return ONNXReranker(config)
        else:
            raise ValueError(f"Unsupported: {config.api_type}")
```

## Core Method

### run()

```python
async def run(
    query: str,
    texts: List[str],
    top_k: int = 3,
    threshold: float = 0.0,
    **kwargs
) -> List[Dict]:
    """
    Parameters:
        query: Search query để rank against
        texts: Documents cần rerank
        top_k: Số results trả về (default: 3)
        threshold: Score tối thiểu (default: 0.0)

    Returns:
        [
            {"text": "...", "score": 0.95, "index": 2},
            {"text": "...", "score": 0.87, "index": 0},
            {"text": "...", "score": 0.72, "index": 1},
        ]

    Note: Results sorted by score descending
    """
```

## Supported Providers

| Provider | Type | Notes |
|----------|------|-------|
| TEI | `tei` | HuggingFace Text Embedding Inference |
| vLLM | `vllm` | vLLM reranking endpoint |
| Pinecone | `pinecone` | Pinecone Rerank API |
| HuggingFace | `hf` | Local cross-encoder models |
| ONNX | `onnx` | Optimized ONNX runtime |

## Usage

### Direct Factory

```python
from hush.providers.rerankers.factory import RerankingFactory
from hush.providers.rerankers.config import RerankingConfig, RerankingType

config = RerankingConfig(
    api_type=RerankingType.PINECONE,
    api_key="your-api-key",
    base_url="https://api.pinecone.io/rerank",
    model="bge-reranker-v2-m3"
)

reranker = RerankingFactory.create(config)

# Rerank documents
query = "What is machine learning?"
texts = [
    "Machine learning is a subset of AI...",
    "The weather today is sunny...",
    "Deep learning uses neural networks..."
]

results = await reranker.run(query, texts, top_k=2)
# [
#     {"text": "Machine learning is...", "score": 0.95, "index": 0},
#     {"text": "Deep learning uses...", "score": 0.89, "index": 2}
# ]
```

### Via ResourceHub

```python
from hush.providers.registry import RerankPlugin
from hush.core.registry import get_hub

RerankPlugin.register()

hub = get_hub()
reranker = hub.reranker("bge-reranker")  # Loads configs/reranking/bge-reranker.yaml
```

## YAML Configuration

```yaml
# configs/reranking/bge-reranker.yaml
api_type: pinecone
api_key: ${PINECONE_API_KEY}
base_url: https://api.pinecone.io/rerank
model: bge-reranker-v2-m3
```

## RAG Pipeline Integration

```python
# Typical RAG flow
query = "What is machine learning?"

# 1. Vector search (fast, approximate)
candidates = await vector_store.search(query, top_k=100)

# 2. Rerank (accurate, slower)
reranked = await reranker.run(
    query=query,
    texts=[doc.text for doc in candidates],
    top_k=10,
    threshold=0.5
)

# 3. Use top results for LLM context
context = "\n".join([r["text"] for r in reranked])
```

## Thresholds và Top-K

```python
# Get all results above threshold
results = await reranker.run(
    query=query,
    texts=texts,
    top_k=len(texts),  # Return all
    threshold=0.7       # But only score >= 0.7
)

# Get exactly top 5
results = await reranker.run(
    query=query,
    texts=texts,
    top_k=5,
    threshold=0.0  # No threshold
)
```
