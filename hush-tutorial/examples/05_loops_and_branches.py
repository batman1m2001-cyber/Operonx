"""Tutorial 05: Loops và Branches — Điều khiển luồng workflow.

Không cần API key. Chỉ dùng hush-core.

Học được:
- ForLoopNode: iterate tuần tự
- MapNode: iterate song song (parallel)
- WhileLoopNode: loop với điều kiện
- if_(): routing có điều kiện
- Each(): đánh dấu biến để iterate
- Soft edge (~): merge sau branch

Chạy: cd hush-tutorial && uv run python examples/05_loops_and_branches.py
"""

import asyncio

from hush.core import END, PARENT, START, GraphNode, Hush
from hush.core.nodes import Each, code_node, for_, if_, map_, while_

# =============================================================================
# Code nodes dùng @code_node decorator (gọn hơn CodeNode class)
# =============================================================================


@code_node
def process_item(item: str, prefix: str):
    """Xử lý 1 item."""
    return {"result": f"{prefix}: {item}"}


@code_node
def square(x: int):
    """Bình phương số."""
    return {"squared": x * x}


@code_node
def halve_value(value: int):
    """Chia đôi giá trị."""
    return {"new_value": value // 2}


# =============================================================================
# Examples
# =============================================================================


async def example_1_for_loop():
    """ForLoopNode — Xử lý tuần tự từng item."""
    print("=" * 50)
    print("Ví dụ 1: ForLoopNode (sequential)")
    print("=" * 50)

    with GraphNode(name="for-loop-demo") as graph:
        with for_(
            item=Each(PARENT["items"]),  # Iterate qua mỗi item
            prefix=PARENT["prefix"],  # Broadcast cho tất cả iterations
        ) as loop:
            step = process_item(
                name="process",
                inputs={"item": PARENT["item"], "prefix": PARENT["prefix"]},
                outputs={"*": PARENT},
            )
            START >> step >> END

        loop["result"] >> PARENT["results"]
        START >> loop >> END

    engine = Hush(graph)
    result = await engine.run(
        inputs={
            "items": ["apple", "banana", "cherry"],
            "prefix": "Fruit",
        }
    )

    print(f"  Results: {result['results']}")
    # ['Fruit: apple', 'Fruit: banana', 'Fruit: cherry']


async def example_2_map_node():
    """MapNode — Xử lý song song, có giới hạn concurrency."""
    print()
    print("=" * 50)
    print("Ví dụ 2: MapNode (parallel)")
    print("=" * 50)

    with GraphNode(name="map-node-demo") as graph:
        with map_(
            x=Each(PARENT["numbers"]),
            max_concurrency=3,  # Tối đa 3 tasks cùng lúc
        ) as map_node:
            step = square(
                name="square",
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},
            )
            START >> step >> END

        map_node["squared"] >> PARENT["results"]
        START >> map_node >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"numbers": [1, 2, 3, 4, 5]})

    print("  Input:   [1, 2, 3, 4, 5]")
    print(f"  Squared: {result['results']}")
    # [1, 4, 9, 16, 25]


async def example_3_while_loop():
    """WhileLoopNode — Loop cho đến khi điều kiện dừng."""
    print()
    print("=" * 50)
    print("Ví dụ 3: WhileLoopNode (conditional)")
    print("=" * 50)

    with GraphNode(name="while-loop-demo") as graph:
        with while_(
            value=PARENT["start_value"],
            stop_condition="value < 5",
            max_iterations=20,
        ) as while_loop:
            step = halve_value(
                name="halve",
                inputs={"value": PARENT["value"]},
            )
            step["new_value"] >> PARENT["value"]
            START >> step >> END

        while_loop["value"] >> PARENT["final_value"]
        START >> while_loop >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"start_value": 256})

    print("  Start: 256")
    print(f"  Final: {result['final_value']}")
    # 256 → 128 → 64 → 32 → 16 → 8 → 4 (dừng vì < 5)


async def example_4_branch_node():
    """if_() — Routing theo điều kiện."""
    print()
    print("=" * 50)
    print("Ví dụ 4: if_() (conditional routing)")
    print("=" * 50)

    @code_node
    def excellent():
        return {"grade": "A", "message": "Xuất sắc!"}

    @code_node
    def good():
        return {"grade": "B", "message": "Tốt!"}

    @code_node
    def average():
        return {"grade": "C", "message": "Trung bình"}

    @code_node
    def fail():
        return {"grade": "F", "message": "Cần cải thiện"}

    with GraphNode(name="grade-workflow") as graph:
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
    """Nested ForLoops — Loop trong loop."""
    print()
    print("=" * 50)
    print("Ví dụ 5: Nested Loops")
    print("=" * 50)

    @code_node
    def multiply(x: int, y: int):
        return {"product": x * y}

    @code_node
    def summarize(products: list):
        return {"total": sum(products) if products else 0}

    with GraphNode(name="nested-loops") as graph:
        with for_(x=Each([2, 3, 4])) as outer:
            with for_(y=Each([10, 20, 30]), x=PARENT["x"]) as inner:
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

        outer["total"] >> PARENT["results"]
        START >> outer >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print("  Outer [2,3,4] x Inner [10,20,30]:")
    print(f"  Totals per outer: {result['results']}")
    # [120, 180, 240] = [2*(10+20+30), 3*(10+20+30), 4*(10+20+30)]


async def main():
    await example_1_for_loop()
    await example_2_map_node()
    await example_3_while_loop()
    await example_4_branch_node()
    await example_5_nested_loops()


if __name__ == "__main__":
    asyncio.run(main())
