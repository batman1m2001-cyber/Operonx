# Quickstart

Hướng dẫn chạy workflow đầu tiên với Hush.

> **Ví dụ chạy được**: `examples/01_hello_world.py`, `examples/02_data_pipeline.py`

## 1. Cài đặt

```bash
pip install hush-ai[standard]
```

Xem chi tiết tại [Cài đặt và Thiết lập](01-cai-dat-va-thiet-lap.md).

## 2. Hello World

```python
import asyncio
from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT

async def main():
    with GraphNode(name="hello-world") as graph:
        greet = CodeNode(
            name="greet",
            code_fn=lambda name: {"greeting": f"Xin chào, {name}!"},
            inputs={"name": PARENT["name"]},
            outputs={"greeting": PARENT}
        )
        START >> greet >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"name": "Hush"})
    print(result["greeting"])  # Xin chào, Hush!

asyncio.run(main())
```

## 3. Multi-step Pipeline

```python
import asyncio
from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT

async def main():
    with GraphNode(name="data-pipeline") as graph:
        fetch = CodeNode(
            name="fetch",
            code_fn=lambda: {"data": [1, 2, 3, 4, 5]},
            outputs={"data": PARENT}
        )
        transform = CodeNode(
            name="transform",
            code_fn=lambda data: {"transformed": [x * 2 for x in data]},
            inputs={"data": PARENT["data"]},
            outputs={"transformed": PARENT}
        )
        aggregate = CodeNode(
            name="aggregate",
            code_fn=lambda data: {"total": sum(data)},
            inputs={"data": PARENT["transformed"]},
            outputs={"total": PARENT}
        )
        START >> fetch >> transform >> aggregate >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})
    print(f"Data: {result['data']}")             # [1, 2, 3, 4, 5]
    print(f"Transformed: {result['transformed']}") # [2, 4, 6, 8, 10]
    print(f"Total: {result['total']}")            # 30

asyncio.run(main())
```

## 4. Sử dụng LLM

### Bước 1: Cấu hình resources.yaml

```yaml
llm:gpt-4o:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: gpt-4o
```

### Bước 2: Đặt biến môi trường

```bash
export OPENAI_API_KEY=sk-your-api-key
export HUSH_CONFIG=/path/to/resources.yaml
```

### Bước 3: Workflow với LLM

```python
import asyncio
from hush.core import Hush, GraphNode, START, END, PARENT
from hush.providers import PromptNode, LLMNode

async def main():
    with GraphNode(name="chat-workflow") as graph:
        prompt = PromptNode(
            name="prompt",
            inputs={
                "prompt": {
                    "system": "Bạn là trợ lý AI thân thiện.",
                    "user": "{question}"
                },
                "question": PARENT["question"]
            }
        )
        llm = LLMNode(
            name="llm",
            resource_key="gpt-4o",
            inputs={"messages": prompt["messages"]},
            outputs={"content": PARENT["answer"]}
        )
        START >> prompt >> llm >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"question": "Python là gì?"})
    print(f"Trả lời: {result['answer']}")

asyncio.run(main())
```

## Khái niệm chính

| Khái niệm | Mô tả |
|-----------|-------|
| `GraphNode` | Container chứa workflow |
| `CodeNode` | Node chạy Python function |
| `PromptNode` | Node tạo messages cho LLM |
| `LLMNode` | Node gọi LLM qua ResourceHub |
| `START >> node >> END` | Kết nối nodes thành pipeline |
| `PARENT["key"]` | Lấy data từ state của parent graph |
| `inputs` | Mapping input variables cho node |
| `outputs` | Mapping output variables từ node |

## Tiếp theo

- [Core Concepts](03-core-concepts.md) — Hiểu sâu các khái niệm cốt lõi
- [LLM Integration](04-llm-integration.md) — Chi tiết về LLM providers
