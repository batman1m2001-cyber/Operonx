# Loops và Branches

Sử dụng generator ops (yield) để iterate, `@graph.loop()` cho conditional loops, và `if_()` cho branches.

> **Ví dụ chạy được**: `examples/05_loops_and_branches/demo.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Syntax | Class | Ví dụ |
> |--------|-------|-------|
> | `@op` | `FuncOp` | `@op` decorator trên function |
> | `yield` | Generator op | `yield {"item": item}` trong `@op` function |
> | `@graph.loop()` | Loop GraphOp | `@graph.loop(until="done == True")` |
> | `if_().else_()` | `BranchOp` | `if_(PARENT["x"] > 0, "pos").else_("neg")` |

## Generator ops — Iterate bằng yield

Thay vì dùng ForOp/MapOp, Hush dùng **generator ops** — `@op` functions dùng `yield` để phát ra từng item. Downstream ops tự động chạy cho mỗi item được yield.

### Sequential iteration

```python
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def each_item(items: list, prefix: str):
    """Yield từng item — downstream ops tự động chạy per item."""
    for item in items:
        yield {"item": item, "prefix": prefix}

@op
def process_item(item: str, prefix: str):
    return {"result": f"{prefix}: {item}"}

with GraphOp(name="sequential-process") as graph:
    src = each_item(items=PARENT["items"], prefix=PARENT["prefix"])
    step = process_item(item=src["item"], prefix=src["prefix"])
    START >> src >> step >> END

engine = Hush(graph)
result = await engine.run(inputs={"items": ["a", "b", "c"], "prefix": "Item"})
# result["result"] = ["Item: a", "Item: b", "Item: c"]
```

### Parallel map

Generator ops tự động song song hóa downstream ops — scheduler chạy parallel cho mỗi item yield:

```python
@op
def each_number(numbers: list):
    """Yield từng số — scheduler tự động song song hóa."""
    for x in numbers:
        yield {"x": x}

@op
def square(x: int):
    return {"squared": x * x}

with GraphOp(name="parallel-map") as graph:
    src = each_number(numbers=PARENT["numbers"])
    step = square(x=src["x"])
    START >> src >> step >> END

# result["squared"] = [1, 4, 9, 16, 25]
```

### While loop trong generator

Dùng `while` trong generator để loop cho đến khi điều kiện dừng:

```python
@op
def halve_until(value: int):
    """Chia đôi cho đến khi < 5."""
    while value >= 5:
        value = value // 2
        yield {"value": value}

with GraphOp(name="while-loop") as graph:
    src = halve_until(value=PARENT["start_value"])
    START >> src >> END

result = await engine.run(inputs={"start_value": 256})
# 256 → 128 → 64 → 32 → 16 → 8 → 4 (dừng vì < 5)
```

## @graph.loop() — Feedback loop với điều kiện

Khi cần **feedback loop** (output iteration N trở thành input iteration N+1), dùng `@graph.loop()`:

```python
from hush.core import graph, op, START, END, PARENT

@op
def increment(counter: int):
    return {"counter": counter + 1}

@graph.loop(until="count >= 5", max_iterations=10)
def counting_loop(count):
    inc = increment(counter=count)
    inc["counter"] >> PARENT["count"]
    START >> inc >> END

with GraphOp(name="demo") as g:
    loop = counting_loop(count=0)
    loop["count"] >> PARENT["final_count"]
    START >> loop >> END

# count: 0 → 1 → 2 → 3 → 4 → 5 (dừng vì >= 5)
# result["final_count"] = 5
```

### Giải thích @graph.loop

- `until="count >= 5"`: Điều kiện dừng (string expression, đánh giá sau mỗi iteration)
- `max_iterations=10`: Giới hạn số vòng lặp (safety net)
- Function params (`count`) tự động trở thành `PARENT` refs
- Dùng `>>` operator để update loop state: `inc["counter"] >> PARENT["count"]`

### So sánh Generator vs @graph.loop

| Tiêu chí | Generator (`yield`) | `@graph.loop()` |
|----------|-------------------|-----------------|
| Pattern | Fan-out: 1 input → N outputs | Feedback: output N → input N+1 |
| Use case | Iterate list, parallel map | Agent loops, convergence |
| State | Stateless — mỗi yield độc lập | Stateful — state carry qua iterations |
| Parallelism | Downstream ops chạy parallel | Sequential iterations |
| Ví dụ | Xử lý list items | LLM agent tool-calling loop |

## BranchOp — Conditional Routing

Định tuyến workflow theo điều kiện. Chỉ một nhánh được thực thi.

```python
from hush.core.ops import if_, op

@op
def excellent():
    return {"grade": "A", "message": "Xuất sắc!"}

@op
def good():
    return {"grade": "B", "message": "Tốt!"}

@op
def average():
    return {"grade": "C", "message": "Trung bình"}

@op
def fail():
    return {"grade": "F", "message": "Cần cải thiện"}

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

    START >> grade_router
    grade_router >> [ex, gd, av, fl]
    [ex, gd, av, fl] >> ~END  # Soft edge vì chỉ 1 nhánh chạy

result = await engine.run(inputs={"score": 85})
# result["grade"] = "B", result["message"] = "Tốt!"
```

> **Tip**: `if_()` tự suy tên op từ tên biến (`grade_router`). Nếu muốn tên khác, dùng `Branch("custom_name").if_(...).else_(...)`.
> Hoặc dùng `BranchOp(name=..., cases=[...])` cho full control.

### Hard Edge vs Soft Edge

- `>>` (Hard Edge): Op đích chờ **tất cả** predecessors hoàn thành
- `>>~` (Soft Edge): Op đích chờ **bất kỳ một** soft predecessor hoàn thành

```python
# Sau branch, dùng soft edge vì chỉ 1 nhánh chạy
[path_a, path_b, path_c] >> ~merge_node
```

## Tổng kết

| Pattern | Syntax | Use case |
|---------|--------|----------|
| Generator yield | `yield {"key": val}` trong `@op` | Iterate list, parallel map |
| `@graph.loop()` | `@graph.loop(until="...", max_iterations=N)` | Feedback loops, agent loops |
| `if_().else_()` | `if_(cond, target).else_(default)` | Conditional routing |

| Syntax | Mô tả |
|--------|-------|
| `yield {...}` | Phát ra item — downstream ops chạy per item |
| `@graph.loop(until=...)` | Loop cho đến khi điều kiện True |
| `>>` | Hard edge — chờ tất cả |
| `>>~` | Soft edge — chờ bất kỳ một |
| `op["key"] >> PARENT["key"]` | Output mapping — map op output sang parent |
| `outputs={"*": PARENT}` | Forward tất cả outputs lên parent |

## Tiếp theo

- [Shorthand Syntax](12-shorthand-syntax.md) — Viết code ngắn gọn hơn
- [Embeddings & RAG](06-embeddings-rag.md) — Vector search và reranking
- [Error Handling](07-error-handling.md) — Xử lý lỗi
- [Parallel Execution](08-parallel-execution.md) — Chi tiết về parallel patterns
