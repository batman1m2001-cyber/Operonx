# Loops và Branches

Sử dụng các node điều khiển luồng: ForLoopNode, MapNode, WhileLoopNode và if_() / BranchNode.

> **Ví dụ chạy được**: `examples/05_loops_and_branches.py`, `examples/15_shorthand_syntax.py`
>
> **Tip**: Xem [Shorthand Syntax](12-shorthand-syntax.md) để viết code ngắn gọn hơn với `for_()`, `map_()`, `while_()`.

## ForLoopNode — Iterate tuần tự

Xử lý từng item một cách tuần tự. Dùng khi items có thể phụ thuộc vào nhau.

```python
from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT
from hush.core.nodes.iteration import ForLoopNode
from hush.core.nodes.iteration.base import Each

with GraphNode(name="sequential-process") as graph:
    with ForLoopNode(
        name="process_items",
        inputs={
            "item": Each(PARENT["items"]),  # Iterate qua mỗi item
            "prefix": PARENT["prefix"]       # Broadcast cho tất cả iterations
        },
        outputs={"results": PARENT}
    ) as loop:
        process = CodeNode(
            name="process",
            code_fn=lambda item, prefix: {"result": f"{prefix}: {item}"},
            inputs={"item": PARENT["item"], "prefix": PARENT["prefix"]},
            outputs={"result": PARENT}
        )
        START >> process >> END

    START >> loop >> END

engine = Hush(graph)
result = await engine.run(inputs={"items": ["a", "b", "c"], "prefix": "Item"})
# result["results"] = ["Item: a", "Item: b", "Item: c"]
```

### Giải thích

- `Each(PARENT["items"])`: Đánh dấu biến sẽ được iterate
- Các biến không có `Each()` sẽ được broadcast cho tất cả iterations
- Output là list kết quả theo thứ tự

### Shorthand: for_()

```python
from hush.core.nodes import for_, Each

# Shorthand — truyền inputs trực tiếp
with for_(
    item=Each(PARENT["items"]),  # Iterate
    prefix=PARENT["prefix"]       # Broadcast
) as loop:
    ...
```

## MapNode — Iterate song song

Xử lý nhiều items cùng lúc (parallel). Dùng cho I/O bound tasks hoặc items độc lập.

```python
from hush.core.nodes.iteration import MapNode

with GraphNode(name="parallel-fetch") as graph:
    with MapNode(
        name="fetch_all",
        inputs={"url": Each(PARENT["urls"]), "timeout": 30},
        max_concurrency=10,  # Giới hạn concurrent tasks
        outputs={"results": PARENT}
    ) as map_node:
        fetch = CodeNode(
            name="fetch",
            code_fn=lambda url, timeout: {"data": f"Content from {url}"},
            inputs={"url": PARENT["url"], "timeout": PARENT["timeout"]},
            outputs={"data": PARENT}
        )
        START >> fetch >> END

    START >> map_node >> END
```

### Shorthand: map_()

```python
from hush.core.nodes import map_, Each

# Shorthand — truyền inputs và config trực tiếp
with map_(
    url=Each(PARENT["urls"]),
    timeout=30,
    max_concurrency=10
) as map_node:
    ...
```

### So sánh ForLoopNode vs MapNode

| Tiêu chí | ForLoopNode | MapNode |
|----------|-------------|---------|
| Execution | Tuần tự (sequential) | Song song (parallel) |
| Dependencies | Items có thể phụ thuộc nhau | Items độc lập |
| Memory | Thấp hơn | Cao hơn |
| Use case | Chain processing, stateful | I/O bound, batch processing |
| Shorthand | `for_(...)` | `map_(...)` |

## WhileLoopNode — Loop với điều kiện

Chạy cho đến khi điều kiện trả về False.

```python
from hush.core.nodes.iteration import WhileLoopNode

with GraphNode(name="countdown") as graph:
    with WhileLoopNode(
        name="countdown_loop",
        condition=lambda count: count > 0,
        inputs={"count": PARENT["start"]},
        outputs={"final_count": PARENT}
    ) as loop:
        decrement = CodeNode(
            name="decrement",
            code_fn=lambda count: {"count": count - 1, "message": f"Count: {count}"},
            inputs={"count": PARENT["count"]},
            outputs={"count": PARENT, "message": PARENT}
        )
        START >> decrement >> END

    START >> loop >> END

result = await engine.run(inputs={"start": 5})
# result["final_count"] = 0
```

### Shorthand: while_()

```python
from hush.core.nodes import while_

# Shorthand — truyền inputs và config trực tiếp
with while_(
    count=PARENT["start"],
    stop_condition="count <= 0",  # String expression
    max_iterations=100
) as loop:
    ...
```

## BranchNode — Conditional Routing

Định tuyến workflow theo điều kiện. Chỉ một nhánh được thực thi.

```python
from hush.core.nodes.flow.branch_node import if_

with GraphNode(name="grade-workflow") as graph:
    grade_router = (if_(PARENT["score"] >= 90, "excellent")
                    .if_(PARENT["score"] >= 70, "good")
                    .if_(PARENT["score"] >= 50, "average")
                    .else_("fail"))

    excellent = CodeNode(name="excellent", code_fn=lambda: {"grade": "A"}, outputs={"grade": PARENT})
    good = CodeNode(name="good", code_fn=lambda: {"grade": "B"}, outputs={"grade": PARENT})
    average = CodeNode(name="average", code_fn=lambda: {"grade": "C"}, outputs={"grade": PARENT})
    fail = CodeNode(name="fail", code_fn=lambda: {"grade": "F"}, outputs={"grade": PARENT})

    START >> grade_router
    grade_router >> [excellent, good, average, fail]
    [excellent, good, average, fail] >> ~END  # Soft edge — chỉ 1 nhánh chạy

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
    with ForLoopNode(
        name="outer",
        inputs={"category": Each(PARENT["categories"])},
        outputs={"all_results": PARENT}
    ) as outer:
        with MapNode(
            name="inner",
            inputs={"item": Each(PARENT["category"]["items"])},
            max_concurrency=5,
            outputs={"category_results": PARENT}
        ) as inner:
            process = CodeNode(...)
            START >> process >> END
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
