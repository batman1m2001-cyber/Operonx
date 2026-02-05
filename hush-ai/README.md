# Hush AI

> Meta-package cho Hush ecosystem.

Cài đặt package này để có đầy đủ tính năng của Hush workflow engine.

## Cài đặt

```bash
# Standard - workflow engine + LLM providers (OpenAI)
pip install hush-ai[standard]

# Core only - workflow engine với local tracing
pip install hush-ai[core]

# Full - tất cả providers + observability
pip install hush-ai[all]
```

## Installation Tiers

| Tier | Bao gồm |
|------|---------|
| `hush-ai[core]` | Workflow engine + LocalTracer + Web UI |
| `hush-ai[standard]` | Core + LLM providers (OpenAI) |
| `hush-ai[all]` | Standard + tất cả providers + observability |
| `hush-ai[full]` | All + heavy ML frameworks (torch, transformers) |

## Provider Extras

```bash
# LLM providers
pip install hush-ai[openai]      # OpenAI
pip install hush-ai[azure]       # Azure OpenAI
pip install hush-ai[gemini]      # Google Gemini

# Local inference
pip install hush-ai[onnx]        # ONNX Runtime (nhẹ)
pip install hush-ai[huggingface] # HuggingFace + PyTorch (nặng)

# Observability
pip install hush-ai[standard,langfuse]  # Thêm Langfuse
pip install hush-ai[standard,otel]      # Thêm OpenTelemetry
```

## Quick Start

```python
import asyncio
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
    result = await engine.run(inputs={"question": "Python là gì?"})
    print(result["answer"])

asyncio.run(main())
```

## Local Trace Viewer

Traces tự động lưu vào `~/.hush/traces.db`. Xem bằng:

```bash
python -m hush.core.ui.server
# Mở http://localhost:8765
```

Hoặc dùng VS Code extension: [hush-vscode-traceview](../hush-vscode-traceview/)

## Packages

| Package | Mô tả |
|---------|-------|
| [hush-core](../hush-core/) | Workflow engine, state management, local tracing |
| [hush-providers](../hush-providers/) | LLM, embedding, reranking providers |
| [hush-observability](../hush-observability/) | Langfuse, OpenTelemetry integration |

## License

MIT
