# Quickstart

Hướng dẫn chạy workflow đầu tiên với Hush.

> **Ví dụ chạy được**: `examples/01_hello_world/demo.py`, `examples/02_data_pipeline/demo.py`

> **Yêu cầu trước khi bắt đầu:** Hoàn thành [Cài đặt và Thiết lập](01-cai-dat-va-thiet-lap.md) — bao gồm cài packages, tạo `.env`, và có `resources.yaml`. Mục 2-3 dưới đây chỉ cần `hush-icore`. Mục 4 (LLM) cần `.env` + `resources.yaml` đã thiết lập.

## 1. Cài đặt

Xem [Cài đặt và Thiết lập](01-cai-dat-va-thiet-lap.md) để cài đặt Hush (hỗ trợ cả pip và uv).

## 2. Hello World

```python
import asyncio
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def greet(name: str):
    return {"greeting": f"Xin chào, {name}!"}

async def main():
    with GraphOp(name="hello-world") as graph:
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
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def fetch():
    return {"data": [1, 2, 3, 4, 5]}

@op
def transform(data: list):
    return {"transformed": [x * 2 for x in data]}

@op
def aggregate(data: list):
    return {"total": sum(data)}

async def main():
    with GraphOp(name="data-pipeline") as graph:
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

### Bước 2: Đặt API key trong `.env`

```dotenv
# .env
OPENAI_API_KEY=sk-your-api-key
```

### Bước 3: Workflow với LLM

```python
import asyncio
from hush.core import Hush, GraphOp, START, END, PARENT
from hush.providers import chain

async def main():
    with GraphOp(name="chat-workflow") as graph:
        chat = chain(
            resource="gpt-4o",
            template={"system": "Bạn là trợ lý AI thân thiện.", "user": "{question}"},
            question=PARENT["question"],
        )
        START >> chat >> END

    # env=True (mặc định) tự load .env, resources= chỉ định file provider
    engine = Hush(graph, resources="resources.yaml")
    result = await engine.run(inputs={"question": "Python là gì?"})
    print(f"Trả lời: {result['content']}")

asyncio.run(main())
```

## Khái niệm chính

| Khái niệm | Mô tả |
|-----------|-------|
| `GraphOp` | Container chứa workflow — **core** |
| `@op` | Decorator biến function thành FuncOp — **core** |
| `if_().else_()` | Rẽ nhánh có điều kiện — **core** |
| `START >> step >> END` | Kết nối ops thành pipeline |
| `PARENT["key"]` | Lấy data từ state của parent graph / external inputs |
| `step["key"]` | Lấy output từ op anh em (sibling) |
| `outputs` | Mapping output — hoặc dùng `>> END` auto-forward |
| `step["key"] >> PARENT["key"]` | Output mapping via `>>` operator |
| `chain()` | Gọi LLM — **add-on** (cài `hush-providers`) |

## Tiếp theo

- [Core Concepts](03-core-concepts.md) — Hiểu sâu các khái niệm cốt lõi
- [LLM Integration](04-llm-integration.md) — Chi tiết về LLM providers
