# Adding New Provider

## Overview

Guide thêm provider mới cho LLM, Embedding, hoặc Reranker.

## 1. LLM Provider

### Step 1: Create Config (nếu cần)

```python
# hush-providers/hush/providers/llms/config.py

class MyLLMConfig(LLMConfig):
    _type: ClassVar[str] = "my_llm"
    _category: ClassVar[str] = "llm"
    api_type: LLMType = LLMType.MY_LLM  # Add to LLMType enum

    # Provider-specific fields
    api_key: str
    base_url: str
    model: str
    custom_param: Optional[str] = None
```

### Step 2: Implement BaseLLM

```python
# hush-providers/hush/providers/llms/my_llm.py

from hush.providers.llms.base import BaseLLM
from hush.providers.llms.config import MyLLMConfig

class MyLLMModel(BaseLLM):
    def __init__(self, config: MyLLMConfig):
        super().__init__(config)
        self.client = MySDK(
            api_key=config.api_key,
            base_url=config.base_url
        )

    async def stream(
        self,
        messages: List[ChatCompletionMessageParam],
        temperature: float = 0.0,
        top_p: float = 0.1,
        **kwargs
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        """Implement streaming."""
        params = self._prepare_params(
            model=self.config.model,
            messages=messages,
            stream=True,
            temperature=temperature,
            top_p=top_p,
            **kwargs
        )

        async for chunk in self.client.chat.completions.create(**params):
            yield chunk

    async def generate(
        self,
        messages: List[ChatCompletionMessageParam],
        temperature: float = 0.0,
        top_p: float = 0.1,
        **kwargs
    ) -> ChatCompletion:
        """Implement non-streaming."""
        params = self._prepare_params(
            model=self.config.model,
            messages=messages,
            stream=False,
            temperature=temperature,
            top_p=top_p,
            **kwargs
        )

        return await self.client.chat.completions.create(**params)

    # Optional: Override batch processing
    async def generate_batch(
        self,
        batch_messages: List[List[ChatCompletionMessageParam]],
        **kwargs
    ) -> List[ChatCompletion]:
        """Parallel batch processing."""
        tasks = [
            self.generate(messages, **kwargs)
            for messages in batch_messages
        ]
        return await asyncio.gather(*tasks)
```

### Step 3: Register in Factory

```python
# hush-providers/hush/providers/llms/factory.py

class LLMFactory:
    @staticmethod
    def create(config: LLMConfig) -> BaseLLM:
        # ... existing providers ...
        elif config.api_type == LLMType.MY_LLM:
            from .my_llm import MyLLMModel
            return MyLLMModel(config=config)
```

### Step 4: Update Plugin (for ResourceHub)

```python
# hush-providers/hush/providers/registry/llm_plugin.py

from hush.providers.llms.config import MyLLMConfig

class LLMPlugin:
    @classmethod
    def register(cls):
        # ... existing registrations ...
        REGISTRY.register(MyLLMConfig, LLMFactory.create)
```

## 2. Embedding Provider

### Step 1: Implement BaseEmbedder

```python
# hush-providers/hush/providers/embeddings/my_embedder.py

from hush.providers.embeddings.base import BaseEmbedder
from hush.providers.embeddings.config import EmbeddingConfig

class MyEmbedder(BaseEmbedder):
    __slots__ = ['client', 'config']

    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self.client = MySDK(
            api_key=config.api_key,
            base_url=config.base_url
        )

    async def run(
        self,
        texts: Union[str, List[str]],
        **kwargs
    ) -> Dict[str, Any]:
        """Generate embeddings."""
        if isinstance(texts, str):
            texts = [texts]

        # Call your API
        response = await self.client.embeddings.create(
            model=self.config.model,
            input=texts
        )

        return {
            "embeddings": [item.embedding for item in response.data]
        }

    def get_output_dim(self) -> int:
        """Return embedding dimension."""
        return self.config.dimensions or 1024
```

### Step 2: Register in Factory

```python
# hush-providers/hush/providers/embeddings/factory.py

class EmbeddingFactory:
    @staticmethod
    def create(config: EmbeddingConfig) -> BaseEmbedder:
        # ... existing providers ...
        elif config.api_type == EmbeddingType.MY_EMBEDDER:
            from .my_embedder import MyEmbedder
            return MyEmbedder(config)
```

## 3. Reranker Provider

### Step 1: Implement BaseReranker

```python
# hush-providers/hush/providers/rerankers/my_reranker.py

from hush.providers.rerankers.base import BaseReranker
from hush.providers.rerankers.config import RerankingConfig

class MyReranker(BaseReranker):
    def __init__(self, config: RerankingConfig):
        self.config = config
        self.client = MySDK(
            api_key=config.api_key,
            base_url=config.base_url
        )

    async def run(
        self,
        query: str,
        texts: List[str],
        top_k: int = 3,
        threshold: float = 0.0,
        **kwargs
    ) -> List[Dict]:
        """Rerank texts."""
        # Call your API
        response = await self.client.rerank(
            query=query,
            documents=texts,
            model=self.config.model,
            top_n=top_k
        )

        # Format results
        results = []
        for item in response.results:
            if item.score >= threshold:
                results.append({
                    "text": texts[item.index],
                    "score": item.score,
                    "index": item.index
                })

        return results
```

### Step 2: Register in Factory

```python
# hush-providers/hush/providers/rerankers/factory.py

class RerankingFactory:
    @staticmethod
    def create(config: RerankingConfig) -> BaseReranker:
        # ... existing providers ...
        elif config.api_type == RerankingType.MY_RERANKER:
            from .my_reranker import MyReranker
            return MyReranker(config)
```

## Testing New Provider

### Unit Tests

```python
# tests/providers/test_my_provider.py

import pytest
from hush.providers.llms.my_llm import MyLLMModel
from hush.providers.llms.config import MyLLMConfig

@pytest.fixture
def config():
    return MyLLMConfig(
        api_key="test-key",
        base_url="http://localhost:8000",
        model="test-model"
    )

@pytest.mark.asyncio
async def test_generate(config):
    llm = MyLLMModel(config)
    response = await llm.generate([
        {"role": "user", "content": "Hello"}
    ])
    assert response.choices[0].message.content
```

### Integration Test

```python
# Test with real API
@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_api():
    config = MyLLMConfig.from_yaml_file("configs/llm/my-llm.yaml")
    llm = LLMFactory.create(config)

    # Test streaming
    chunks = []
    async for chunk in llm.stream([{"role": "user", "content": "Hi"}]):
        if chunk.choices[0].delta.content:
            chunks.append(chunk.choices[0].delta.content)

    assert len(chunks) > 0
```

## Checklist

- [ ] Add type to Enum (LLMType/EmbeddingType/RerankingType)
- [ ] Create Config class (nếu cần custom fields)
- [ ] Implement base class (BaseLLM/BaseEmbedder/BaseReranker)
- [ ] Register in Factory
- [ ] Update Plugin (for ResourceHub integration)
- [ ] Add YAML config example
- [ ] Write unit tests
- [ ] Write integration tests
- [ ] Update documentation
