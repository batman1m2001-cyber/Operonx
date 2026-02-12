# Hush Providers

> LLM, embedding và reranking providers cho Hush workflow engine.

## Cài đặt

```bash
# Với pip
pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"
pip install "hush-providers @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-providers"

# Với uv
uv pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"
uv pip install "hush-providers @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-providers"

# Editable (cho development)
git clone https://github.com/batman1m2001-cyber/Hush-ai.git && cd Hush-ai
uv pip install -e hush-core -e hush-providers
```

Xem chi tiết tại [Cài đặt và Thiết lập](../hush-tutorial/docs/01-cai-dat-va-thiet-lap.md).

## Quick Start

### LLM Op

```python
from hush.core import Hush, GraphOp, START, END, PARENT
from hush.providers import ChainOp

async def main():
    with GraphOp(name="chat") as graph:
        chat = ChainOp.of(
            resource_key="gpt-4o",
            template={"system": "Bạn là trợ lý AI.", "user": "{question}"},
            question=PARENT["question"],
        )
        START >> chat >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"question": "Hello!"})
```

### Embedding Op

```python
from hush.providers import EmbeddingOp

embed = EmbeddingOp.of(resource_key="bge-m3", texts=PARENT["documents"])
```

### Rerank Op

```python
from hush.providers import RerankOp

rerank = RerankOp.of(resource_key="bge-reranker", query=PARENT["query"], documents=PARENT["docs"])
```

## Supported Providers

| Type | Providers |
|------|-----------|
| LLM | OpenAI, Azure, Gemini, vLLM |
| Embedding | vLLM, TEI, HuggingFace, ONNX |
| Reranking | vLLM, Pinecone, HuggingFace, ONNX |

## Configuration

```yaml
# resources.yaml
llm:gpt-4o:
  _class: OpenAIConfig
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: gpt-4o

embedding:bge-m3:
  _class: EmbeddingConfig
  api_type: vllm
  base_url: http://localhost:8000/v1
  model: BAAI/bge-m3
```

## Features

- Streaming và non-streaming responses
- Token counting và usage tracking
- Multimodal input (images)
- Tool/function calling
- Batch processing

## Documentation

- [User Docs](../hush-tutorial/docs/) - Tutorials và guides
- [Architecture](../architecture/providers/) - Internal documentation
  - [LLM Abstraction](../architecture/providers/llm-abstraction.md)
  - [Embedding Provider](../architecture/providers/embedding-provider.md)
  - [Reranker Provider](../architecture/providers/reranker-provider.md)
  - [Adding New Provider](../architecture/providers/adding-new-provider.md)

## License

MIT
