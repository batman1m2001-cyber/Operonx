# Error Handling

Xử lý lỗi trong workflows: error capture, retry, fallback, và error routing.

> **Ví dụ chạy được**: `examples/10_error_handling.py`

## Error Capture trong State

Khi node lỗi, Hush **không crash** workflow — error được lưu vào `$state`.

```python
with GraphNode(name="error-demo") as graph:
    failing = CodeNode(
        name="failing",
        code_fn=lambda: 1 / 0,  # ZeroDivisionError!
        inputs={},
    )
    START >> failing >> END

engine = Hush(graph)
result = await engine.run(inputs={})

# Workflow không crash — error nằm trong $state
state = result["$state"]
error = state["error-demo.failing", "error", None]
print(f"Error captured: {error is not None}")  # True
```

## Try/Catch Pattern trong CodeNode

Trả về `success`/`error` thay vì throw exception.

```python
from hush.core.nodes.transform.code_node import code_node

@code_node
def safe_divide(a: int, b: int):
    try:
        result = a / b
        return {"success": True, "result": result, "error": None}
    except ZeroDivisionError:
        return {"success": False, "result": None, "error": "Cannot divide by zero"}
```

## Error Routing với if_()

Dùng `if_()` để route success/error theo nhánh khác nhau.

```python
from hush.core.nodes.flow.branch_node import if_

with GraphNode(name="error-routing") as graph:
    divide = safe_divide(
        name="divide",
        inputs={"a": PARENT["a"], "b": PARENT["b"]},
    )
    router = if_(divide["success"] == True, "on_success").else_("on_error")

    on_success = CodeNode(
        name="on_success",
        code_fn=lambda result: {"output": f"Result: {result}"},
        inputs={"result": divide["result"]},
        outputs={"output": PARENT},
    )
    on_error = CodeNode(
        name="on_error",
        code_fn=lambda error: {"output": f"Error: {error}"},
        inputs={"error": divide["error"]},
        outputs={"output": PARENT},
    )

    START >> divide >> router
    router >> [on_success, on_error]
    [on_success, on_error] >> ~END
```

## Retry với Exponential Backoff

```python
@code_node
def retry_with_backoff(query: str):
    import time
    max_attempts = 3
    base_delay = 0.1

    for attempt in range(max_attempts):
        try:
            result = call_api(query)
            return {"success": True, "answer": result, "attempts": attempt + 1}
        except ConnectionError:
            if attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt)
                time.sleep(delay)

    return {"success": False, "answer": "Service unavailable", "attempts": max_attempts}
```

## Graceful Degradation

Kết hợp retry + fallback value.

```python
with GraphNode(name="retry-demo") as graph:
    api_call = retry_with_backoff(
        name="api_call",
        inputs={"query": PARENT["query"]},
    )
    fallback = CodeNode(
        name="fallback",
        code_fn=lambda answer, success: {
            "output": answer if success else "Default answer (fallback)"
        },
        inputs={"answer": api_call["answer"], "success": api_call["success"]},
        outputs={"output": PARENT},
    )
    START >> api_call >> fallback >> END
```

## LLM Fallback Chain

LLMNode hỗ trợ tự động fallback khi model fails.

```python
from hush.providers import LLMNode

llm = LLMNode(
    name="llm",
    resource_key="gpt-4o",
    fallback=["gpt-4o-mini"],  # Nếu gpt-4o fails → thử gpt-4o-mini
    inputs={"messages": prompt["messages"]},
    outputs={"content": PARENT["answer"], "model_used": PARENT["model"]}
)
```

## Best Practices

1. **Try/catch trong CodeNode** — Trả success/error thay vì throw
2. **if_() routing** — Route success/error theo nhánh riêng
3. **Retry với backoff** — Cho external API calls
4. **LLM fallback** — Cấu hình backup models
5. **Soft edges (~END)** — Sau branch khi chỉ 1 nhánh chạy

## Tiếp theo

- [Parallel Execution](08-parallel-execution.md) — Parallel patterns
- [Tracing & Observability](09-tracing-observability.md) — Debug workflows
