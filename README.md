# Hush

> Async workflow orchestration engine cho GenAI applications.

## Features

- **DAG-based workflows** - Định nghĩa workflows với nodes và edges
- **Async-first** - Native async execution với parallel processing
- **Built-in tracing** - Observability với SQLite và external backends
- **Provider agnostic** - OpenAI, Azure, Gemini, vLLM, ONNX
- **Type-safe state** - O(1) state access với compile-time validation

## Cài đặt

```bash
# Khuyến nghị — đầy đủ cho phát triển
uv pip install "hush-ai[all] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# Nhẹ hơn — chỉ OpenAI + Langfuse
uv pip install "hush-ai[openai,langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# Tối thiểu — chỉ workflow engine
uv pip install "hush-ai[core] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"
```

Xem chi tiết tại [Cài đặt và Thiết lập](hush-tutorial/docs/01-cai-dat-va-thiet-lap.md).

## Quick Start

```python
import asyncio
from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT

async def main():
    with GraphNode(name="hello") as graph:
        step1 = CodeNode(
            name="greet",
            code_fn=lambda name: {"message": f"Hello, {name}!"},
            inputs={"name": PARENT["name"]},
            outputs={"message": PARENT}
        )
        START >> step1 >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"name": "World"})
    print(result["message"])  # Hello, World!

asyncio.run(main())
```

## Sử dụng với LLM

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
    result = await engine.run(inputs={"question": "Python là gì?"})
    print(result["answer"])

asyncio.run(main())
```

## Documentation

| Tài liệu | Mô tả |
|----------|-------|
| [hush-tutorial/docs/](hush-tutorial/docs/) | User documentation - tutorials, guides, examples |
| [architecture/](architecture/) | Internal documentation - cho developers và AI |

## Packages

| Package | Description |
|---------|-------------|
| [hush-core](hush-core/) | Core workflow engine |
| [hush-providers](hush-providers/) | LLM, embedding, reranking providers |
| [hush-observability](hush-observability/) | Tracing backends (Langfuse, Phoenix) |
| [hush-tutorial](hush-tutorial/) | Tutorials và examples |
| [hush-vscode-traceview](hush-vscode-traceview/) | VS Code extension |

## Local Trace Viewer

Traces được tự động lưu vào `~/.hush/traces.db`. Xem traces bằng:

```bash
# Web UI
python -m hush.core.ui.server
# Mở http://localhost:8765
```

Hoặc cài VS Code extension: [hush-vscode-traceview](hush-vscode-traceview/)

## License

MIT
