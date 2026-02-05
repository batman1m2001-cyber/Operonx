# Parallel Execution

Thực thi song song trong workflows: fan-out/fan-in, MapNode, partial failure.

> **Ví dụ chạy được**: `examples/13_parallel_advanced.py`

## Fan-out / Fan-in

Chạy nhiều nodes song song, rồi merge kết quả.

```python
with GraphNode(name="fan-out") as graph:
    # Fan-out: 3 nodes chạy song song
    task_a = CodeNode(name="a", code_fn=lambda: {"result": "A"}, outputs={"result": PARENT["a"]})
    task_b = CodeNode(name="b", code_fn=lambda: {"result": "B"}, outputs={"result": PARENT["b"]})
    task_c = CodeNode(name="c", code_fn=lambda: {"result": "C"}, outputs={"result": PARENT["c"]})

    # Fan-in: merge khi tất cả xong
    merge = CodeNode(
        name="merge",
        code_fn=lambda a, b, c: {"combined": f"{a}+{b}+{c}"},
        inputs={"a": PARENT["a"], "b": PARENT["b"], "c": PARENT["c"]},
        outputs={"combined": PARENT}
    )

    START >> [task_a, task_b, task_c]  # Fan-out
    [task_a, task_b, task_c] >> merge >> END  # Fan-in (hard edge: chờ tất cả)
```

## MapNode với max_concurrency

Xử lý list items song song với giới hạn concurrency.

```python
from hush.core.nodes.iteration import MapNode
from hush.core.nodes.iteration.base import Each

with GraphNode(name="parallel-map") as graph:
    with MapNode(
        name="process_all",
        inputs={"item": Each(PARENT["items"])},
        max_concurrency=5,  # Tối đa 5 tasks cùng lúc
        outputs={"results": PARENT}
    ) as map_node:
        process = CodeNode(
            name="process",
            code_fn=lambda item: {"result": item * 2},
            inputs={"item": PARENT["item"]},
            outputs={"result": PARENT}
        )
        START >> process >> END

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

with GraphNode(name="partial-failure") as graph:
    with MapNode(
        name="safe_map",
        inputs={"item": Each(PARENT["items"])},
        max_concurrency=3,
        outputs={"results": PARENT}
    ) as map_node:
        proc = safe_process(
            name="process",
            inputs={"item": PARENT["item"]},
            outputs={"result": PARENT, "error": PARENT}
        )
        START >> proc >> END

    # Tách kết quả thành công/thất bại
    summarize = CodeNode(
        name="summarize",
        code_fn=lambda results: {
            "succeeded": [r for r in results if r.get("error") is None],
            "failed": [r for r in results if r.get("error") is not None],
        },
        inputs={"results": PARENT["results"]},
        outputs={"succeeded": PARENT, "failed": PARENT}
    )

    START >> map_node >> summarize >> END
```

## Parallel LLM Calls

Gọi nhiều LLMs song song (ví dụ: so sánh models).

```python
from hush.providers import PromptNode, LLMNode

with GraphNode(name="parallel-llm") as graph:
    prompt = PromptNode(
        name="prompt",
        inputs={
            "prompt": {"system": "Answer briefly.", "user": "{query}"},
            "query": PARENT["query"]
        }
    )
    llm_a = LLMNode(
        name="llm_a",
        resource_key="gpt-4o",
        inputs={"messages": prompt["messages"]},
        outputs={"content": PARENT["answer_a"]}
    )
    llm_b = LLMNode(
        name="llm_b",
        resource_key="gpt-4o-mini",
        inputs={"messages": prompt["messages"]},
        outputs={"content": PARENT["answer_b"]}
    )

    START >> prompt >> [llm_a, llm_b]  # Song song
    [llm_a, llm_b] >> END              # Chờ cả hai
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
