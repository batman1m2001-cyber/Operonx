# Parallel Execution

Thực thi song song trong workflows: fan-out/fan-in, MapNode, partial failure.

> **Ví dụ chạy được**: `examples/13_parallel_advanced.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Viết tắt | Class gốc | Ví dụ |
> |----------|-----------|-------|
> | `@code_node` | `CodeNode` | `@code_node` decorator trên function |
> | `map_()` | `MapNode` | `map_(x=Each([1,2,3]), max_concurrency=4)` |
> | `llm_()` | `LLMNode` | `llm_(resource_key="gpt-4o", messages=PARENT["msgs"])` |
> | `prompt_()` | `PromptNode` | `prompt_(template={...}, var=PARENT["x"])` |

## Fan-out / Fan-in

Chạy nhiều nodes song song, rồi merge kết quả.

```python
@code_node
def task_a():
    return {"result": "A"}

@code_node
def task_b():
    return {"result": "B"}

@code_node
def task_c():
    return {"result": "C"}

@code_node
def merge(a, b, c):
    return {"combined": f"{a}+{b}+{c}"}

with GraphNode(name="fan-out") as graph:
    a = task_a()
    b = task_b()
    c = task_c()
    m = merge(a=a["result"], b=b["result"], c=c["result"])

    START >> [a, b, c]  # Fan-out
    [a, b, c] >> m >> END  # Fan-in (hard edge: chờ tất cả)
```

## map_() với max_concurrency

Xử lý list items song song với giới hạn concurrency.

```python
from hush.core.nodes import map_, Each

@code_node
def process(item):
    return {"result": item * 2}

with GraphNode(name="parallel-map") as graph:
    with map_(
        item=Each(PARENT["items"]),
        max_concurrency=5,  # Tối đa 5 tasks cùng lúc
    ) as map_node:
        step = process(item=PARENT["item"])
        START >> step >> END

    START >> map_node >> END
```

## Partial Failure Handling

Xử lý trường hợp một số items fail trong MapNode.

```python
@code_node
def safe_process(item: dict):
    try:
        result = process_item(item)
        return {"result": result, "error": None}
    except Exception as e:
        return {"result": None, "error": str(e)}

@code_node
def summarize(results):
    return {
        "succeeded": [r for r in results if r.get("error") is None],
        "failed": [r for r in results if r.get("error") is not None],
    }

with GraphNode(name="partial-failure") as graph:
    with map_(
        item=Each(PARENT["items"]),
        max_concurrency=3,
    ) as map_node:
        proc = safe_process(item=PARENT["item"])
        START >> proc >> END

    s = summarize(results=map_node["results"])
    START >> map_node >> s >> END
```

## Parallel LLM Calls

Gọi nhiều LLMs song song (ví dụ: so sánh models).

```python
from hush.providers import prompt_, llm_

with GraphNode(name="parallel-llm") as graph:
    p = prompt_(
        template={"system": "Answer briefly.", "user": "{query}"},
        query=PARENT["query"],
    )
    a = llm_(resource_key="gpt-4o", messages=p["messages"])
    b = llm_(resource_key="gpt-4o-mini", messages=p["messages"])

    START >> p >> [a, b]  # Song song
    [a, b] >> END         # Chờ cả hai
```

Xem thêm parallel LLM comparison tại `examples/12_multi_model.py`.

## Best Practices

1. **Fan-out cho independent tasks** — Dùng `START >> [a, b, c]`
2. **MapNode cho list processing** — Với `max_concurrency` để rate limit
3. **Try/catch trong MapNode** — Xử lý partial failure
4. **Hard edge cho fan-in** — `[a, b, c] >> merge` chờ tất cả
5. **Soft edge sau branch** — `[path_a, path_b] >> ~c` khi chỉ 1 nhánh chạy

## Tiếp theo

- [Tracing & Observability](09-tracing-observability.md) — Debug parallel workflows
- [Error Handling](07-error-handling.md) — Error patterns
