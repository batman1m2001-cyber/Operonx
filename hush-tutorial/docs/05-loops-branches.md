# Loops và Branches

Sử dụng các node điều khiển luồng: `ForLoopNode.of()`, `MapNode.of()`, `WhileLoopNode.of()` và `if_()`.

> **Ví dụ chạy được**: `examples/05_loops_and_branches.py`, `examples/15_shorthand_syntax.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Syntax | Class | Ví dụ |
> |--------|-------|-------|
> | `@code_node` | `CodeNode` | `@code_node` decorator trên function |
> | `ForLoopNode.of()` | `ForLoopNode` | `ForLoopNode.of(x=Each([1,2,3]))` |
> | `MapNode.of()` | `MapNode` | `MapNode.of(x=Each([1,2,3]), max_concurrency=4)` |
> | `WhileLoopNode.of()` | `WhileLoopNode` | `WhileLoopNode.of(counter=0, stop_condition="counter >= 5")` |
> | `if_().else_()` | `BranchNode` | `if_(PARENT["x"] > 0, "pos").else_("neg")` |

## ForLoopNode.of() — Iterate tuần tự

Xử lý từng item một cách tuần tự. Dùng khi items có thể phụ thuộc vào nhau.

```python
from hush.core import Hush, GraphNode, code_node, START, END, PARENT
from hush.core import ForLoopNode, Each

@code_node
def process(item: str, prefix: str):
    return {"result": f"{prefix}: {item}"}

with GraphNode(name="sequential-process") as graph:
    with ForLoopNode.of(
        item=Each(PARENT["items"]),  # Iterate qua mỗi item
        prefix=PARENT["prefix"],     # Broadcast cho tất cả iterations
    ) as loop:
        step = process(
            name="process",
            inputs={"item": PARENT["item"], "prefix": PARENT["prefix"]},
            outputs={"*": PARENT},
        )
        START >> step >> END

    loop["result"] >> PARENT["results"]  # Map loop output → graph output
    START >> loop >> END

engine = Hush(graph)
result = await engine.run(inputs={"items": ["a", "b", "c"], "prefix": "Item"})
# result["results"] = ["Item: a", "Item: b", "Item: c"]
```

### Giải thích

- `Each(PARENT["items"])`: Đánh dấu biến sẽ được iterate
- Các biến không có `Each()` sẽ được broadcast cho tất cả iterations
- Output là list kết quả theo thứ tự

## MapNode.of() — Iterate song song

Xử lý nhiều items cùng lúc (parallel). Dùng cho I/O bound tasks hoặc items độc lập.

```python
from hush.core import MapNode, Each

@code_node
def square(x: int):
    return {"squared": x * x}

with GraphNode(name="parallel-map") as graph:
    with MapNode.of(
        x=Each(PARENT["numbers"]),
        max_concurrency=3,  # Giới hạn concurrent tasks
    ) as map_node:
        step = square(
            name="square",
            inputs={"x": PARENT["x"]},
            outputs={"*": PARENT},
        )
        START >> step >> END

    map_node["squared"] >> PARENT["results"]  # Map loop output → graph output
    START >> map_node >> END

# result["results"] = [1, 4, 9, 16, 25]
```

### So sánh ForLoopNode vs MapNode

| Tiêu chí | ForLoopNode | MapNode |
|----------|-------------|---------|
| Execution | Tuần tự (sequential) | Song song (parallel) |
| Dependencies | Items có thể phụ thuộc nhau | Items độc lập |
| Memory | Thấp hơn | Cao hơn |
| Use case | Chain processing, stateful | I/O bound, batch processing |
| Classmethod | `ForLoopNode.of(...)` | `MapNode.of(...)` |

## WhileLoopNode.of() — Loop với điều kiện

Chạy cho đến khi điều kiện trả về False.

```python
from hush.core import WhileLoopNode

@code_node
def halve_value(value: int):
    return {"new_value": value // 2}

with GraphNode(name="countdown") as graph:
    with WhileLoopNode.of(
        value=PARENT["start_value"],
        stop_condition="value < 5",
        max_iterations=20,
    ) as while_loop:
        step = halve_value(
            name="halve",
            inputs={"value": PARENT["value"]},
        )
        step["new_value"] >> PARENT["value"]  # Update loop state
        START >> step >> END

    while_loop["value"] >> PARENT["final_value"]  # Map loop output → graph
    START >> while_loop >> END

result = await engine.run(inputs={"start_value": 256})
# 256 → 128 → 64 → 32 → 16 → 8 → 4 (dừng vì < 5)
# result["final_value"] = 4
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

    ex = excellent(outputs={"grade": PARENT, "message": PARENT})
    gd = good(outputs={"grade": PARENT, "message": PARENT})
    av = average(outputs={"grade": PARENT, "message": PARENT})
    fl = fail(outputs={"grade": PARENT, "message": PARENT})

    START >> grade_router
    grade_router >> [ex, gd, av, fl]
    # Soft edge (~) vì chỉ 1 nhánh chạy
    [ex, gd, av, fl] >> ~END

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
with GraphNode(name="nested-loops") as graph:
    with ForLoopNode.of(x=Each([2, 3, 4])) as outer:
        with ForLoopNode.of(y=Each([10, 20, 30]), x=PARENT["x"]) as inner:
            mult = multiply(
                name="multiply",
                inputs={"x": PARENT["x"], "y": PARENT["y"]},
                outputs={"*": PARENT},
            )
            START >> mult >> END

        sum_node = summarize(
            name="summarize",
            inputs={"products": inner["product"]},
            outputs={"*": PARENT},
        )
        START >> inner >> sum_node >> END

    outer["total"] >> PARENT["results"]  # Map outer output → graph
    START >> outer >> END

# result["results"] = [120, 180, 240]
```

## Tổng kết

| Node | Classmethod | Execution | Use case |
|------|-------------|-----------|----------|
| `ForLoopNode` | `ForLoopNode.of()` | Sequential | Items phụ thuộc nhau |
| `MapNode` | `MapNode.of()` | Parallel | I/O bound, independent items |
| `WhileLoopNode` | `WhileLoopNode.of()` | Conditional | Loop đến khi điều kiện False |
| `BranchNode` | `if_()` | Conditional | Route dựa trên điều kiện |

| Syntax | Mô tả |
|--------|-------|
| `Each(PARENT["items"])` | Đánh dấu biến để iterate |
| `>>` | Hard edge — chờ tất cả |
| `~` | Soft edge — chờ bất kỳ một |
| `node["key"] >> PARENT["key"]` | Output mapping — map node output sang graph/parent |
| `outputs={"*": PARENT}` | Forward tất cả outputs lên parent |

## Tiếp theo

- [Shorthand Syntax](12-shorthand-syntax.md) — Viết code ngắn gọn hơn
- [Embeddings & RAG](06-embeddings-rag.md) — Vector search và reranking
- [Error Handling](07-error-handling.md) — Xử lý lỗi
- [Parallel Execution](08-parallel-execution.md) — Chi tiết về parallel patterns
