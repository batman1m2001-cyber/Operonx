"""Tutorial 05: Loops và Branches — Điều khiển luồng workflow.

Không cần API key. Chỉ dùng hush-core.

Học được:
- Generator @op với yield: iterate tuần tự (thay ForOp)
- Generator @op tự động song song hóa bởi scheduler (thay MapOp)
- Generator @op với while loop (thay WhileOp)
- if_(): routing có điều kiện
- Broadcast: op thường chạy 1 lần, kết quả broadcast cho stream items
- Soft edge (~): merge sau branch

Chạy: cd tutorial && uv run python examples/05_loops_and_branches.py
"""

import asyncio

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import if_, op

# =============================================================================
# Generator ops dùng yield để iterate (thay ForOp/MapOp/WhileOp)
# =============================================================================


@op
def each_item(items: list, prefix: str):
    """Yield từng item — downstream ops tự động chạy per item."""
    for item in items:
        yield {"item": item, "prefix": prefix}


@op
def process_item(item: str, prefix: str):
    """Xử lý 1 item."""
    return {"result": f"{prefix}: {item}"}


@op
def each_number(numbers: list):
    """Yield từng số — scheduler tự động song song hóa."""
    for x in numbers:
        yield {"x": x}


@op
def square(x: int):
    """Bình phương số."""
    return {"squared": x * x}


@op
def halve_until(value: int):
    """Chia đôi cho đến khi < 5 — while loop trong generator."""
    while value >= 5:
        value = value // 2
        yield {"value": value}


# =============================================================================
# Examples
# =============================================================================


async def example_1_for_loop():
    """Generator yield — Xử lý tuần tự từng item (thay ForOp)."""
    print("=" * 50)
    print("Ví dụ 1: Generator yield (sequential, thay ForOp)")
    print("=" * 50)

    with GraphOp(name="for-loop-demo") as graph:
        src = each_item(items=PARENT["items"], prefix=PARENT["prefix"])
        step = process_item(item=src["item"], prefix=src["prefix"])
        START >> src >> step >> END

    engine = Hush(graph)
    result = await engine.run(
        inputs={
            "items": ["apple", "banana", "cherry"],
            "prefix": "Fruit",
        }
    )

    print(f"  Results: {result['result']}")
    # ['Fruit: apple', 'Fruit: banana', 'Fruit: cherry']


async def example_2_map_op():
    """Generator yield — Scheduler tự động song song hóa (thay MapOp)."""
    print()
    print("=" * 50)
    print("Ví dụ 2: Generator yield (parallel, thay MapOp)")
    print("=" * 50)

    with GraphOp(name="map-op-demo") as graph:
        src = each_number(numbers=PARENT["numbers"])
        step = square(x=src["x"])
        START >> src >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"numbers": [1, 2, 3, 4, 5]})

    print("  Input:   [1, 2, 3, 4, 5]")
    print(f"  Squared: {result['squared']}")
    # [1, 4, 9, 16, 25]


async def example_3_while_loop():
    """Generator while — Loop cho đến khi điều kiện dừng (thay WhileOp)."""
    print()
    print("=" * 50)
    print("Ví dụ 3: Generator while (conditional, thay WhileOp)")
    print("=" * 50)

    with GraphOp(name="while-loop-demo") as graph:
        src = halve_until(value=PARENT["start_value"])
        START >> src >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"start_value": 256})

    print("  Start: 256")
    print(f"  Values: {result['value']}")
    # [128, 64, 32, 16, 8, 4, 2] — mỗi yield tạo 1 item trong list


async def example_4_branch_op():
    """if_() — Routing theo điều kiện."""
    print()
    print("=" * 50)
    print("Ví dụ 4: if_() (conditional routing)")
    print("=" * 50)

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
        # Soft edge (~) vì chỉ 1 nhánh chạy
        [ex, gd, av, fl] >> ~END

    engine = Hush(graph)

    for score in [95, 75, 55, 30]:
        result = await engine.run(inputs={"score": score})
        print(f"  Score {score}: {result['grade']} — {result['message']}")


async def example_5_nested_loops():
    """Nested generators — Loop trong loop (thay nested ForOp)."""
    print()
    print("=" * 50)
    print("Ví dụ 5: Nested Generators (thay Nested ForLoops)")
    print("=" * 50)

    @op
    def outer_iter(xs: list):
        """Yield từng x — tạo outer stream."""
        for x in xs:
            yield {"x": x}

    @op
    def inner_iter(ys: list, x: int):
        """Yield từng y kết hợp với x — tạo inner stream."""
        for y in ys:
            yield {"product": x * y}

    with GraphOp(name="nested-loops") as graph:
        outer = outer_iter(xs=PARENT["xs"])
        inner = inner_iter(ys=PARENT["ys"], x=outer["x"])
        START >> outer >> inner >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"xs": [2, 3, 4], "ys": [10, 20, 30]})

    print("  Outer [2,3,4] x Inner [10,20,30]:")
    print(f"  Products (flattened): {result['product']}")
    # [20, 40, 60, 30, 60, 90, 40, 80, 120] (flattened)


async def main():
    await example_1_for_loop()
    await example_2_map_op()
    await example_3_while_loop()
    await example_4_branch_op()
    await example_5_nested_loops()


if __name__ == "__main__":
    asyncio.run(main())
