"""Example demonstrating iteration nodes: ForLoopNode and MapNode.

This example shows:
1. MapNode: Parallel iteration (for independent items)
2. ForLoopNode: Sequential iteration (for dependent iterations or nested loops)
3. WhileLoopNode: Conditional iteration
4. Using >> operator for output mapping
5. Nested graph structures with PARENT references

Key difference:
- MapNode: Runs all iterations in parallel (faster, but no order guarantee)
- ForLoopNode: Runs iterations one at a time (slower, but supports dependencies)
"""

import asyncio

from hush.core import (
    GraphNode,
    code_node,
    START, END, PARENT,
    StateSchema,
)
from hush.core.nodes.iteration.for_loop_node import ForLoopNode
from hush.core.nodes.iteration.map_node import MapNode
from hush.core.nodes.iteration.while_loop_node import WhileLoopNode
from hush.core.nodes.iteration.base import Each


# ============================================================================
# Example 1: MapNode with WhileLoop Inside (Parallel Outer Loop)
# ============================================================================

@code_node
def halve(value: int):
    """Divide value by 2."""
    return {"new_value": value // 2}


async def run_mapnode_whileloop_example():
    """MapNode iterating items in parallel, with WhileLoop processing each item.

    MapNode: iterate over [10, 20, 30] (parallel)
    WhileLoop: for each item, divide by 2 until < 5 (sequential within each)

    Expected results:
    - 10 -> 5 -> 2 (stops at 2)
    - 20 -> 10 -> 5 -> 2 (stops at 2)
    - 30 -> 15 -> 7 -> 3 (stops at 3)
    """
    print("=" * 60)
    print("Example 1: MapNode (parallel) + WhileLoop")
    print("=" * 60)
    print()

    # Build the graph
    with GraphNode(name="mapnode_whileloop_graph") as graph:
        # Outer MapNode: iterate over [10, 20, 30] in PARALLEL
        with MapNode(
            name="outer_map",
            inputs={"item": Each([10, 20, 30])}
        ) as map_node:
            # Inner WhileLoop: divide by 2 until value < 5
            with WhileLoopNode(
                name="inner_while",
                inputs={"value": PARENT["item"]},
                stop_condition="value < 5",
                max_iterations=5
            ) as while_loop:
                halve_node = halve(inputs={"value": PARENT["value"]})
                halve_node["new_value"] >> PARENT["value"]
                START >> halve_node >> END

            while_loop["value"] >> PARENT["value"]
            START >> while_loop >> END

        map_node["value"] >> PARENT["results"]
        START >> map_node >> END

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
    print(f"Expected: [2, 2, 3]")
    print()

    return result


# ============================================================================
# Example 2: Nested ForLoopNode (Sequential Nested Loops)
# ============================================================================

@code_node
def multiply(x: int, y: int):
    """Multiply two numbers."""
    return {"result": x * y}


async def run_nested_forloop_example():
    """Nested ForLoopNode - inner loop depends on outer loop variable.

    This pattern REQUIRES ForLoopNode (sequential) because MapNode (parallel)
    would cause race conditions when inner loop depends on outer loop variables.

    Outer ForLoop: iterate over [1, 2, 3] (sequential)
    Inner ForLoop: for each x, iterate over [10, 20] and multiply by x
    """
    print("=" * 60)
    print("Example 2: Nested ForLoopNode (Sequential)")
    print("=" * 60)
    print()

    with ForLoopNode(
        name="outer_loop",
        inputs={"x": Each([1, 2, 3])}
    ) as outer:
        with ForLoopNode(
            name="inner_loop",
            inputs={
                "y": Each([10, 20]),
                "x": PARENT["x"]  # Pass outer variable to inner loop
            }
        ) as inner:
            node = multiply(
                inputs={"x": PARENT["x"], "y": PARENT["y"]},
                outputs={"*": PARENT}
            )
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
    print(f"Expected: [[10, 20], [20, 40], [30, 60]]")
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
