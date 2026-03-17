"""13 @graph — Modular, reusable workflow components.

No API key required. Uses hush-icore only.

Learn:
- @graph: decorator to create reusable GraphOp from function
- Function params auto-become PARENT refs
- Auto-naming from variable name
- Chain graphs, nest graphs, output renaming

Chạy: cd examples && uv run python ex13_graph/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import sys

from hush.core import END, PARENT, START, GraphOp, Hush

from ex13_graph.workflow import add_and_double, double_flow, double_renamed, quad_flow

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def example_1_basic():
    """@graph cơ bản — auto-naming và >> END auto-forward."""
    print("=" * 55)
    print("1. @graph cơ bản")
    print("=" * 55)

    with GraphOp(name="basic-demo") as g:
        d = double_flow(val=PARENT["input"])
        START >> d >> END

    result = await Hush(g).run(inputs={"input": 5})
    print(f"  Input: 5 → Output: {result['result']}")
    print(f"  Op name: '{d.name}' (auto-named from variable)")


async def example_2_chained():
    """Chain nhiều @graph nối tiếp."""
    print()
    print("=" * 55)
    print("2. Chain graphs (3 → 6 → 12 → 24)")
    print("=" * 55)

    with GraphOp(name="chain-demo") as g:
        d1 = double_flow(val=PARENT["input"])
        d2 = double_flow(val=d1["result"])
        d3 = double_flow(val=d2["result"])
        START >> d1 >> d2 >> d3 >> END

    result = await Hush(g).run(inputs={"input": 3})
    print(f"  Input: 3 → Chain: 3→6→12→24 → Output: {result['result']}")


async def example_3_renamed_outputs():
    """Rename outputs trong graph."""
    print()
    print("=" * 55)
    print("3. Output renaming")
    print("=" * 55)

    with GraphOp(name="rename-demo") as g:
        d = double_renamed(val=PARENT["input"])
        d["doubled"] >> PARENT["answer"]
        START >> d >> END

    result = await Hush(g).run(inputs={"input": 7})
    print(f"  Input: 7 → result['answer'] = {result['answer']}")


async def example_4_multi_params():
    """@graph nhận nhiều tham số."""
    print()
    print("=" * 55)
    print("4. Multi params")
    print("=" * 55)

    with GraphOp(name="multi-param") as g:
        calc = add_and_double(a=PARENT["x"], b=PARENT["y"])
        START >> calc >> END

    result = await Hush(g).run(inputs={"x": 3, "y": 7})
    print("  (3 + 7) * 2 = 20")
    print(f"  Result: {result['result']}")


async def example_5_nested():
    """@graph chứa graph khác."""
    print()
    print("=" * 55)
    print("5. Nested graphs (quad = double(double(x)))")
    print("=" * 55)

    with GraphOp(name="nested-demo") as g:
        q = quad_flow(val=PARENT["input"])
        START >> q >> END

    result = await Hush(g).run(inputs={"input": 5})
    print(f"  Input: 5 → 5→10→20 → Output: {result['result']}")


async def main():
    await example_1_basic()
    await example_2_chained()
    await example_3_renamed_outputs()
    await example_4_multi_params()
    await example_5_nested()


if __name__ == "__main__":
    asyncio.run(main())
