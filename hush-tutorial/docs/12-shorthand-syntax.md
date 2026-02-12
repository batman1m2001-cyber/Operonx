# Shorthand Syntax

Hush cung cấp các `.of()` classmethod và decorator để viết workflow ngắn gọn hơn. Thay vì dùng class đầy đủ với `inputs={}`, bạn có thể truyền trực tiếp các tham số.

> **Ví dụ chạy được**: `examples/15_shorthand_syntax.py`, `examples/16_subgraph.py`

## Tổng quan

| Full Class | Shorthand | Mô tả |
|------------|-----------|-------|
| `CodeNode(...)` | `@code_node` | Decorator tạo CodeNode từ function |
| `GraphNode(...)` + manual setup | `@subgraph` | Decorator tạo reusable workflow module |
| `ForLoopNode(inputs={...})` | `ForLoopNode.of(item=Each(...), ...)` | Iterate tuần tự |
| `MapNode(inputs={...})` | `MapNode.of(item=Each(...), ...)` | Iterate song song |
| `WhileLoopNode(inputs={...})` | `WhileLoopNode.of(counter=0, stop_condition=...)` | Loop với điều kiện |
| `AsyncIterNode(inputs={...})` | `AsyncIterNode.of(chunk=Each(stream), ...)` | Xử lý async streaming |
| `BranchNode(...)` | `if_(...).else_(...)` | Routing có điều kiện |
| `LLMChainNode(inputs={...})` | `LLMChainNode.of(resource_key="gpt-4o", template=..., ...)` | Prompt + LLM all-in-one |
| `LLMNode(inputs={...})` | `LLMNode.of(resource_key="gpt-4o", messages=...)` | Gọi LLM |
| `PromptNode(inputs={...})` | `PromptNode.of(template=..., ...)` | Tạo messages từ template |
| `EmbeddingNode(inputs={...})` | `EmbeddingNode.of(resource_key="model", texts=...)` | Tạo embeddings |
| `RerankNode(inputs={...})` | `RerankNode.of(resource_key="model", query=..., documents=...)` | Rerank documents |

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

## ForLoopNode.of() — ForLoopNode Classmethod

Iterate tuần tự qua collection.

### So sánh

```python
from hush.core import ForLoopNode, Each

# ❌ Verbose
with ForLoopNode(
    name="loop",
    inputs={
        "item": Each(PARENT["items"]),
        "prefix": PARENT["prefix"]
    }
) as loop:
    ...

# ✅ Classmethod
with ForLoopNode.of(
    name="loop",
    item=Each(PARENT["items"]),   # Each() = iterate
    prefix=PARENT["prefix"]        # Không có Each() = broadcast
) as loop:
    ...
```

### Ví dụ đầy đủ

```python
from hush.core import ForLoopNode, Each, code_node

@code_node
def double(x: int):
    return {"result": x * 2}

with GraphNode(name="demo") as graph:
    with ForLoopNode.of(item=Each([1, 2, 3, 4, 5])) as loop:
        step = double(name="double", x=PARENT["item"], outputs={"*": PARENT})
        START >> step >> END

    loop["result"] >> PARENT["results"]
    START >> loop >> END

# result["results"] = [2, 4, 6, 8, 10]
```

## MapNode.of() — MapNode Classmethod

Iterate song song với giới hạn concurrency.

### So sánh

```python
from hush.core import MapNode, Each

# ❌ Verbose
with MapNode(
    name="parallel",
    inputs={"url": Each(PARENT["urls"]), "timeout": 30},
    max_concurrency=10
) as map_node:
    ...

# ✅ Classmethod
with MapNode.of(
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
    with MapNode.of(x=Each([1, 2, 3, 4, 5]), max_concurrency=3) as loop:
        step = square(name="square", x=PARENT["x"], outputs={"*": PARENT})
        START >> step >> END

    loop["squared"] >> PARENT["results"]
    START >> loop >> END

# result["results"] = [1, 4, 9, 16, 25]
```

## WhileLoopNode.of() — WhileLoopNode Classmethod

Loop cho đến khi điều kiện dừng.

### So sánh

```python
from hush.core import WhileLoopNode

# ❌ Verbose
with WhileLoopNode(
    name="countdown",
    inputs={"count": PARENT["start"]},
    stop_condition="count <= 0",
    max_iterations=100
) as loop:
    ...

# ✅ Classmethod
with WhileLoopNode.of(
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
    with WhileLoopNode.of(value=256, stop_condition="value < 10", max_iterations=20) as loop:
        step = halve(name="halve", value=PARENT["value"])
        step["new_value"] >> PARENT["value"]
        START >> step >> END

    loop["value"] >> PARENT["final"]
    START >> loop >> END

# 256 → 128 → 64 → 32 → 16 → 8 (dừng vì < 10)
```

## AsyncIterNode.of() — AsyncIterNode Classmethod

Xử lý async streaming data với concurrent processing.

### So sánh

```python
from hush.core import AsyncIterNode, Each

# ❌ Verbose
with AsyncIterNode(
    name="stream_processor",
    inputs={"chunk": Each(async_stream)},
    callback=handle_result,
    max_concurrency=5
) as stream:
    ...

# ✅ Classmethod
with AsyncIterNode.of(
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
        with AsyncIterNode.of(
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

## LLMNode.of() — LLMNode Classmethod

Gọi LLM với syntax ngắn gọn.

### So sánh

```python
from hush.providers import LLMNode

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

# ✅ Classmethod
llm = LLMNode.of(
    resource_key="gpt-4o",
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
llm = LLMNode.of(
    resource_key=["gpt-4o", "gpt-4o-mini"],
    ratios=[0.3, 0.7],
    name="balanced",
    messages=PARENT["messages"],
    seed=42  # Reproducible selection
)
```

### Fallback

```python
# Tự động fallback khi primary fails
llm = LLMNode.of(
    resource_key="gpt-4o",
    fallback=["azure-gpt4", "gemini"],
    name="resilient",
    messages=PARENT["messages"]
)
```

### Batch Mode

```python
# OpenAI Batch API (50% cheaper)
llm = LLMNode.of(
    resource_key="gpt-4o",
    batch_mode=True,
    name="batch_llm",
    messages=PARENT["messages"]
)
```

## LLMChainNode.of() — LLMChainNode Classmethod

Prompt + LLM all-in-one. Ngắn nhất có thể.

### So sánh

```python
from hush.providers import LLMChainNode

# ❌ Verbose
chain = LLMChainNode(
    name="chat",
    resource_key="gpt-4o",
    inputs={
        "template": {"system": "Bạn là assistant.", "user": "{query}"},
        "query": PARENT["query"],
        "*": PARENT,
    },
    outputs={"content": PARENT["response"]},
)

# ✅ Classmethod (auto-name + >> END auto-forward)
chat = LLMChainNode.of(
    resource_key="gpt-4o",
    template={"system": "Bạn là assistant.", "user": "{query}"},
    query=PARENT["query"],
)
START >> chat >> END  # result["content"], result["model_used"], ...
```

### String template

```python
summarize = LLMChainNode.of(resource_key="gpt-4o", template="Tóm tắt: {text}", text=PARENT["text"])
```

### Structured output

```python
classifier = LLMChainNode.of(
    resource_key="gpt-4o",
    template={"user": "Phân loại: {text}"},
    text=PARENT["text"],
    response_format={"type": "json_object"},
)
```

### Load Balancing + Fallback

```python
chat = LLMChainNode.of(
    resource_key=["gpt-4o", "gpt-4o-mini"],
    template={"system": "Help.", "user": "{query}"},
    ratios=[0.7, 0.3],
    fallback=["or-claude-4-sonnet"],
    query=PARENT["query"],
)
```

## PromptNode.of() — PromptNode Classmethod

Tạo messages từ template, dùng khi cần tách riêng prompt và LLM.

```python
from hush.providers import PromptNode

# String → [{"role": "user", "content": "..."}]
p = PromptNode.of(template="Tóm tắt: {text}", text=PARENT["text"])

# Dict → system + user messages
p = PromptNode.of(
    template={"system": "Bạn là assistant chuyên {task}.", "user": "{query}"},
    task="tóm tắt",
    query=PARENT["query"],
)
```

## EmbeddingNode.of() — EmbeddingNode Classmethod

Tạo embeddings từ text.

```python
from hush.providers import EmbeddingNode

embed = EmbeddingNode.of(resource_key="bge-m3", texts=PARENT["texts"])
START >> embed >> END  # result["embeddings"]
```

## RerankNode.of() — RerankNode Classmethod

Rerank documents theo query.

```python
from hush.providers import RerankNode

rerank = RerankNode.of(resource_key="bge-m3", query=PARENT["query"], documents=PARENT["docs"], top_k=5)
START >> rerank >> END  # result["reranked_documents"]
```

## @subgraph — Modular Workflow

`@subgraph` biến builder function thành factory tạo `GraphNode` tái sử dụng. Tham số function tự động trở thành `PARENT` refs.

### So sánh

```python
from hush.core import subgraph, code_node, GraphNode, START, END, PARENT

@code_node
def double(x: int):
    return {"result": x * 2}

# ❌ Verbose: tạo GraphNode thủ công
with GraphNode(name="main") as main:
    with GraphNode(name="double_flow", inputs={"val": PARENT["input"]}) as sub:
        step = double(x=PARENT["val"])
        START >> step >> END
    START >> sub >> END

# ✅ Shorthand: @subgraph decorator
@subgraph
def double_flow(val):
    step = double(x=val)        # val = PARENT["val"] (injected)
    START >> step >> END

with GraphNode(name="main") as main:
    d = double_flow(val=PARENT["input"])  # auto-named "d"
    START >> d >> END
```

### Tái sử dụng — Chuỗi subgraphs

```python
with GraphNode(name="chain") as main:
    d1 = double_flow(val=PARENT["input"])
    d2 = double_flow(val=d1["result"])      # chain output
    START >> d1 >> d2 >> END

# input=3 → 3*2=6 → 6*2=12
```

### Output renaming

```python
@subgraph
def double_flow(val):
    step = double(x=val)
    step["result"] >> PARENT["doubled"]     # rename output key
    START >> step >> END

with GraphNode(name="main") as main:
    d = double_flow(val=PARENT["input"])
    d["doubled"] >> PARENT["answer"]        # map to graph output
    START >> d >> END

# result["answer"] == 14 (input=7)
```

### Zero-param subgraph

```python
@subgraph
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
with ForLoopNode.of(item=Each(items), multiplier=10) as loop:
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

### 2. Mix .of() classmethod và verbose

```python
# OK: mix trong cùng workflow
with GraphNode(name="mixed") as graph:
    # Classmethod cho simple nodes
    with ForLoopNode.of(item=Each(PARENT["items"])) as loop:
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

`.of()` classmethods sử dụng variable name làm node name khi không chỉ định:

```python
# Tên node sẽ là "grade_router" (từ variable name)
grade_router = if_(PARENT["score"] >= 90, "a").else_("b")

# Explicit name khi cần
router = if_(PARENT["score"] >= 90, "a", name="my_router").else_("b")
```

### 4. Auto-forward outputs với >> END

Khi node kết nối trực tiếp đến END mà không định nghĩa `outputs`, tất cả outputs sẽ tự động forward lên parent:

```python
# ❌ Verbose: phải viết outputs
node = CodeNode(
    name="compute",
    code_fn=lambda: {"a": 1, "b": 2},
    outputs={"a": PARENT, "b": PARENT}
)
START >> node >> END

# ✅ Shorthand: auto-forward tất cả outputs
node = CodeNode(
    name="compute",
    code_fn=lambda: {"a": 1, "b": 2}
)
START >> node >> END  # result["a"] == 1, result["b"] == 2

# Với @code_node decorator
@code_node
def compute():
    return {"a": 1, "b": 2}

step = compute(name="step")  # Không cần outputs
START >> step >> END         # Auto-forward
```

## Tổng kết

| Shorthand | Config Options | Khi nào dùng |
|-----------|----------------|--------------|
| `@code_node` | - | Tạo node từ function |
| `@subgraph` | `name`, `outputs`, `description` | Tạo reusable workflow module |
| `ForLoopNode.of(...)` | - | Sequential iteration |
| `MapNode.of(...)` | `max_concurrency` | Parallel iteration |
| `WhileLoopNode.of(...)` | `stop_condition`, `max_iterations` | Conditional loop |
| `AsyncIterNode.of(...)` | `max_concurrency`, `callback`, `batch_fn` | Async streaming |
| `if_(...).else_(...)` | - | Conditional routing |
| `LLMChainNode.of(...)` | `ratios`, `fallback`, `response_format`, `extract` | Prompt + LLM all-in-one |
| `LLMNode.of(...)` | `ratios`, `fallback`, `batch_mode`, `seed` | LLM calls |
| `PromptNode.of(...)` | - | Tạo messages từ template |
| `EmbeddingNode.of(...)` | - | Tạo embeddings |
| `RerankNode.of(...)` | - | Rerank documents |

## Tiếp theo

- [Core Concepts](03-core-concepts.md) — Hiểu inputs/outputs mapping
- [Loops & Branches](05-loops-branches.md) — Chi tiết về flow control
- [LLM Integration](04-llm-integration.md) — Chi tiết về LLMNode
