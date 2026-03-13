# Shorthand Syntax

Hush cung cấp các `.of()` classmethod, decorator, và factory functions để viết workflow ngắn gọn hơn. Thay vì dùng class đầy đủ với `inputs={}`, bạn có thể truyền trực tiếp các tham số.

> **Ví dụ chạy được**: `examples/05_loops_and_branches/demo.py`, `examples/09_agent_workflow/demo.py`

## Tổng quan

| Full Class | Shorthand | Mô tả |
|------------|-----------|-------|
| `FuncOp(...)` | `@op` | Decorator tạo FuncOp từ function |
| `GraphOp(...)` + manual setup | `@graph` | Decorator tạo reusable workflow module |
| *(yield trong @op)* | Generator op | `yield {"item": item}` — iterate + parallel |
| *(GraphOp with loop)* | `@graph.loop()` | `@graph.loop(until="done == True")` — feedback loop |
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
# Verbose: dùng FuncOp class
def clean_text(text: str) -> dict:
    return {"cleaned": text.strip().lower()}

step = FuncOp(
    name="clean",
    code_fn=clean_text,
    inputs={"text": PARENT["text"]},
    outputs={"cleaned": PARENT}
)

# Shorthand: dùng @op decorator
from hush.core.ops import op

@op
def clean_text(text: str):
    return {"cleaned": text.strip().lower()}

step = clean_text(
    name="clean",
    text=PARENT["text"],
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

## Generator ops — Iteration bằng yield

Dùng `yield` trong `@op` function để iterate — thay thế ForOp/MapOp.

### Sequential iteration

```python
@op
def each_item(items: list, prefix: str):
    """Yield từng item — downstream ops chạy per item."""
    for item in items:
        yield {"item": item, "prefix": prefix}

@op
def process_item(item: str, prefix: str):
    return {"result": f"{prefix}: {item}"}

with GraphOp(name="for-loop") as graph:
    src = each_item(items=PARENT["items"], prefix=PARENT["prefix"])
    step = process_item(item=src["item"], prefix=src["prefix"])
    START >> src >> step >> END
```

### Parallel map

```python
@op
def each_number(numbers: list):
    for x in numbers:
        yield {"x": x}

@op
def square(x: int):
    return {"squared": x * x}

with GraphOp(name="map-op") as graph:
    src = each_number(numbers=PARENT["numbers"])
    step = square(x=src["x"])
    START >> src >> step >> END
# Downstream ops tự động chạy song song per yield
```

### While loop trong generator

```python
@op
def halve_until(value: int):
    while value >= 5:
        value = value // 2
        yield {"value": value}

with GraphOp(name="while-loop") as graph:
    src = halve_until(value=PARENT["start_value"])
    START >> src >> END
```

## @graph.loop() — Feedback Loop

Khi cần feedback loop (output iteration N → input iteration N+1), dùng `@graph.loop()`:

```python
from hush.core import graph, op, START, END, PARENT
from hush.providers import LLMOp

@graph.loop(until="done == True", max_iterations=10)
def agent_loop(messages, done, answer):
    """Agent loop: LLM → process → update state → repeat."""
    llm = LLMOp.of(resource="gpt-4o-mini", messages=messages, tools=TOOL_DESCRIPTIONS)
    proc = process_response(
        content=llm["content"],
        tool_calls=llm["tool_calls"],
        messages=messages,
    )
    proc["messages"] >> PARENT["messages"]
    proc["done"] >> PARENT["done"]
    proc["answer"] >> PARENT["answer"]
    START >> llm >> proc >> END
```

- `until="done == True"` — Điều kiện dừng (string expression)
- `max_iterations=10` — Safety net
- Function params = loop state, carry qua mỗi iteration
- Dùng `>>` để update state: `proc["done"] >> PARENT["done"]`

## if_() — BranchOp Shorthand

Routing có điều kiện với fluent syntax.

### So sánh

```python
from hush.core.ops import BranchOp, Branch, if_

# Verbose
router = BranchOp(
    name="router",
    cases=[
        Branch(condition=PARENT["score"] >= 90, target="excellent"),
        Branch(condition=PARENT["score"] >= 70, target="good"),
        Branch(condition=True, target="fail")
    ]
)

# Shorthand (fluent chaining)
router = (if_(PARENT["score"] >= 90, "excellent")
          .if_(PARENT["score"] >= 70, "good")
          .else_("fail"))
```

### Ví dụ đầy đủ

```python
with GraphOp(name="grade-workflow") as graph:
    grade_router = (
        if_(PARENT["score"] >= 90, "ex")
        .if_(PARENT["score"] >= 70, "gd")
        .if_(PARENT["score"] >= 50, "av")
        .else_("fl")
    )

    ex = excellent(outputs={"grade": PARENT, "message": PARENT})
    gd = good(outputs={"grade": PARENT, "message": PARENT})
    av = average(outputs={"grade": PARENT, "message": PARENT})
    fl = fail(outputs={"grade": PARENT, "message": PARENT})

    START >> grade_router >> [ex, gd, av, fl]
    [ex, gd, av, fl] >> ~END  # Soft edge vì chỉ 1 nhánh chạy
```

## LLMOp.of() — LLMOp Classmethod

Gọi LLM với syntax ngắn gọn.

### So sánh

```python
from hush.providers import LLMOp

# Verbose
llm = LLMOp(
    name="chat",
    resource="gpt-4o",
    inputs={"messages": PARENT["messages"], "temperature": 0.7},
    outputs={"content": PARENT["response"]}
)

# Classmethod
llm = LLMOp.of(
    resource="gpt-4o",
    name="chat",
    messages=PARENT["messages"],
    temperature=0.7,
    outputs={"content": PARENT["response"]}
)
```

### Load Balancing

```python
llm = LLMOp.of(
    resource=["gpt-4o", "gpt-4o-mini"],
    ratios=[0.3, 0.7],
    name="balanced",
    messages=PARENT["messages"],
    seed=42
)
```

### Fallback

```python
llm = LLMOp.of(
    resource="gpt-4o",
    fallback=["azure-gpt4", "gemini"],
    name="resilient",
    messages=PARENT["messages"]
)
```

## chain() — Factory Function

Prompt + LLM all-in-one. `chain()` là một factory function, trả về `GraphOp`.

```python
from hush.providers import chain

chat = chain(
    resource="gpt-4o",
    template={"system": "You are a helpful assistant.", "user": "{query}"},
    query=PARENT["query"],
)
START >> chat >> END  # result["content"], result["model_used"], ...
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
p = PromptOp.of(template="Summarize: {text}", text=PARENT["text"])

# Dict → system + user messages
p = PromptOp.of(
    template={"system": "You are a {task} expert.", "user": "{query}"},
    task="summarization",
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

# Verbose: tạo GraphOp thủ công
with GraphOp(name="main") as main:
    with GraphOp(name="double_flow", inputs={"val": PARENT["input"]}) as sub:
        step = double(x=PARENT["val"])
        START >> step >> END
    START >> sub >> END

# Shorthand: @graph decorator
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
    d2 = double_flow(val=d1["result"])
    START >> d1 >> d2 >> END

# input=3 → 3*2=6 → 6*2=12
```

### Output renaming

```python
@graph
def double_flow(val):
    step = double(x=val)
    step["result"] >> PARENT["doubled"]
    START >> step >> END

with GraphOp(name="main") as main:
    d = double_flow(val=PARENT["input"])
    d["doubled"] >> PARENT["answer"]
    START >> d >> END
```

## Best Practices

### Auto-naming

`.of()` classmethods và `@graph` sử dụng variable name làm op name khi không chỉ định:

```python
# Tên op sẽ là "grade_router" (từ variable name)
grade_router = if_(PARENT["score"] >= 90, "a").else_("b")

# Explicit name khi cần
router = if_(PARENT["score"] >= 90, "a", name="my_router").else_("b")
```

### Auto-forward outputs với >> END

Khi op kết nối trực tiếp đến END mà không định nghĩa `outputs`, tất cả outputs sẽ tự động forward lên parent:

```python
@op
def compute():
    return {"a": 1, "b": 2}

step = compute()
START >> step >> END  # result["a"] == 1, result["b"] == 2
```

## Tổng kết

| Shorthand | Config Options | Khi nào dùng |
|-----------|----------------|--------------|
| `@op` | `rust="..."`, `bound="io"/"cpu"` | Tạo op từ function |
| `yield` trong `@op` | - | Iterate list, parallel map |
| `@graph` | `name`, `outputs`, `description` | Tạo reusable workflow module |
| `@graph.loop()` | `until`, `max_iterations` | Feedback loop (agent, convergence) |
| `if_(...).else_(...)` | - | Conditional routing |
| `chain(...)` | `ratios`, `fallback`, `response_format` | Prompt + LLM all-in-one |
| `LLMOp.of(...)` | `ratios`, `fallback`, `batch_mode`, `seed` | LLM calls |
| `PromptOp.of(...)` | - | Tạo messages từ template |
| `EmbeddingOp.of(...)` | - | Tạo embeddings |
| `RerankOp.of(...)` | - | Rerank documents |

## Tiếp theo

- [Core Concepts](03-core-concepts.md) — Hiểu inputs/outputs mapping
- [Loops & Branches](05-loops-branches.md) — Chi tiết về flow control
- [LLM Integration](04-llm-integration.md) — Chi tiết về LLMOp
