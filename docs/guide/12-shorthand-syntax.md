# Shorthand Syntax

Hush cung cấp các `.of()` classmethod và decorator để viết workflow ngắn gọn hơn. Thay vì dùng class đầy đủ với `inputs={}`, bạn có thể truyền trực tiếp các tham số.

> **Ví dụ chạy được**: `examples/15_shorthand_syntax.py`, `examples/16_graph.py`

## Tổng quan

| Full Class | Shorthand | Mô tả |
|------------|-----------|-------|
| `FuncOp(...)` | `@op` | Decorator tạo FuncOp từ function |
| `GraphOp(...)` + manual setup | `@graph` | Decorator tạo reusable workflow module |
| `ForOp(inputs={...})` | `ForOp.of(item=Each(...), ...)` | Iterate tuần tự |
| `MapOp(inputs={...})` | `MapOp.of(item=Each(...), ...)` | Iterate song song |
| `WhileOp(inputs={...})` | `WhileOp.of(counter=0, until=...)` | Loop với điều kiện |
| `AIterOp(inputs={...})` | `AIterOp.of(chunk=Each(stream), ...)` | Xử lý async streaming |
| `BranchOp(...)` | `if_(...).else_(...)` | Routing có điều kiện |
| *(verbose graph)* | `chain(resource="gpt-4o", template=..., ...)` | Prompt + LLM all-in-one |
| `LLMOp(inputs={...})` | `LLMOp.of(resource="gpt-4o", messages=...)` | Gọi LLM |
| `PromptOp(inputs={...})` | `PromptOp.of(template=..., ...)` | Tạo messages từ template |
| `EmbeddingOp(inputs={...})` | `EmbeddingOp.of(resource="model", texts=...)` | Tạo embeddings |
| `RerankOp(inputs={...})` | `RerankOp.of(resource="model", query=..., documents=...)` | Rerank documents |

## @op — Decorator

Biến Python function thành FuncOp.

### So sánh

```python
# ❌ Verbose: dùng FuncOp class
def clean_text(text: str) -> dict:
    return {"cleaned": text.strip().lower()}

step = FuncOp(
    name="clean",
    code_fn=clean_text,
    inputs={"text": PARENT["text"]},
    outputs={"cleaned": PARENT}
)

# ✅ Shorthand: dùng @op decorator
from hush.core.ops import op

@op
def clean_text(text: str):
    return {"cleaned": text.strip().lower()}

step = clean_text(
    name="clean",
    text=PARENT["text"],           # Input trực tiếp
    outputs={"cleaned": PARENT}
)
```

### Outputs shorthand

```python
@op
def process(x: int):
    return {"result": x * 2, "status": "ok"}

# outputs={"*": PARENT} ghi tất cả outputs lên parent
step = process(name="proc", x=PARENT["value"], outputs={"*": PARENT})
```

## ForOp.of() — ForOp Classmethod

Iterate tuần tự qua collection.

### So sánh

```python
from hush.core import ForOp, Each

# ❌ Verbose
with ForOp(
    name="loop",
    inputs={
        "item": Each(PARENT["items"]),
        "prefix": PARENT["prefix"]
    }
) as loop:
    ...

# ✅ Classmethod
with ForOp.of(
    name="loop",
    item=Each(PARENT["items"]),   # Each() = iterate
    prefix=PARENT["prefix"]        # Không có Each() = broadcast
) as loop:
    ...
```

### Ví dụ đầy đủ

```python
from hush.core import ForOp, Each, op

@op
def double(x: int):
    return {"result": x * 2}

with GraphOp(name="demo") as graph:
    with ForOp.of(item=Each([1, 2, 3, 4, 5])) as loop:
        step = double(name="double", x=PARENT["item"], outputs={"*": PARENT})
        START >> step >> END

    loop["result"] >> PARENT["results"]
    START >> loop >> END

# result["results"] = [2, 4, 6, 8, 10]
```

## MapOp.of() — MapOp Classmethod

Iterate song song với giới hạn concurrency.

### So sánh

```python
from hush.core import MapOp, Each

# ❌ Verbose
with MapOp(
    name="parallel",
    inputs={"url": Each(PARENT["urls"]), "timeout": 30},
    max_concurrency=10
) as map_op:
    ...

# ✅ Classmethod
with MapOp.of(
    name="parallel",
    url=Each(PARENT["urls"]),
    timeout=30,                    # Broadcast
    max_concurrency=10             # Config option
) as map_op:
    ...
```

### Ví dụ đầy đủ

```python
@op
def square(x: int):
    return {"squared": x * x}

with GraphOp(name="parallel-demo") as graph:
    with MapOp.of(x=Each([1, 2, 3, 4, 5]), max_concurrency=3) as loop:
        step = square(name="square", x=PARENT["x"], outputs={"*": PARENT})
        START >> step >> END

    loop["squared"] >> PARENT["results"]
    START >> loop >> END

# result["results"] = [1, 4, 9, 16, 25]
```

## WhileOp.of() — WhileOp Classmethod

Loop cho đến khi điều kiện dừng.

### So sánh

```python
from hush.core import WhileOp

# ❌ Verbose
with WhileOp(
    name="countdown",
    inputs={"count": PARENT["start"]},
    until="count <= 0",
    max_iterations=100
) as loop:
    ...

# ✅ Classmethod
with WhileOp.of(
    name="countdown",
    count=PARENT["start"],          # Input variable
    until="count <= 0",    # Điều kiện dừng (string expression)
    max_iterations=100
) as loop:
    ...
```

### Ví dụ đầy đủ

```python
@op
def halve(value: int):
    return {"new_value": value // 2}

with GraphOp(name="halve-demo") as graph:
    with WhileOp.of(value=256, until="value < 10", max_iterations=20) as loop:
        step = halve(name="halve", value=PARENT["value"])
        step["new_value"] >> PARENT["value"]
        START >> step >> END

    loop["value"] >> PARENT["final"]
    START >> loop >> END

# 256 → 128 → 64 → 32 → 16 → 8 (dừng vì < 10)
```

## AIterOp.of() — AIterOp Classmethod

Xử lý async streaming data với concurrent processing.

### So sánh

```python
from hush.core import AIterOp, Each

# ❌ Verbose
with AIterOp(
    name="stream_processor",
    inputs={"chunk": Each(async_stream)},
    callback=handle_result,
    max_concurrency=5
) as stream:
    ...

# ✅ Classmethod
with AIterOp.of(
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
    with GraphOp(name="stream-demo") as graph:
        with AIterOp.of(
            chunk=Each(llm_stream),
            callback=lambda r: print(r["text"], end=""),
            max_concurrency=1
        ) as stream:
            process = op(...)
            START >> process >> END

        START >> stream >> END
```

## if_() — BranchOp Shorthand

Routing có điều kiện với fluent syntax.

### So sánh

```python
from hush.core.ops import BranchOp, Branch, if_

# ❌ Verbose
router = BranchOp(
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
with GraphOp(name="grade-workflow") as graph:
    # Fluent syntax tự động lấy tên từ biến (grade_router)
    grade_router = (if_(PARENT["score"] >= 90, "excellent")
                    .if_(PARENT["score"] >= 70, "good")
                    .if_(PARENT["score"] >= 50, "average")
                    .else_("fail"))

    excellent = FuncOp(name="excellent", code_fn=lambda: {"grade": "A"}, outputs={"grade": PARENT})
    good = FuncOp(name="good", code_fn=lambda: {"grade": "B"}, outputs={"grade": PARENT})
    average = FuncOp(name="average", code_fn=lambda: {"grade": "C"}, outputs={"grade": PARENT})
    fail = FuncOp(name="fail", code_fn=lambda: {"grade": "F"}, outputs={"grade": PARENT})

    START >> grade_router >> [excellent, good, average, fail]
    [excellent, good, average, fail] >> ~END  # Soft edge vì chỉ 1 nhánh chạy
```

## LLMOp.of() — LLMOp Classmethod

Gọi LLM với syntax ngắn gọn.

### So sánh

```python
from hush.providers import LLMOp

# ❌ Verbose
llm = LLMOp(
    name="chat",
    resource="gpt-4o",
    inputs={
        "messages": PARENT["messages"],
        "temperature": 0.7,
        "max_tokens": 1000
    },
    outputs={"content": PARENT["response"]}
)

# ✅ Classmethod
llm = LLMOp.of(
    resource="gpt-4o",
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
llm = LLMOp.of(
    resource=["gpt-4o", "gpt-4o-mini"],
    ratios=[0.3, 0.7],
    name="balanced",
    messages=PARENT["messages"],
    seed=42  # Reproducible selection
)
```

### Fallback

```python
# Tự động fallback khi primary fails
llm = LLMOp.of(
    resource="gpt-4o",
    fallback=["azure-gpt4", "gemini"],
    name="resilient",
    messages=PARENT["messages"]
)
```

### Batch Mode

```python
# OpenAI Batch API (50% cheaper)
llm = LLMOp.of(
    resource="gpt-4o",
    batch_mode=True,
    name="batch_llm",
    messages=PARENT["messages"]
)
```

## chain() — Factory Function

Prompt + LLM all-in-one. Ngắn nhất có thể. `chain()` là một factory function (không phải class), trả về `GraphOp`.

### Cách dùng

```python
from hush.providers import chain

# ✅ Factory function (auto-name + >> END auto-forward)
chat = chain(
    resource="gpt-4o",
    template={"system": "Bạn là assistant.", "user": "{query}"},
    query=PARENT["query"],
)
START >> chat >> END  # result["content"], result["model_used"], ...
```

### String template

```python
summarize = chain(resource="gpt-4o", template="Tóm tắt: {text}", text=PARENT["text"])
```

### Structured output

```python
classifier = chain(
    resource="gpt-4o",
    template={"user": "Phân loại: {text}"},
    text=PARENT["text"],
    response_format={"type": "json_object"},
)
```

### Load Balancing + Fallback

```python
chat = chain(
    resource=["gpt-4o", "gpt-4o-mini"],
    template={"system": "Help.", "user": "{query}"},
    ratios=[0.7, 0.3],
    fallback=["or-claude-4-sonnet"],
    query=PARENT["query"],
)
```

## PromptOp.of() — PromptOp Classmethod

Tạo messages từ template, dùng khi cần tách riêng prompt và LLM.

```python
from hush.providers import PromptOp

# String → [{"role": "user", "content": "..."}]
p = PromptOp.of(template="Tóm tắt: {text}", text=PARENT["text"])

# Dict → system + user messages
p = PromptOp.of(
    template={"system": "Bạn là assistant chuyên {task}.", "user": "{query}"},
    task="tóm tắt",
    query=PARENT["query"],
)
```

## EmbeddingOp.of() — EmbeddingOp Classmethod

Tạo embeddings từ text.

```python
from hush.providers import EmbeddingOp

embed = EmbeddingOp.of(resource="bge-m3", texts=PARENT["texts"])
START >> embed >> END  # result["embeddings"]
```

## RerankOp.of() — RerankOp Classmethod

Rerank documents theo query.

```python
from hush.providers import RerankOp

rerank = RerankOp.of(resource="bge-m3", query=PARENT["query"], documents=PARENT["docs"], top_k=5)
START >> rerank >> END  # result["reranked_documents"]
```

## @graph — Modular Workflow

`@graph` biến builder function thành factory tạo `GraphOp` tái sử dụng. Tham số function tự động trở thành `PARENT` refs.

### So sánh

```python
from hush.core import graph, op, GraphOp, START, END, PARENT

@op
def double(x: int):
    return {"result": x * 2}

# ❌ Verbose: tạo GraphOp thủ công
with GraphOp(name="main") as main:
    with GraphOp(name="double_flow", inputs={"val": PARENT["input"]}) as sub:
        step = double(x=PARENT["val"])
        START >> step >> END
    START >> sub >> END

# ✅ Shorthand: @graph decorator
@graph
def double_flow(val):
    step = double(x=val)        # val = PARENT["val"] (injected)
    START >> step >> END

with GraphOp(name="main") as main:
    d = double_flow(val=PARENT["input"])  # auto-named "d"
    START >> d >> END
```

### Tái sử dụng — Chuỗi graphs

```python
with GraphOp(name="chain") as main:
    d1 = double_flow(val=PARENT["input"])
    d2 = double_flow(val=d1["result"])      # chain output
    START >> d1 >> d2 >> END

# input=3 → 3*2=6 → 6*2=12
```

### Output renaming

```python
@graph
def double_flow(val):
    step = double(x=val)
    step["result"] >> PARENT["doubled"]     # rename output key
    START >> step >> END

with GraphOp(name="main") as main:
    d = double_flow(val=PARENT["input"])
    d["doubled"] >> PARENT["answer"]        # map to graph output
    START >> d >> END

# result["answer"] == 14 (input=7)
```

### Zero-param graph

```python
@graph
def static_flow():
    step = double(x=PARENT["val"])          # manual PARENT reference
    START >> step >> END

g = static_flow(val=10)                     # val là input mapping
```

### Explicit name và config

```python
d = double_flow(val=PARENT["input"], name="custom_name")
d = double_flow(val=PARENT["input"], outputs={"result": PARENT["answer"]})
```

## Best Practices

### 1. Khi nào dùng .of() classmethod

```python
# ✅ Dùng .of() classmethod cho cases đơn giản
with ForOp.of(item=Each(items), multiplier=10) as loop:
    ...

# ✅ Dùng class đầy đủ khi cần nhiều config
with ForOp(
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

### 2. Mix .of() classmethod và verbose

```python
# OK: mix trong cùng workflow
with GraphOp(name="mixed") as graph:
    # Classmethod cho simple ops
    with ForOp.of(item=Each(PARENT["items"])) as loop:
        step = process(name="step", x=PARENT["item"], outputs={"*": PARENT})
        START >> step >> END

    # Verbose cho complex ops
    final = FuncOp(
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

`.of()` classmethods sử dụng variable name làm op name khi không chỉ định:

```python
# Tên op sẽ là "grade_router" (từ variable name)
grade_router = if_(PARENT["score"] >= 90, "a").else_("b")

# Explicit name khi cần
router = if_(PARENT["score"] >= 90, "a", name="my_router").else_("b")
```

### 4. Auto-forward outputs với >> END

Khi op kết nối trực tiếp đến END mà không định nghĩa `outputs`, tất cả outputs sẽ tự động forward lên parent:

```python
# ❌ Verbose: phải viết outputs
step = FuncOp(
    name="compute",
    code_fn=lambda: {"a": 1, "b": 2},
    outputs={"a": PARENT, "b": PARENT}
)
START >> step >> END

# ✅ Shorthand: auto-forward tất cả outputs
step = FuncOp(
    name="compute",
    code_fn=lambda: {"a": 1, "b": 2}
)
START >> step >> END  # result["a"] == 1, result["b"] == 2

# Với @op decorator
@op
def compute():
    return {"a": 1, "b": 2}

step = compute(name="step")  # Không cần outputs
START >> step >> END         # Auto-forward
```

## Tổng kết

| Shorthand | Config Options | Khi nào dùng |
|-----------|----------------|--------------|
| `@op` | - | Tạo op từ function |
| `@graph` | `name`, `outputs`, `description` | Tạo reusable workflow module |
| `ForOp.of(...)` | - | Sequential iteration |
| `MapOp.of(...)` | `max_concurrency` | Parallel iteration |
| `WhileOp.of(...)` | `until`, `max_iterations` | Conditional loop |
| `AIterOp.of(...)` | `max_concurrency`, `callback`, `batch_fn` | Async streaming |
| `if_(...).else_(...)` | - | Conditional routing |
| `chain(...)` | `ratios`, `fallback`, `response_format`, `extract` | Prompt + LLM all-in-one |
| `LLMOp.of(...)` | `ratios`, `fallback`, `batch_mode`, `seed` | LLM calls |
| `PromptOp.of(...)` | - | Tạo messages từ template |
| `EmbeddingOp.of(...)` | - | Tạo embeddings |
| `RerankOp.of(...)` | - | Rerank documents |

## Tiếp theo

- [Core Concepts](03-core-concepts.md) — Hiểu inputs/outputs mapping
- [Loops & Branches](05-loops-branches.md) — Chi tiết về flow control
- [LLM Integration](04-llm-integration.md) — Chi tiết về LLMOp
