# Hush Providers

> LLM, embedding và reranking providers cho Hush workflow engine.

## Cài đặt

```bash
# Qua meta-package (khuyến nghị)
uv pip install "hush-ai[standard] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# Với provider cụ thể
uv pip install "hush-ai[openai,gemini] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# Hoặc editable (cho development)
git clone https://github.com/batman1m2001-cyber/Hush-ai.git && cd hush
uv pip install -e hush-core -e hush-providers
```

Xem chi tiết tại [Cài đặt và Thiết lập](../hush-tutorial/docs/01-cai-dat-va-thiet-lap.md).

## Quick Start

### LLM Node

```python
from hush.core import Hush, GraphNode, START, END, PARENT
from hush.providers import PromptNode, LLMNode

async def main():
    with GraphNode(name="chat") as graph:
        prompt = PromptNode(
            name="prompt",
            inputs={
                "prompt": {"system": "Bạn là trợ lý AI.", "user": "{question}"},
                "question": PARENT["question"]
            },
            outputs={"messages": PARENT}
        )
        llm = LLMNode(
            name="llm",
            resource_key="gpt-4o",
            inputs={"messages": PARENT["messages"]},
            outputs={"content": PARENT["answer"]}
        )
        START >> prompt >> llm >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"question": "Hello!"})
```

### Embedding Node

```python
from hush.providers import EmbeddingNode

embed = EmbeddingNode(
    name="embed",
    resource_key="bge-m3",
    inputs={"texts": PARENT["documents"]},
    outputs={"embeddings": PARENT}
)
```

### Rerank Node

```python
from hush.providers import RerankNode

rerank = RerankNode(
    name="rerank",
    resource_key="bge-reranker",
    inputs={"query": PARENT["query"], "documents": PARENT["docs"]},
    outputs={"ranked_docs": PARENT}
)
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
