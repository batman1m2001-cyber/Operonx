# hush-providers

LLM, embedding, and reranking provider integrations for Hush workflows.

[![PyPI](https://img.shields.io/pypi/v/hush-providers)](https://pypi.org/project/hush-providers/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)

## Installation

```bash
pip install hush-providers
```

## Quick Start

### LLM (chain = prompt + LLM combined)

```python
from hush.core import Hush, GraphOp, START, END, PARENT
from hush.providers import chain

async def main():
    with GraphOp(name="chat") as graph:
        chat = chain(
            resource="gpt-4o",
            template={"system": "You are a helpful assistant.", "user": "{question}"},
            question=PARENT["question"],
        )
        START >> chat >> END

    result = await Hush(graph).run(inputs={"question": "What is Python?"})
    print(result["content"])
```

### Embedding

```python
from hush.providers import EmbeddingOp

embed = EmbeddingOp.of(resource="bge-m3", texts=PARENT["documents"])
```

### Reranking

```python
from hush.providers import RerankOp

rerank = RerankOp.of(resource="bge-reranker", query=PARENT["query"], documents=PARENT["docs"])
```

## Supported Providers

| Type | Providers |
|------|-----------|
| **LLM** | OpenAI, Azure OpenAI, Google Gemini, vLLM |
| **Embedding** | OpenAI/vLLM, TEI, HuggingFace, ONNX |
| **Reranking** | vLLM, Pinecone, Cohere, HuggingFace, ONNX |

## Configuration

Providers are configured via YAML resource files:

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

## Feature Flags

Install only the providers you need:

```bash
pip install "hush-providers[openai]"       # OpenAI + Azure
pip install "hush-providers[gemini]"       # Google Gemini
pip install "hush-providers[onnx]"         # ONNX Runtime
pip install "hush-providers[all-light]"    # All without PyTorch
pip install "hush-providers[all]"          # Everything
```

## Rust Backend

All providers have native Rust implementations via [hush-providers (crate)](https://crates.io/crates/hush-providers) — direct HTTP calls without Python overhead.

## Related Packages

| Package | Description |
|---------|-------------|
| [hush-icore](https://pypi.org/project/hush-icore/) | Core workflow engine (required) |
| [hush-telemetry](https://pypi.org/project/hush-telemetry/) | Tracing with token/cost tracking |
| [hush-serve](https://pypi.org/project/hush-serve/) | Serve workflows as HTTP APIs |

## License

Apache 2.0
