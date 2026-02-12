"""Example demonstrating iteration nodes: ForOp and MapOp.

This example shows:
1. MapOp: Parallel iteration (for independent items)
2. ForOp: Sequential iteration (for dependent iterations or nested loops)
3. WhileOp: Conditional iteration
4. Using >> operator for output mapping
5. Nested graph structures with PARENT references

Key difference:
- MapOp: Runs all iterations in parallel (faster, but no order guarantee)
- ForOp: Runs iterations one at a time (slower, but supports dependencies)
"""

import asyncio

from hush.core import (
    END,
    PARENT,
    START,
    GraphOp,
    StateSchema,
    op,
)
from hush.core.ops.iteration.base import Each
from hush.core.ops.iteration.for_op import ForOp
from hush.core.ops.iteration.map_op import MapOp
from hush.core.ops.iteration.while_op import WhileOp

# ============================================================================
# Example 1: MapOp with WhileLoop Inside (Parallel Outer Loop)
# ============================================================================


@op
def halve(value: int):
    """Divide value by 2."""
    return {"new_value": value // 2}


async def run_mapnode_whileloop_example():
    """MapOp iterating items in parallel, with WhileLoop processing each item.

    MapOp: iterate over [10, 20, 30] (parallel)
    WhileLoop: for each item, divide by 2 until < 5 (sequential within each)

    Expected results:
    - 10 -> 5 -> 2 (stops at 2)
    - 20 -> 10 -> 5 -> 2 (stops at 2)
    - 30 -> 15 -> 7 -> 3 (stops at 3)
    """
    print("=" * 60)
    print("Example 1: MapOp (parallel) + WhileLoop")
    print("=" * 60)
    print()

    # Build the graph
    with GraphOp(name="mapnode_whileloop_graph") as graph:
        # Outer MapOp: iterate over [10, 20, 30] in PARALLEL
        with MapOp(name="outer_map", inputs={"item": Each([10, 20, 30])}) as map_op:
            # Inner WhileLoop: divide by 2 until value < 5
            with WhileOp(
                name="inner_while",
                inputs={"value": PARENT["item"]},
                stop_condition="value < 5",
                max_iterations=5,
            ) as while_loop:
                halve_node = halve(inputs={"value": PARENT["value"]})
                halve_node["new_value"] >> PARENT["value"]
                START >> halve_node >> END

            while_loop["value"] >> PARENT["value"]
            START >> while_loop >> END

        map_op["value"] >> PARENT["results"]
        START >> map_op >> END

    # Build and run
    graph.build()
    schema = StateSchema(graph)
    state = schema.create_state(inputs={})

    print("Input: [10, 20, 30]")
    print("Operation: Divide each by 2 repeatedly until < 5 (parallel)")
    print()

    result = await graph.run(state)

    print("Processing steps (run in parallel):")
    print("  10 -> 5 -> 2 (stops at 2)")
    print("  20 -> 10 -> 5 -> 2 (stops at 2)")
    print("  30 -> 15 -> 7 -> 3 (stops at 3)")
    print()
    print(f"Results: {result.get('results', 'N/A')}")
    print("Expected: [2, 2, 3]")
    print()

    return result


# ============================================================================
# Example 2: Nested ForOp (Sequential Nested Loops)
# ============================================================================


@op
def multiply(x: int, y: int):
    """Multiply two numbers."""
    return {"result": x * y}


async def run_nested_forloop_example():
    """Nested ForOp - inner loop depends on outer loop variable.

    This pattern REQUIRES ForOp (sequential) because MapOp (parallel)
    would cause race conditions when inner loop depends on outer loop variables.

    Outer ForLoop: iterate over [1, 2, 3] (sequential)
    Inner ForLoop: for each x, iterate over [10, 20] and multiply by x
    """
    print("=" * 60)
    print("Example 2: Nested ForOp (Sequential)")
    print("=" * 60)
    print()

    with ForOp(name="outer_loop", inputs={"x": Each([1, 2, 3])}) as outer:
        with ForOp(
            name="inner_loop",
            inputs={
                "y": Each([10, 20]),
                "x": PARENT["x"],  # Pass outer variable to inner loop
            },
        ) as inner:
            node = multiply(inputs={"x": PARENT["x"], "y": PARENT["y"]}, outputs={"*": PARENT})
            START >> node >> END

        inner["result"] >> PARENT["results"]
        START >> inner >> END

    outer.build()
    schema = StateSchema(outer)
    state = schema.create_state(inputs={})

    print("Outer loop: [1, 2, 3]")
    print("Inner loop: [10, 20] for each outer value")
    print("Operation: multiply(x, y)")
    print()

    result = await outer.run(state)

    print("Processing steps (sequential):")
    print("  x=1: [1*10, 1*20] = [10, 20]")
    print("  x=2: [2*10, 2*20] = [20, 40]")
    print("  x=3: [3*10, 3*20] = [30, 60]")
    print()
    print(f"Results: {result.get('results', 'N/A')}")
    print("Expected: [[10, 20], [20, 40], [30, 60]]")
    print()

    return result


# ============================================================================
# Main
# ============================================================================


async def main():
    """Run all examples."""
    await run_mapnode_whileloop_example()
    print("\n")
    await run_nested_forloop_example()


if __name__ == "__main__":
    asyncio.run(main())
