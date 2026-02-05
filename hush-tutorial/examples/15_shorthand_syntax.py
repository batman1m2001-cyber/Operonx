"""Tutorial 15: Shorthand Syntax - Write workflows more concisely.

No API key required. Uses hush-core only.

Learn:
- @code_node decorator: turn function into CodeNode
- for_(): ForLoopNode shorthand
- map_(): MapNode shorthand
- while_(): WhileLoopNode shorthand
- if_(): BranchNode fluent syntax

Run: cd hush-tutorial && uv run python examples/15_shorthand_syntax.py
"""

import asyncio
from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT

# Shorthand imports
from hush.core.nodes import (
    code_node,     # Decorator
    for_, map_, while_,  # Iteration shorthands
    if_,           # Branch shorthand
    Each,          # Iteration marker
)


# =============================================================================
# @code_node decorator - Turn function into CodeNode
# =============================================================================

@code_node
def add_prefix(text: str, prefix: str):
    """Add prefix to text."""
    return {"result": f"{prefix}: {text}"}


@code_node
def square(x: int):
    """Square a number."""
    return {"squared": x * x}


@code_node
def halve(value: int):
    """Halve the value."""
    return {"new_value": value // 2}


@code_node
def grade_to_message(grade: str):
    """Convert grade to message."""
    messages = {"A": "Excellent!", "B": "Good!", "C": "Average", "F": "Need improvement"}
    return {"message": messages.get(grade, "Unknown")}


# =============================================================================
# Examples
# =============================================================================

async def example_1_code_node_decorator():
    """@code_node decorator - Create node from function."""
    print("=" * 60)
    print("Example 1: @code_node decorator")
    print("=" * 60)

    with GraphNode(name="decorator-demo") as graph:
        # Shorthand: pass inputs directly instead of inputs={}
        step = add_prefix(
            name="add_prefix",
            text=PARENT["text"],      # Direct input
            prefix=PARENT["prefix"],  # Direct input
            outputs={"result": PARENT["output"]}
        )
        START >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"text": "Hello World", "prefix": "Greeting"})
    print(f"  Input:  text='Hello World', prefix='Greeting'")
    print(f"  Output: {result['output']}")


async def example_2_for_shorthand():
    """for_() - ForLoopNode shorthand."""
    print()
    print("=" * 60)
    print("Example 2: for_() shorthand (sequential iteration)")
    print("=" * 60)

    with GraphNode(name="for-shorthand") as graph:
        # Shorthand: item=Each(...) instead of inputs={"item": Each(...)}
        with for_(
            item=Each(["apple", "banana", "cherry"]),  # Iterate
            prefix="Fruit"                              # Broadcast
        ) as loop:
            step = add_prefix(
                name="process",
                text=PARENT["item"],
                prefix=PARENT["prefix"],
                outputs={"*": PARENT}  # Write all outputs
            )
            START >> step >> END

        loop["result"] >> PARENT["results"]
        START >> loop >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print(f"  Items:   ['apple', 'banana', 'cherry']")
    print(f"  Results: {result['results']}")


async def example_3_map_shorthand():
    """map_() - MapNode shorthand with max_concurrency."""
    print()
    print("=" * 60)
    print("Example 3: map_() shorthand (parallel iteration)")
    print("=" * 60)

    with GraphNode(name="map-shorthand") as graph:
        # Shorthand with config option
        with map_(
            x=Each([1, 2, 3, 4, 5]),  # Iterate
            max_concurrency=3          # Config
        ) as loop:
            step = square(
                name="square",
                x=PARENT["x"],
                outputs={"*": PARENT}
            )
            START >> step >> END

        loop["squared"] >> PARENT["results"]
        START >> loop >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print(f"  Input:   [1, 2, 3, 4, 5]")
    print(f"  Squared: {result['results']}")


async def example_4_while_shorthand():
    """while_() - WhileLoopNode shorthand."""
    print()
    print("=" * 60)
    print("Example 4: while_() shorthand (conditional loop)")
    print("=" * 60)

    with GraphNode(name="while-shorthand") as graph:
        # Shorthand: value=256 instead of inputs={"value": 256}
        with while_(
            value=256,
            stop_condition="value < 10",  # Stop when value < 10
            max_iterations=20
        ) as loop:
            step = halve(name="halve", value=PARENT["value"])
            step["new_value"] >> PARENT["value"]
            START >> step >> END

        loop["value"] >> PARENT["final"]
        START >> loop >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print(f"  Start: 256")
    print(f"  Path:  256 -> 128 -> 64 -> 32 -> 16 -> 8")
    print(f"  Final: {result['final']} (stopped because < 10)")


async def example_5_if_shorthand():
    """if_() - BranchNode fluent shorthand."""
    print()
    print("=" * 60)
    print("Example 5: if_() shorthand (conditional routing)")
    print("=" * 60)

    with GraphNode(name="if-shorthand") as graph:
        # Fluent chaining syntax
        grade_router = (if_(PARENT["score"] >= 90, "excellent")
                        .if_(PARENT["score"] >= 70, "good")
                        .if_(PARENT["score"] >= 50, "average")
                        .else_("fail"))

        excellent = CodeNode(name="excellent", code_fn=lambda: {"grade": "A"}, outputs={"grade": PARENT})
        good = CodeNode(name="good", code_fn=lambda: {"grade": "B"}, outputs={"grade": PARENT})
        average = CodeNode(name="average", code_fn=lambda: {"grade": "C"}, outputs={"grade": PARENT})
        fail = CodeNode(name="fail", code_fn=lambda: {"grade": "F"}, outputs={"grade": PARENT})

        # Add message
        msg = grade_to_message(name="msg", grade=PARENT["grade"], outputs={"*": PARENT})

        START >> grade_router >> [excellent, good, average, fail]
        [excellent, good, average, fail] >> ~msg >> END  # Soft edge

    engine = Hush(graph)

    for score in [95, 75, 55, 30]:
        result = await engine.run(inputs={"score": score})
        print(f"  Score {score}: Grade {result['grade']} - {result['message']}")


async def example_6_combined():
    """Combine multiple shorthands in one workflow."""
    print()
    print("=" * 60)
    print("Example 6: Combined shorthand syntax")
    print("=" * 60)

    @code_node
    def multiply(x: int, y: int):
        return {"product": x * y}

    @code_node
    def sum_list(numbers: list):
        return {"total": sum(numbers) if numbers else 0}

    with GraphNode(name="combined-demo") as graph:
        # Nested loops with shorthand
        with for_(outer=Each([2, 3, 4])) as outer_loop:
            with map_(
                inner=Each([10, 20, 30]),
                multiplier=PARENT["outer"],
                max_concurrency=3
            ) as inner_loop:
                calc = multiply(
                    name="calc",
                    x=PARENT["inner"],
                    y=PARENT["multiplier"],
                    outputs={"*": PARENT}
                )
                START >> calc >> END

            total = sum_list(
                name="sum",
                numbers=inner_loop["product"],
                outputs={"*": PARENT}
            )
            START >> inner_loop >> total >> END

        outer_loop["total"] >> PARENT["results"]
        START >> outer_loop >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print("  Calculation: outer [2,3,4] x inner [10,20,30]")
    print(f"  Results: {result['results']}")
    print("  Explanation:")
    print("    2 x (10+20+30) = 2 x 60 = 120")
    print("    3 x (10+20+30) = 3 x 60 = 180")
    print("    4 x (10+20+30) = 4 x 60 = 240")


async def example_7_comparison():
    """Compare verbose vs shorthand."""
    print()
    print("=" * 60)
    print("Example 7: Verbose vs Shorthand comparison")
    print("=" * 60)

    # --- Verbose version ---
    from hush.core.nodes.iteration import ForLoopNode

    with GraphNode(name="verbose-style") as graph1:
        with ForLoopNode(
            name="loop",
            inputs={
                "x": Each([1, 2, 3]),
                "multiplier": 10
            }
        ) as loop:
            step = CodeNode(
                name="calc",
                code_fn=lambda x, multiplier: {"result": x * multiplier},
                inputs={"x": PARENT["x"], "multiplier": PARENT["multiplier"]},
                outputs={"result": PARENT}
            )
            START >> step >> END

        loop["result"] >> PARENT["results"]
        START >> loop >> END

    # --- Shorthand version ---
    @code_node
    def calc(x: int, multiplier: int):
        return {"result": x * multiplier}

    with GraphNode(name="shorthand-style") as graph2:
        with for_(x=Each([1, 2, 3]), multiplier=10) as loop:
            step = calc(name="calc", x=PARENT["x"], multiplier=PARENT["multiplier"], outputs={"*": PARENT})
            START >> step >> END

        loop["result"] >> PARENT["results"]
        START >> loop >> END

    # Run both
    result1 = await Hush(graph1).run(inputs={})
    result2 = await Hush(graph2).run(inputs={})

    print("  Verbose style:   ForLoopNode(inputs={...})")
    print("  Shorthand style: for_(x=Each(...), multiplier=10)")
    print()
    print(f"  Verbose result:   {result1['results']}")
    print(f"  Shorthand result: {result2['results']}")
    print(f"  Same output: {result1['results'] == result2['results']}")


async def main():
    await example_1_code_node_decorator()
    await example_2_for_shorthand()
    await example_3_map_shorthand()
    await example_4_while_shorthand()
    await example_5_if_shorthand()
    await example_6_combined()
    await example_7_comparison()

    print()
    print("=" * 60)
    print("Shorthand Syntax Summary")
    print("=" * 60)
    print("""
  | Full Class        | Shorthand      |
  |-------------------|----------------|
  | CodeNode          | @code_node     |
  | ForLoopNode       | for_()         |
  | MapNode           | map_()         |
  | WhileLoopNode     | while_()       |
  | BranchNode        | if_().else_()  |
    """)


if __name__ == "__main__":
    asyncio.run(main())
