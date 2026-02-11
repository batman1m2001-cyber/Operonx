# hush-providers

LLM, embedding, and reranking provider integrations for Hush workflows.

## Module Structure

```
hush/providers/
├── llms/               # LLM provider implementations
│   ├── base.py         # BaseLLM abstract class
│   ├── config.py       # LLMConfig, OpenAIConfig, AzureConfig, GeminiConfig
│   ├── factory.py      # LLMFactory for instantiation
│   ├── openai.py       # OpenAI implementation
│   ├── azure.py        # Azure OpenAI implementation
│   └── gemini.py       # Google Gemini implementation
├── embeddings/         # Embedding provider implementations
│   ├── base.py         # BaseEmbedder abstract class
│   ├── config.py       # EmbeddingConfig
│   ├── factory.py      # EmbeddingFactory
│   ├── vllm.py         # vLLM embedding
│   ├── tei.py          # Text Embeddings Inference
│   ├── huggingface.py  # HuggingFace transformers
│   └── onnx.py         # ONNX runtime
├── rerankers/          # Reranking provider implementations
│   ├── base.py         # BaseReranker abstract class
│   ├── config.py       # RerankingConfig
│   ├── factory.py      # RerankingFactory
│   ├── vllm.py, tei.py, huggingface.py, onnx.py, pinecone.py
├── auth/               # Authentication providers
│   ├── config.py       # KeycloakTokenConfig
│   ├── factory.py      # AuthFactory
│   └── keycloak.py     # Keycloak token provider
├── nodes/              # Workflow node wrappers
│   ├── llm.py          # LLMNode, llm_()
│   ├── llm_chain.py    # LLMChainNode, llmchain_()
│   ├── embedding.py    # EmbeddingNode, embedding_()
│   ├── rerank.py       # RerankNode, rerank_()
│   └── prompt.py       # PromptNode, prompt_()
└── registry/           # Plugin registration
    ├── llm_plugin.py
    ├── embedding_plugin.py
    ├── rerank_plugin.py
    └── auth_plugin.py
```

## Key Files to Read First

1. `llms/base.py` - BaseLLM interface (stream, generate, generate_batch)
2. `nodes/llm.py` - LLMNode for workflow integration
3. `registry/llm_plugin.py` - Plugin registration pattern

## Provider Pattern

Each provider type follows the same structure:

```
provider_type/
├── base.py      # Abstract base class with interface
├── config.py    # Pydantic config classes
├── factory.py   # Factory for instantiation by type
└── {impl}.py    # Concrete implementations
```

## Adding a New LLM Provider

1. Create config in `llms/config.py`:
```python
class MyProviderConfig(LLMConfig):
    type: Literal["my_provider"] = "my_provider"
    api_key: str
    # provider-specific fields
```

2. Create implementation in `llms/my_provider.py`:
```python
from hush.providers.llms.base import BaseLLM
from hush.providers.llms.config import MyProviderConfig

class MyProviderModel(BaseLLM):
    def __init__(self, config: MyProviderConfig):
        super().__init__(config)
        # Initialize client

    async def stream(self, messages, **kwargs) -> AsyncGenerator[ChatCompletionChunk, None]:
        # Implement streaming
        yield chunk

    async def generate(self, messages, **kwargs) -> ChatCompletion:
        # Implement non-streaming
        return completion
```

3. Register in `llms/factory.py`:
```python
LLMFactory.register("my_provider", MyProviderModel)
```

4. Export in `llms/__init__.py`

## Adding a New Embedding Provider

Same pattern as LLM:

```python
from hush.providers.embeddings.base import BaseEmbedder

class MyEmbedder(BaseEmbedder):
    async def embed(self, texts: List[str]) -> List[List[float]]:
        # Return list of embedding vectors
        pass

    async def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        # Batch processing
        pass
```

## Adding a New Reranker

```python
from hush.providers.rerankers.base import BaseReranker

class MyReranker(BaseReranker):
    async def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        # Return list of {"index": int, "score": float, "text": str}
        pass
```

## Workflow Nodes

All provider nodes have shorthand functions (recommended) and full class equivalents.

### Shorthand Functions (Recommended)
```python
from hush.providers import llmchain_, llm_, prompt_, embedding_, rerank_

# Prompt + LLM all-in-one
chat = llmchain_(resource_key="gpt-4o", template={"system": "...", "user": "{query}"}, query=PARENT["query"])

# Separate LLM call
llm = llm_(resource_key="gpt-4o", messages=PARENT["messages"])

# Separate prompt formatting
p = prompt_(template={"system": "...", "user": "{query}"}, query=PARENT["query"])

# Embeddings
embed = embedding_(resource_key="bge-m3", texts=PARENT["texts"])

# Reranking
rerank = rerank_(resource_key="bge-m3", query=PARENT["query"], documents=PARENT["docs"])
```

### Full Class Equivalents
```python
from hush.providers import LLMChainNode, LLMNode, PromptNode, EmbeddingNode, RerankNode

# LLMChainNode = Prompt + LLM combined
chain = LLMChainNode(
    name="chat",
    resource_key="gpt-4o",
    inputs={"template": {"system": "...", "user": "{input}"}, "input": PARENT["query"]},
    outputs={"content": PARENT["answer"]}
)

# LLMNode = Raw LLM call
llm = LLMNode(
    name="generate",
    resource_key="gpt-4o",
    inputs={"messages": PARENT["messages"]},
    outputs={"content": PARENT["response"]},
)

# PromptNode = Template formatting
prompt = PromptNode(
    name="format",
    inputs={"template": {"system": "...", "user": "{question}"}, "question": PARENT["question"]},
    outputs={"messages": PARENT}
)
```

## Plugin Registration

Plugins auto-register resource types from YAML:

```yaml
# resources.yaml
llm:
  gpt-4o:
    type: openai
    model: gpt-4o
    api_key: ${OPENAI_API_KEY}

embeddings:
  bge-m3:
    type: onnx
    model_path: /models/bge-m3
```

```python
# In code, plugins resolve configs:
from hush.core.registry import get_hub

hub = get_hub()
llm_config = hub.get("llm", "gpt-4o")  # Returns OpenAIConfig
```

## Feature Flags (pyproject.toml)

Optional dependencies for specific providers:
- `[openai]` - OpenAI + Azure OpenAI (already in base deps, no-op extra)
- `[gemini]` - Google Cloud AI Platform + requests
- `[bedrock]` - AWS Bedrock (boto3)
- `[onnx]` - ONNX Runtime
- `[huggingface]` - Transformers + PyTorch
- `[embeddings]` - ONNX embedding
- `[rerankers]` - ONNX reranking
- `[all-light]` - All providers without PyTorch
- `[all]` - Everything including PyTorch

## Testing Providers

Mock external APIs in tests:
```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_llm_node():
    with patch("hush.providers.llms.openai.OpenAISDKModel") as mock:
        mock.return_value.generate = AsyncMock(return_value=mock_completion)
        # Test node execution
```

## Error Handling

Use exception classes from hush-core:
- `PromptError` - Template formatting failures
- `EmbeddingError` - Embedding provider failures
- `RerankError` - Reranking failures

```python
from hush.core.exceptions import EmbeddingError

raise EmbeddingError(
    message="Connection failed",
    resource_key="bge-m3",
    text_count=100,
    original_error=e
)
```
