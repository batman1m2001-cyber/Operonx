# Parallel Execution

Thực thi song song trong workflows: fan-out/fan-in, MapNode, partial failure.

> **Ví dụ chạy được**: `examples/13_parallel_advanced.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Syntax | Class | Ví dụ |
> |--------|-------|-------|
> | `@code_node` | `CodeNode` | `@code_node` decorator trên function |
> | `MapNode.of()` | `MapNode` | `MapNode.of(x=Each([1,2,3]), max_concurrency=4)` |
> | `LLMNode.of()` | `LLMNode` | `LLMNode.of(resource_key="gpt-4o", messages=PARENT["msgs"])` |
> | `PromptNode.of()` | `PromptNode` | `PromptNode.of(template={...}, var=PARENT["x"])` |

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
    m = merge(
        a=a["result"], b=b["result"], c=c["result"],
        outputs={"analysis": PARENT},  # Explicit output mapping
    )

    START >> [a, b, c]  # Fan-out
    [a, b, c] >> m >> END  # Fan-in (hard edge: chờ tất cả)
```

## MapNode.of() với max_concurrency

Xử lý list items song song với giới hạn concurrency.

```python
from hush.core import MapNode, Each

@code_node
def process(item):
    return {"result": item * 2}

with GraphNode(name="parallel-map") as graph:
    with MapNode.of(
        item=Each(PARENT["items"]),
        max_concurrency=3,  # Tối đa 3 tasks cùng lúc
    ) as map_node:
        step = process(
            name="process",
            inputs={"item": PARENT["item"]},
            outputs={"*": PARENT},
        )
        START >> step >> END

    map_node["result"] >> PARENT["results"]  # Map loop output → graph output
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
def summarize(results, errors):
    return {
        "successful": [r for r, e in zip(results, errors) if e is None],
        "failed": [e for e in errors if e is not None],
    }

with GraphNode(name="partial-failure") as graph:
    with MapNode.of(item=Each(PARENT["items"])) as map_node:
        proc = safe_process(
            item=PARENT["item"],
            outputs={"result": PARENT, "error": PARENT},
        )
        START >> proc >> END

    s = summarize(
        results=map_node["result"],
        errors=map_node["error"],
        outputs={"*": PARENT},
    )
    START >> map_node >> s >> END
```

## Parallel LLM Calls

Gọi nhiều LLMs song song (ví dụ: so sánh models).

```python
from hush.providers import PromptNode, LLMNode

@code_node
def merge_results(s, k):
    return {"summary": s, "keywords": k}

with GraphNode(name="parallel-llm") as graph:
    p_summary = PromptNode.of(
        template={"system": "Summarize in one sentence.", "user": "{text}"},
        text=PARENT["text"],
    )
    p_keywords = PromptNode.of(
        template={"system": "List 3 keywords, comma-separated.", "user": "{text}"},
        text=PARENT["text"],
    )

    llm_summary = LLMNode.of(resource_key="gpt-4o-mini", messages=p_summary["messages"])
    llm_keywords = LLMNode.of(resource_key="gpt-4o-mini", messages=p_keywords["messages"])

    m = merge_results(
        s=llm_summary["content"],
        k=llm_keywords["content"],
        outputs={"*": PARENT},  # Forward all outputs
    )

    START >> [p_summary, p_keywords]
    p_summary >> llm_summary
    p_keywords >> llm_keywords
    [llm_summary, llm_keywords] >> m >> END
```

### Batch LLM via MapNode

Gọi nhiều queries song song qua MapNode:

```python
with GraphNode(name="batch-llm") as graph:
    with MapNode.of(
        query=Each(PARENT["queries"]),
        max_concurrency=3,
    ) as map_node:
        p = PromptNode.of(
            template={"system": "Answer in one sentence.", "user": "{query}"},
            query=PARENT["query"],
        )
        llm = LLMNode.of(
            resource_key="gpt-4o-mini",
            messages=p["messages"],
            outputs={"content": PARENT["answer"]},
        )
        START >> p >> llm >> END

    map_node["answer"] >> PARENT["answers"]  # Collect all answers
    START >> map_node >> END
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
