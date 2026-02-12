# Quickstart

Hướng dẫn chạy workflow đầu tiên với Hush.

> **Ví dụ chạy được**: `examples/01_hello_world.py`, `examples/02_data_pipeline.py`

> **Yêu cầu trước khi bắt đầu:** Hoàn thành [Cài đặt và Thiết lập](01-cai-dat-va-thiet-lap.md) — bao gồm cài packages, tạo `.env`, và có `resources.yaml`. Mục 2-3 dưới đây chỉ cần `hush-core`. Mục 4 (LLM) cần `.env` + `resources.yaml` đã thiết lập.

## 1. Cài đặt

Xem [Cài đặt và Thiết lập](01-cai-dat-va-thiet-lap.md) để cài đặt Hush (hỗ trợ cả pip và uv).

## 2. Hello World

```python
import asyncio
from hush.core import Hush, GraphNode, code_node, START, END, PARENT

@code_node
def greet(name: str):
    return {"greeting": f"Xin chào, {name}!"}

async def main():
    with GraphNode(name="hello-world") as graph:
        step = greet(name=PARENT["name"])
        START >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"name": "Hush"})
    print(result["greeting"])  # Xin chào, Hush!

asyncio.run(main())
```

## 3. Multi-step Pipeline

```python
import asyncio
from hush.core import Hush, GraphNode, code_node, START, END, PARENT

@code_node
def fetch():
    return {"data": [1, 2, 3, 4, 5]}

@code_node
def transform(data: list):
    return {"transformed": [x * 2 for x in data]}

@code_node
def aggregate(data: list):
    return {"total": sum(data)}

async def main():
    with GraphNode(name="data-pipeline") as graph:
        f = fetch()
        t = transform(data=f["data"])
        a = aggregate(data=t["transformed"])
        START >> f >> t >> a >> END

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
from hush.providers import LLMChainNode

async def main():
    with GraphNode(name="chat-workflow") as graph:
        chat = LLMChainNode.of(
            resource_key="gpt-4o",
            template={"system": "Bạn là trợ lý AI thân thiện.", "user": "{question}"},
            question=PARENT["question"],
        )
        START >> chat >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"question": "Python là gì?"})
    print(f"Trả lời: {result['content']}")

asyncio.run(main())
```

## Khái niệm chính

| Khái niệm | Mô tả |
|-----------|-------|
| `GraphNode` | Container chứa workflow — **core** |
| `@code_node` | Decorator biến function thành CodeNode — **core** |
| `if_().else_()` | Rẽ nhánh có điều kiện — **core** |
| `START >> node >> END` | Kết nối nodes thành pipeline |
| `PARENT["key"]` | Lấy data từ state của parent graph / external inputs |
| `node["key"]` | Lấy output từ node anh em (sibling) |
| `outputs` | Mapping output — hoặc dùng `>> END` auto-forward |
| `node["key"] >> PARENT["key"]` | Output mapping via `>>` operator |
| `LLMChainNode.of()` | Gọi LLM — **add-on** (cài `hush-providers`) |

## Tiếp theo

- [Core Concepts](03-core-concepts.md) — Hiểu sâu các khái niệm cốt lõi
- [LLM Integration](04-llm-integration.md) — Chi tiết về LLM providers
