# Loops và Branches

Sử dụng các node điều khiển luồng: `for_()`, `map_()`, `while_()` và `if_()`.

> **Ví dụ chạy được**: `examples/05_loops_and_branches.py`, `examples/15_shorthand_syntax.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Viết tắt | Class gốc | Ví dụ |
> |----------|-----------|-------|
> | `@code_node` | `CodeNode` | `@code_node` decorator trên function |
> | `for_()` | `ForLoopNode` | `for_(x=Each([1,2,3]))` |
> | `map_()` | `MapNode` | `map_(x=Each([1,2,3]), max_concurrency=4)` |
> | `while_()` | `WhileLoopNode` | `while_(counter=0, stop_condition="counter >= 5")` |
> | `if_().else_()` | `BranchNode` | `if_(PARENT["x"] > 0, "pos").else_("neg")` |

## for_() — Iterate tuần tự

Xử lý từng item một cách tuần tự. Dùng khi items có thể phụ thuộc vào nhau.

```python
from hush.core import Hush, GraphNode, code_node, START, END, PARENT
from hush.core.nodes import for_, Each

@code_node
def process(item: str, prefix: str):
    return {"result": f"{prefix}: {item}"}

with GraphNode(name="sequential-process") as graph:
    with for_(
        item=Each(PARENT["items"]),  # Iterate qua mỗi item
        prefix=PARENT["prefix"],     # Broadcast cho tất cả iterations
    ) as loop:
        step = process(item=PARENT["item"], prefix=PARENT["prefix"])
        START >> step >> END

    START >> loop >> END

engine = Hush(graph)
result = await engine.run(inputs={"items": ["a", "b", "c"], "prefix": "Item"})
# result["results"] = ["Item: a", "Item: b", "Item: c"]
```

### Giải thích

- `Each(PARENT["items"])`: Đánh dấu biến sẽ được iterate
- Các biến không có `Each()` sẽ được broadcast cho tất cả iterations
- Output là list kết quả theo thứ tự

## map_() — Iterate song song

Xử lý nhiều items cùng lúc (parallel). Dùng cho I/O bound tasks hoặc items độc lập.

```python
from hush.core.nodes import map_, Each

@code_node
def fetch(url: str, timeout: int):
    return {"data": f"Content from {url}"}

with GraphNode(name="parallel-fetch") as graph:
    with map_(
        url=Each(PARENT["urls"]),
        timeout=30,
        max_concurrency=10,  # Giới hạn concurrent tasks
    ) as map_node:
        step = fetch(url=PARENT["url"], timeout=PARENT["timeout"])
        START >> step >> END

    START >> map_node >> END
```

### So sánh ForLoopNode vs MapNode

| Tiêu chí | ForLoopNode | MapNode |
|----------|-------------|---------|
| Execution | Tuần tự (sequential) | Song song (parallel) |
| Dependencies | Items có thể phụ thuộc nhau | Items độc lập |
| Memory | Thấp hơn | Cao hơn |
| Use case | Chain processing, stateful | I/O bound, batch processing |
| Shorthand | `for_(...)` | `map_(...)` |

## while_() — Loop với điều kiện

Chạy cho đến khi điều kiện trả về False.

```python
from hush.core.nodes import while_

@code_node
def decrement(count: int):
    return {"count": count - 1, "message": f"Count: {count}"}

with GraphNode(name="countdown") as graph:
    with while_(
        count=PARENT["start"],
        stop_condition="count <= 0",
        max_iterations=100,
    ) as loop:
        step = decrement(count=PARENT["count"])
        START >> step >> END

    START >> loop >> END

result = await engine.run(inputs={"start": 5})
# result["final_count"] = 0
```

## BranchNode — Conditional Routing

Định tuyến workflow theo điều kiện. Chỉ một nhánh được thực thi.

```python
from hush.core.nodes.flow.branch_node import if_

@code_node
def excellent():
    return {"grade": "A"}

@code_node
def good():
    return {"grade": "B"}

@code_node
def average():
    return {"grade": "C"}

@code_node
def fail():
    return {"grade": "F"}

with GraphNode(name="grade-workflow") as graph:
    grade_router = (if_(PARENT["score"] >= 90, "excellent")
                    .if_(PARENT["score"] >= 70, "good")
                    .if_(PARENT["score"] >= 50, "average")
                    .else_("fail"))

    a = excellent()
    b = good()
    c = average()
    d = fail()

    START >> grade_router
    grade_router >> [a, b, c, d]
    [a, b, c, d] >> ~END  # Soft edge — chỉ 1 nhánh chạy

result = await engine.run(inputs={"score": 85})
# result["grade"] = "B"
```

> **Tip**: `if_()` tự suy tên node từ tên biến (`grade_router`). Nếu muốn tên khác, dùng `Branch("custom_name").if_(...).else_(...)`.
> Hoặc dùng `BranchNode(name=..., cases=[...])` cho full control.

### Hard Edge vs Soft Edge

- `>>` (Hard Edge): Node đích chờ **tất cả** predecessors hoàn thành
- `~` (Soft Edge): Node đích chờ **bất kỳ một** soft predecessor hoàn thành

```python
# Sau branch, dùng soft edge vì chỉ 1 nhánh chạy
[path_a, path_b, path_c] >> ~merge_node
```

## Nested Loops

Loops có thể nest bên trong nhau:

```python
with GraphNode(name="nested") as graph:
    with for_(category=Each(PARENT["categories"])) as outer:
        with map_(
            item=Each(PARENT["category"]["items"]),
            max_concurrency=5,
        ) as inner:
            step = process(...)
            START >> step >> END
        START >> inner >> END
    START >> outer >> END
```

## Tổng kết

| Node | Shorthand | Execution | Use case |
|------|-----------|-----------|----------|
| `ForLoopNode` | `for_()` | Sequential | Items phụ thuộc nhau |
| `MapNode` | `map_()` | Parallel | I/O bound, independent items |
| `WhileLoopNode` | `while_()` | Conditional | Loop đến khi điều kiện False |
| `BranchNode` | `if_()` | Conditional | Route dựa trên điều kiện |

| Syntax | Mô tả |
|--------|-------|
| `Each(PARENT["items"])` | Đánh dấu biến để iterate |
| `>>` | Hard edge — chờ tất cả |
| `~` | Soft edge — chờ bất kỳ một |

## Tiếp theo

- [Shorthand Syntax](12-shorthand-syntax.md) — Viết code ngắn gọn hơn
- [Embeddings & RAG](06-embeddings-rag.md) — Vector search và reranking
- [Error Handling](07-error-handling.md) — Xử lý lỗi
- [Parallel Execution](08-parallel-execution.md) — Chi tiết về parallel patterns
