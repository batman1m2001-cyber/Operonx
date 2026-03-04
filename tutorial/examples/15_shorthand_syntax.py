"""Tutorial 15: Shorthand Syntax - Write workflows more concisely.

No API key required. Uses hush-core only.

Learn:
- @op decorator: turn function into FuncOp
- ForOp.of(): ForOp classmethod syntax
- MapOp.of(): MapOp classmethod syntax
- WhileOp.of(): WhileOp classmethod syntax
- if_(): BranchOp fluent syntax

Run: cd tutorial && uv run python examples/15_shorthand_syntax.py
"""

import asyncio

from hush.core import END, PARENT, START, FuncOp, GraphOp, Hush

# Op class imports
from hush.core.ops import (
    Each,  # Iteration marker
    ForOp,
    MapOp,
    WhileOp,  # Decorator
    if_,  # Branch shorthand
    op,
)

# =============================================================================
# @op decorator - Turn function into FuncOp
# =============================================================================


@op
def add_prefix(text: str, prefix: str):
    """Add prefix to text."""
    return {"result": f"{prefix}: {text}"}


@op
def square(x: int):
    """Square a number."""
    return {"squared": x * x}


@op
def halve(value: int):
    """Halve the value."""
    return {"new_value": value // 2}


@op
def grade_to_message(grade: str):
    """Convert grade to message."""
    messages = {
        "A": "Excellent!",
        "B": "Good!",
        "C": "Average",
        "F": "Need improvement",
    }
    return {"message": messages.get(grade, "Unknown")}


# =============================================================================
# Examples
# =============================================================================


async def example_1_func_op_decorator():
    """@op decorator - Create op from function."""
    print("=" * 60)
    print("Example 1: @op decorator")
    print("=" * 60)

    with GraphOp(name="decorator-demo") as graph:
        # Shorthand: pass inputs directly instead of inputs={}
        step = add_prefix(
            name="add_prefix",
            text=PARENT["text"],  # Direct input
            prefix=PARENT["prefix"],  # Direct input
            outputs={"result": PARENT["output"]},
        )
        START >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"text": "Hello World", "prefix": "Greeting"})
    print("  Input:  text='Hello World', prefix='Greeting'")
    print(f"  Output: {result['output']}")


async def example_2_for_shorthand():
    """ForOp.of() - ForOp shorthand."""
    print()
    print("=" * 60)
    print("Example 2: ForOp.of() shorthand (sequential iteration)")
    print("=" * 60)

    with GraphOp(name="for-shorthand") as graph:
        # Shorthand: item=Each(...) instead of inputs={"item": Each(...)}
        with ForOp.of(
            item=Each(["apple", "banana", "cherry"]),  # Iterate
            prefix="Fruit",  # Broadcast
        ) as loop:
            step = add_prefix(
                name="process",
                text=PARENT["item"],
                prefix=PARENT["prefix"],
                outputs={"*": PARENT},  # Write all outputs
            )
            START >> step >> END

        loop["result"] >> PARENT["results"]
        START >> loop >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print("  Items:   ['apple', 'banana', 'cherry']")
    print(f"  Results: {result['results']}")


async def example_3_map_shorthand():
    """MapOp.of() - MapOp shorthand with max_concurrency."""
    print()
    print("=" * 60)
    print("Example 3: MapOp.of() shorthand (parallel iteration)")
    print("=" * 60)

    with GraphOp(name="map-shorthand") as graph:
        # Shorthand with config option
        with MapOp.of(
            x=Each([1, 2, 3, 4, 5]),  # Iterate
            max_concurrency=3,  # Config
        ) as loop:
            step = square(name="square", x=PARENT["x"], outputs={"*": PARENT})
            START >> step >> END

        loop["squared"] >> PARENT["results"]
        START >> loop >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print("  Input:   [1, 2, 3, 4, 5]")
    print(f"  Squared: {result['results']}")


async def example_4_while_shorthand():
    """WhileOp.of() - WhileOp shorthand."""
    print()
    print("=" * 60)
    print("Example 4: WhileOp.of() shorthand (conditional loop)")
    print("=" * 60)

    with GraphOp(name="while-shorthand") as graph:
        # Shorthand: value=256 instead of inputs={"value": 256}
        with WhileOp.of(
            value=256,
            until="value < 10",  # Stop when value < 10
            max_iterations=20,
        ) as loop:
            step = halve(name="halve", value=PARENT["value"])
            step["new_value"] >> PARENT["value"]
            START >> step >> END

        loop["value"] >> PARENT["final"]
        START >> loop >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print("  Start: 256")
    print("  Path:  256 -> 128 -> 64 -> 32 -> 16 -> 8")
    print(f"  Final: {result['final']} (stopped because < 10)")


async def example_5_if_shorthand():
    """if_() - BranchOp fluent shorthand."""
    print()
    print("=" * 60)
    print("Example 5: if_() shorthand (conditional routing)")
    print("=" * 60)

    with GraphOp(name="if-shorthand") as graph:
        # Fluent chaining syntax
        grade_router = (
            if_(PARENT["score"] >= 90, "excellent")
            .if_(PARENT["score"] >= 70, "good")
            .if_(PARENT["score"] >= 50, "average")
            .else_("fail")
        )

        excellent = FuncOp(
            name="excellent", code_fn=lambda: {"grade": "A"}, outputs={"grade": PARENT}
        )
        good = FuncOp(name="good", code_fn=lambda: {"grade": "B"}, outputs={"grade": PARENT})
        average = FuncOp(name="average", code_fn=lambda: {"grade": "C"}, outputs={"grade": PARENT})
        fail = FuncOp(name="fail", code_fn=lambda: {"grade": "F"}, outputs={"grade": PARENT})

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

    @op
    def multiply(x: int, y: int):
        return {"product": x * y}

    @op
    def sum_list(numbers: list):
        return {"total": sum(numbers) if numbers else 0}

    with GraphOp(name="combined-demo") as graph:
        # Nested loops with shorthand
        with ForOp.of(outer=Each([2, 3, 4])) as outer_loop:
            with MapOp.of(
                inner=Each([10, 20, 30]), multiplier=PARENT["outer"], max_concurrency=3
            ) as inner_loop:
                calc = multiply(
                    name="calc",
                    x=PARENT["inner"],
                    y=PARENT["multiplier"],
                    outputs={"*": PARENT},
                )
                START >> calc >> END

            total = sum_list(name="sum", numbers=inner_loop["product"], outputs={"*": PARENT})
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
    from hush.core.ops.iteration import ForOp

    with GraphOp(name="verbose-style") as graph1:
        with ForOp(name="loop", inputs={"x": Each([1, 2, 3]), "multiplier": 10}) as loop:
            step = FuncOp(
                name="calc",
                code_fn=lambda x, multiplier: {"result": x * multiplier},
                inputs={"x": PARENT["x"], "multiplier": PARENT["multiplier"]},
                outputs={"result": PARENT},
            )
            START >> step >> END

        loop["result"] >> PARENT["results"]
        START >> loop >> END

    # --- Shorthand version ---
    @op
    def calc(x: int, multiplier: int):
        return {"result": x * multiplier}

    with GraphOp(name="shorthand-style") as graph2:
        with ForOp.of(x=Each([1, 2, 3]), multiplier=10) as loop:
            step = calc(
                name="calc",
                x=PARENT["x"],
                multiplier=PARENT["multiplier"],
                outputs={"*": PARENT},
            )
            START >> step >> END

        loop["result"] >> PARENT["results"]
        START >> loop >> END

    # Run both
    result1 = await Hush(graph1).run(inputs={})
    result2 = await Hush(graph2).run(inputs={})

    print("  Verbose style:   ForOp(name=..., inputs={...})")
    print("  .of() style:     ForOp.of(x=Each(...), multiplier=10)")
    print()
    print(f"  Verbose result:   {result1['results']}")
    print(f"  Shorthand result: {result2['results']}")
    print(f"  Same output: {result1['results'] == result2['results']}")


async def main():
    await example_1_func_op_decorator()
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
  | Full Class        | .of() Syntax          |
  |-------------------|-----------------------|
  | FuncOp          | @op            |
  | ForOp       | ForOp.of()      |
  | MapOp           | MapOp.of()          |
  | WhileOp     | WhileOp.of()    |
  | BranchOp        | if_().else_()         |
    """)


if __name__ == "__main__":
    asyncio.run(main())
