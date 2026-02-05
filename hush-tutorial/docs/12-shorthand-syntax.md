# Shorthand Syntax

Hush cung cấp các hàm shorthand để viết workflow ngắn gọn hơn. Thay vì dùng class đầy đủ với `inputs={}`, bạn có thể truyền trực tiếp các tham số.

> **Ví dụ chạy được**: `examples/15_shorthand_syntax.py`

## Tổng quan

| Full Class | Shorthand | Mô tả |
|------------|-----------|-------|
| `CodeNode(...)` | `@code_node` | Decorator tạo CodeNode từ function |
| `ForLoopNode(inputs={...})` | `for_(item=Each(...), ...)` | Iterate tuần tự |
| `MapNode(inputs={...})` | `map_(item=Each(...), ...)` | Iterate song song |
| `WhileLoopNode(inputs={...})` | `while_(counter=0, stop_condition=...)` | Loop với điều kiện |
| `AsyncIterNode(inputs={...})` | `aiter_(chunk=Each(stream), ...)` | Xử lý async streaming |
| `BranchNode(...)` | `if_(...).else_(...)` | Routing có điều kiện |
| `LLMNode(inputs={...})` | `llm_("gpt-4o", messages=...)` | Gọi LLM |

## @code_node — Decorator

Biến Python function thành CodeNode.

### So sánh

```python
# ❌ Verbose: dùng CodeNode class
def clean_text(text: str) -> dict:
    return {"cleaned": text.strip().lower()}

node = CodeNode(
    name="clean",
    code_fn=clean_text,
    inputs={"text": PARENT["text"]},
    outputs={"cleaned": PARENT}
)

# ✅ Shorthand: dùng @code_node decorator
from hush.core.nodes import code_node

@code_node
def clean_text(text: str):
    return {"cleaned": text.strip().lower()}

node = clean_text(
    name="clean",
    text=PARENT["text"],           # Input trực tiếp
    outputs={"cleaned": PARENT}
)
```

### Outputs shorthand

```python
@code_node
def process(x: int):
    return {"result": x * 2, "status": "ok"}

# outputs={"*": PARENT} ghi tất cả outputs lên parent
node = process(name="proc", x=PARENT["value"], outputs={"*": PARENT})
```

## for_() — ForLoopNode Shorthand

Iterate tuần tự qua collection.

### So sánh

```python
from hush.core.nodes import ForLoopNode, Each, for_

# ❌ Verbose
with ForLoopNode(
    name="loop",
    inputs={
        "item": Each(PARENT["items"]),
        "prefix": PARENT["prefix"]
    }
) as loop:
    ...

# ✅ Shorthand
with for_(
    name="loop",
    item=Each(PARENT["items"]),   # Each() = iterate
    prefix=PARENT["prefix"]        # Không có Each() = broadcast
) as loop:
    ...
```

### Ví dụ đầy đủ

```python
from hush.core.nodes import for_, Each, code_node

@code_node
def double(x: int):
    return {"result": x * 2}

with GraphNode(name="demo") as graph:
    with for_(item=Each([1, 2, 3, 4, 5])) as loop:
        step = double(name="double", x=PARENT["item"], outputs={"*": PARENT})
        START >> step >> END

    loop["result"] >> PARENT["results"]
    START >> loop >> END

# result["results"] = [2, 4, 6, 8, 10]
```

## map_() — MapNode Shorthand

Iterate song song với giới hạn concurrency.

### So sánh

```python
from hush.core.nodes import MapNode, Each, map_

# ❌ Verbose
with MapNode(
    name="parallel",
    inputs={"url": Each(PARENT["urls"]), "timeout": 30},
    max_concurrency=10
) as map_node:
    ...

# ✅ Shorthand
with map_(
    name="parallel",
    url=Each(PARENT["urls"]),
    timeout=30,                    # Broadcast
    max_concurrency=10             # Config option
) as map_node:
    ...
```

### Ví dụ đầy đủ

```python
@code_node
def square(x: int):
    return {"squared": x * x}

with GraphNode(name="parallel-demo") as graph:
    with map_(x=Each([1, 2, 3, 4, 5]), max_concurrency=3) as loop:
        step = square(name="square", x=PARENT["x"], outputs={"*": PARENT})
        START >> step >> END

    loop["squared"] >> PARENT["results"]
    START >> loop >> END

# result["results"] = [1, 4, 9, 16, 25]
```

## while_() — WhileLoopNode Shorthand

Loop cho đến khi điều kiện dừng.

### So sánh

```python
from hush.core.nodes import WhileLoopNode, while_

# ❌ Verbose
with WhileLoopNode(
    name="countdown",
    inputs={"count": PARENT["start"]},
    stop_condition="count <= 0",
    max_iterations=100
) as loop:
    ...

# ✅ Shorthand
with while_(
    name="countdown",
    count=PARENT["start"],          # Input variable
    stop_condition="count <= 0",    # Điều kiện dừng (string expression)
    max_iterations=100
) as loop:
    ...
```

### Ví dụ đầy đủ

```python
@code_node
def halve(value: int):
    return {"new_value": value // 2}

with GraphNode(name="halve-demo") as graph:
    with while_(value=256, stop_condition="value < 10", max_iterations=20) as loop:
        step = halve(name="halve", value=PARENT["value"])
        step["new_value"] >> PARENT["value"]
        START >> step >> END

    loop["value"] >> PARENT["final"]
    START >> loop >> END

# 256 → 128 → 64 → 32 → 16 → 8 (dừng vì < 10)
```

## aiter_() — AsyncIterNode Shorthand

Xử lý async streaming data với concurrent processing.

### So sánh

```python
from hush.core.nodes import AsyncIterNode, Each, aiter_

# ❌ Verbose
with AsyncIterNode(
    name="stream_processor",
    inputs={"chunk": Each(async_stream)},
    callback=handle_result,
    max_concurrency=5
) as stream:
    ...

# ✅ Shorthand
with aiter_(
    name="stream_processor",
    chunk=Each(async_stream),
    callback=handle_result,
    max_concurrency=5
) as stream:
    ...
```

### Ví dụ với LLM streaming

```python
async def process_llm_stream():
    with GraphNode(name="stream-demo") as graph:
        with aiter_(
            chunk=Each(llm_stream),
            callback=lambda r: print(r["text"], end=""),
            max_concurrency=1
        ) as stream:
            process = code_node(...)
            START >> process >> END

        START >> stream >> END
```

## if_() — BranchNode Shorthand

Routing có điều kiện với fluent syntax.

### So sánh

```python
from hush.core.nodes import BranchNode, Branch, if_

# ❌ Verbose
router = BranchNode(
    name="router",
    cases=[
        Branch(condition=PARENT["score"] >= 90, target="excellent"),
        Branch(condition=PARENT["score"] >= 70, target="good"),
        Branch(condition=True, target="fail")  # default
    ]
)

# ✅ Shorthand (fluent chaining)
router = (if_(PARENT["score"] >= 90, "excellent")
          .if_(PARENT["score"] >= 70, "good")
          .else_("fail"))
```

### Ví dụ đầy đủ

```python
with GraphNode(name="grade-workflow") as graph:
    # Fluent syntax tự động lấy tên từ biến (grade_router)
    grade_router = (if_(PARENT["score"] >= 90, "excellent")
                    .if_(PARENT["score"] >= 70, "good")
                    .if_(PARENT["score"] >= 50, "average")
                    .else_("fail"))

    excellent = CodeNode(name="excellent", code_fn=lambda: {"grade": "A"}, outputs={"grade": PARENT})
    good = CodeNode(name="good", code_fn=lambda: {"grade": "B"}, outputs={"grade": PARENT})
    average = CodeNode(name="average", code_fn=lambda: {"grade": "C"}, outputs={"grade": PARENT})
    fail = CodeNode(name="fail", code_fn=lambda: {"grade": "F"}, outputs={"grade": PARENT})

    START >> grade_router >> [excellent, good, average, fail]
    [excellent, good, average, fail] >> ~END  # Soft edge vì chỉ 1 nhánh chạy
```

## llm_() — LLMNode Shorthand

Gọi LLM với syntax ngắn gọn.

### So sánh

```python
from hush.providers import LLMNode, llm_

# ❌ Verbose
llm = LLMNode(
    name="chat",
    resource_key="gpt-4o",
    inputs={
        "messages": PARENT["messages"],
        "temperature": 0.7,
        "max_tokens": 1000
    },
    outputs={"content": PARENT["response"]}
)

# ✅ Shorthand
llm = llm_(
    "gpt-4o",
    name="chat",
    messages=PARENT["messages"],   # Input trực tiếp
    temperature=0.7,
    max_tokens=1000,
    outputs={"content": PARENT["response"]}
)
```

### Load Balancing

```python
# Multiple models với weight ratios
llm = llm_(
    ["gpt-4o", "gpt-4o-mini"],
    ratios=[0.3, 0.7],
    name="balanced",
    messages=PARENT["messages"],
    seed=42  # Reproducible selection
)
```

### Fallback

```python
# Tự động fallback khi primary fails
llm = llm_(
    "gpt-4o",
    fallback=["azure-gpt4", "gemini"],
    name="resilient",
    messages=PARENT["messages"]
)
```

### Batch Mode

```python
# OpenAI Batch API (50% cheaper)
llm = llm_(
    "gpt-4o",
    batch_mode=True,
    name="batch_llm",
    messages=PARENT["messages"]
)
```

## Best Practices

### 1. Khi nào dùng Shorthand

```python
# ✅ Dùng shorthand cho cases đơn giản
with for_(item=Each(items), multiplier=10) as loop:
    ...

# ✅ Dùng class đầy đủ khi cần nhiều config
with ForLoopNode(
    name="complex_loop",
    inputs={
        "item": Each(PARENT["items"]),
        "context": PARENT["context"],
        "settings": PARENT["settings"]
    },
    outputs={
        "results": PARENT["processed"],
        "errors": PARENT["failed"]
    }
) as loop:
    ...
```

### 2. Mix shorthand và verbose

```python
# OK: mix trong cùng workflow
with GraphNode(name="mixed") as graph:
    # Shorthand cho simple nodes
    with for_(item=Each(PARENT["items"])) as loop:
        step = process(name="step", x=PARENT["item"], outputs={"*": PARENT})
        START >> step >> END

    # Verbose cho complex nodes
    final = CodeNode(
        name="aggregate",
        code_fn=aggregate_results,
        inputs={
            "results": loop["result"],
            "config": PARENT["config"],
            "metadata": PARENT["metadata"]
        },
        outputs={
            "summary": PARENT["summary"],
            "stats": PARENT["stats"]
        }
    )

    START >> loop >> final >> END
```

### 3. Auto-naming

Shorthand functions sử dụng variable name làm node name khi không chỉ định:

```python
# Tên node sẽ là "grade_router" (từ variable name)
grade_router = if_(PARENT["score"] >= 90, "a").else_("b")

# Explicit name khi cần
router = if_(PARENT["score"] >= 90, "a", name="my_router").else_("b")
```

## Tổng kết

| Shorthand | Config Options | Khi nào dùng |
|-----------|----------------|--------------|
| `@code_node` | - | Tạo node từ function |
| `for_(...)` | - | Sequential iteration |
| `map_(...)` | `max_concurrency` | Parallel iteration |
| `while_(...)` | `stop_condition`, `max_iterations` | Conditional loop |
| `aiter_(...)` | `max_concurrency`, `callback`, `batch_fn` | Async streaming |
| `if_(...).else_(...)` | - | Conditional routing |
| `llm_(...)` | `ratios`, `fallback`, `batch_mode`, `seed` | LLM calls |

## Tiếp theo

- [Core Concepts](03-core-concepts.md) — Hiểu inputs/outputs mapping
- [Loops & Branches](05-loops-branches.md) — Chi tiết về flow control
- [LLM Integration](04-llm-integration.md) — Chi tiết về LLMNode
