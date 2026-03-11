"""Tutorial 15: Shorthand Syntax - Write workflows more concisely.

No API key required. Uses hush-core only.

Learn:
- @op decorator: turn function into FuncOp
- Generator @op: iterate with yield (replaces ForOp, MapOp, WhileOp)
- Broadcast: non-generator ops broadcast outputs to stream items
- if_(): BranchOp fluent syntax
- Combining generators for nested iteration

Run: uv run python examples/15_shorthand_syntax.py
"""

import asyncio

from hush.core import END, PARENT, START, FuncOp, GraphOp, Hush
from hush.core.ops import if_, op


# =============================================================================
# @op decorator - Turn function into FuncOp (batch or generator)
# =============================================================================


@op(rust="./rust_ops::text::add_prefix")
def add_prefix(text: str, prefix: str):
    """Add prefix to text."""
    return {"result": f"{prefix}: {text}"}


@op(rust="./rust_ops::math::square_named")
def square(x: int):
    """Square a number."""
    return {"squared": x * x}


@op(rust="./rust_ops::text::grade_to_message")
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
# Generator ops - yield items for streaming iteration
# =============================================================================


@op(rust="./rust_ops::iteration::each_fruit")
def each_fruit():
    """Yield fruits one at a time (replaces ForOp with static list)."""
    for fruit in ["apple", "banana", "cherry"]:
        yield {"item": fruit}


@op(rust="./rust_ops::iteration::each_number")
def each_number(numbers: list):
    """Yield numbers from a list (replaces ForOp with dynamic input)."""
    for n in numbers:
        yield {"x": n}


@op(rust="./rust_ops::iteration::each_value")
def each_item(items: list):
    """Generic item iterator."""
    for item in items:
        yield {"value": item}


@op(rust="./rust_ops::iteration::halve_until_threshold")
def halve_until(value: int, threshold: int):
    """Halve value until below threshold (replaces WhileOp)."""
    while value >= threshold:
        value = value // 2
        yield {"value": value}


# =============================================================================
# Examples
# =============================================================================


async def example_1_func_op_decorator():
    """@op decorator - Create op from function."""
    print("=" * 60)
    print("Example 1: @op decorator (batch op)")
    print("=" * 60)

    with GraphOp(name="decorator-demo") as graph:
        step = add_prefix(
            name="add_prefix",
            text=PARENT["text"],
            prefix=PARENT["prefix"],
            outputs={"result": PARENT["output"]},
        )
        START >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"text": "Hello World", "prefix": "Greeting"})
    print("  Input:  text='Hello World', prefix='Greeting'")
    print(f"  Output: {result['output']}")


async def example_2_generator_sequential():
    """Generator op for sequential iteration (replaces ForOp)."""
    print()
    print("=" * 60)
    print("Example 2: Generator @op (sequential iteration)")
    print("=" * 60)

    # A generator op yields items one by one.
    # Downstream ops run once per yielded item.
    # Results are auto-collected as lists.

    with GraphOp(name="generator-sequential") as graph:
        src = each_fruit()
        step = add_prefix(
            name="process",
            text=src["item"],
            prefix="Fruit",
            outputs={"*": PARENT},
        )
        START >> src >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print("  Yielded: 'apple', 'banana', 'cherry'")
    print(f"  Results: {result['result']}")


async def example_3_generator_parallel():
    """Generator op with parallel downstream (replaces MapOp)."""
    print()
    print("=" * 60)
    print("Example 3: Generator @op (parallel iteration)")
    print("=" * 60)

    # The streaming scheduler auto-parallelizes downstream ops.
    # No separate MapOp needed -- same generator pattern, same result.

    with GraphOp(name="generator-parallel") as graph:
        src = each_number(numbers=PARENT["numbers"])
        step = square(name="square", x=src["x"], outputs={"*": PARENT})
        START >> src >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"numbers": [1, 2, 3, 4, 5]})

    print("  Input:   [1, 2, 3, 4, 5]")
    print(f"  Squared: {result['squared']}")


async def example_4_while_generator():
    """Generator with while loop (replaces WhileOp)."""
    print()
    print("=" * 60)
    print("Example 4: While-generator @op (conditional loop)")
    print("=" * 60)

    # Instead of WhileOp with until="condition", just use a while loop
    # inside a generator. The loop condition is plain Python -- no DSL.

    with GraphOp(name="while-generator") as graph:
        loop = halve_until(
            name="halve",
            value=PARENT["start"],
            threshold=PARENT["threshold"],
        )
        loop["value"] >> PARENT["steps"]
        START >> loop >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"start": 256, "threshold": 10})

    print("  Start: 256, threshold: 10")
    print("  Path:  256 -> 128 -> 64 -> 32 -> 16 -> 8")
    print(f"  Steps: {result['steps']}")


async def example_5_if_shorthand():
    """if_() - BranchOp fluent shorthand (unchanged)."""
    print()
    print("=" * 60)
    print("Example 5: if_() shorthand (conditional routing)")
    print("=" * 60)

    with GraphOp(name="if-shorthand") as graph:
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

        msg = grade_to_message(name="msg", grade=PARENT["grade"], outputs={"*": PARENT})

        START >> grade_router >> [excellent, good, average, fail]
        [excellent, good, average, fail] >> ~msg >> END

    engine = Hush(graph)

    for score in [95, 75, 55, 30]:
        result = await engine.run(inputs={"score": score})
        print(f"  Score {score}: Grade {result['grade']} - {result['message']}")


async def example_6_combined():
    """Combine generators for nested iteration."""
    print()
    print("=" * 60)
    print("Example 6: Combined generators (nested iteration + broadcast)")
    print("=" * 60)

    @op(rust="./rust_ops::iteration::each_outer")
    def each_outer(values: list):
        """Outer iterator."""
        for v in values:
            yield {"outer": v}

    @op(rust="./rust_ops::iteration::each_inner")
    def each_inner(values: list):
        """Inner iterator."""
        for v in values:
            yield {"inner": v}

    @op(rust="./rust_ops::math::multiply_xy")
    def multiply(x: int, y: int):
        return {"product": x * y}

    @op(rust="./rust_ops::math::sum_list")
    def sum_list(numbers: list):
        return {"total": sum(numbers) if numbers else 0}

    # For each outer value, multiply by each inner value, then sum.
    # The outer generator yields one item at a time.
    # For each outer item, the inner generator yields its items.
    # multiply runs per inner item with the outer value broadcast.

    with GraphOp(name="combined-demo") as graph:
        with GraphOp(name="outer-loop") as outer_graph:
            outer = each_outer(values=PARENT["outers"])
            with GraphOp(name="inner-loop") as inner_graph:
                inner = each_inner(values=PARENT["inners"])
                calc = multiply(name="calc", x=inner["inner"], y=PARENT["multiplier"])
                START >> inner >> calc >> END

            inner_loop = inner_graph(
                name="inner",
                inners=PARENT["inners"],
                multiplier=outer["outer"],
            )
            total = sum_list(name="sum", numbers=inner_loop["product"], outputs={"*": PARENT})
            START >> outer >> inner_loop >> total >> END

        loop = outer_graph(
            name="loop",
            outers=[2, 3, 4],
            inners=[10, 20, 30],
        )
        loop["total"] >> PARENT["results"]
        START >> loop >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print("  Calculation: outer [2,3,4] x inner [10,20,30]")
    print(f"  Results: {result['results']}")
    print("  Explanation:")
    print("    2 x (10+20+30) = 2 x 60 = 120")
    print("    3 x (10+20+30) = 3 x 60 = 180")
    print("    4 x (10+20+30) = 4 x 60 = 240")


async def example_7_comparison():
    """Compare old verbose style vs new generator style."""
    print()
    print("=" * 60)
    print("Example 7: Old ForOp vs New Generator comparison")
    print("=" * 60)

    # --- Old style (ForOp -- removed) ---
    # with GraphOp(name="old-style") as graph:
    #     with ForOp.of(x=Each([1, 2, 3]), multiplier=10) as loop:
    #         step = calc(x=PARENT["x"], multiplier=PARENT["multiplier"])
    #         START >> step >> END
    #     loop["result"] >> PARENT["results"]
    #     START >> loop >> END

    # --- New style (generator @op) ---
    @op(rust="./rust_ops::iteration::emit_items")
    def emit_items(items: list):
        for item in items:
            yield {"x": item}

    @op(rust="./rust_ops::math::calc")
    def calc(x: int, multiplier: int):
        return {"result": x * multiplier}

    @op(rust="./rust_ops::pipeline::get_config")
    def get_config():
        """Non-generator: runs once, output is broadcast to stream items."""
        return {"multiplier": 10}

    with GraphOp(name="new-style") as graph:
        cfg = get_config()
        src = emit_items(items=PARENT["items"])
        step = calc(name="calc", x=src["x"], multiplier=cfg["multiplier"])
        step["result"] >> PARENT["results"]
        START >> [cfg, src]
        [cfg, src] >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"items": [1, 2, 3]})

    print("  Old: ForOp.of(x=Each([1,2,3]), multiplier=10) + inner graph")
    print("  New: generator @op yield + broadcast from batch op")
    print()
    print(f"  Result: {result['results']}")
    print("  Same output, simpler code, no special loop ops!")


async def main():
    await example_1_func_op_decorator()
    await example_2_generator_sequential()
    await example_3_generator_parallel()
    await example_4_while_generator()
    await example_5_if_shorthand()
    await example_6_combined()
    await example_7_comparison()

    print()
    print("=" * 60)
    print("Shorthand Syntax Summary")
    print("=" * 60)
    print("""
  | Old Pattern          | New Pattern                          |
  |----------------------|--------------------------------------|
  | FuncOp(...)          | @op def func(): return {...}         |
  | ForOp.of(x=Each(..)) | @op def gen(items): yield {...}      |
  | MapOp.of(x=Each(..)) | Same generator (auto-parallelized)   |
  | WhileOp.of(until=..) | @op def gen(): while ...: yield {..} |
  | if_().else_()        | if_().else_()  (unchanged)           |
  | Each(...)            | (not needed -- yield in generator)   |

  Key insight: generators replace ALL loop ops.
  - yield  = emit one item into the stream
  - return = batch op (single result)
  - Broadcast: batch op outputs are available to all stream items
    """)


if __name__ == "__main__":
    asyncio.run(main())
