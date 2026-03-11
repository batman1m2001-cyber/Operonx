"""Tutorial 16: @graph — Modular, reusable workflow components.

Khong can API key. Chi dung hush-core.

Hoc duoc:
- @graph: decorator tao reusable GraphOp tu function
- Function params tu dong tro thanh PARENT refs
- Auto-naming tu variable name
- Chain graphs (chuoi nhieu graph noi tiep)
- Output renaming trong graph
- Graph long nhau (nested)

Chay: cd tutorial && uv run python examples/16_graph.py
"""

import asyncio

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops.graph.graph_op import graph
from hush.core.ops.transform.func_op import op

# =============================================================================
# Dinh nghia code ops
# =============================================================================


@op
def double(x: int):
    """Nhan doi gia tri."""
    return {"result": x * 2}


@op
def add(a: int, b: int):
    """Cong hai so."""
    return {"result": a + b}


@op
def format_result(value: int, label: str):
    """Format ket qua voi label."""
    return {"formatted": f"{label}: {value}"}


# =============================================================================
# Vi du 1: @graph co ban
# =============================================================================


@graph
def double_flow(val):
    """Graph nhan doi mot gia tri.

    `val` tu dong tro thanh PARENT["val"] (injected boi @graph).
    """
    step = double(x=val)
    START >> step >> END  # Auto-forward outputs len PARENT


async def example_1_basic():
    """@graph co ban — auto-naming va >> END auto-forward."""
    print("=" * 60)
    print("Vi du 1: @graph co ban")
    print("=" * 60)

    with GraphOp(name="basic-demo") as graph:
        d = double_flow(val=PARENT["input"])  # d.name == "d" (auto-named)
        START >> d >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"input": 5})

    print("  Input:  5")
    print(f"  Output: {result['result']}")  # 10
    print(f"  Op name: '{d.name}' (auto-named tu variable)")


# =============================================================================
# Vi du 2: Chain graphs
# =============================================================================


async def example_2_chained():
    """Chain nhieu @graph noi tiep nhau."""
    print()
    print("=" * 60)
    print("Vi du 2: Chain graphs")
    print("=" * 60)

    with GraphOp(name="chain-demo") as graph:
        d1 = double_flow(val=PARENT["input"])  # 3 * 2 = 6
        d2 = double_flow(val=d1["result"])  # 6 * 2 = 12
        d3 = double_flow(val=d2["result"])  # 12 * 2 = 24
        START >> d1 >> d2 >> d3 >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"input": 3})

    print("  Input:  3")
    print("  Chain:  3 -> 6 -> 12 -> 24")
    print(f"  Output: {result['result']}")  # 24


# =============================================================================
# Vi du 3: Output renaming
# =============================================================================


@graph
def double_renamed(val):
    """Graph voi output duoc rename."""
    step = double(x=val)
    step["result"] >> PARENT["doubled"]  # Rename: result -> doubled
    START >> step >> END


async def example_3_renamed_outputs():
    """Rename outputs trong graph va outer graph."""
    print()
    print("=" * 60)
    print("Vi du 3: Output renaming")
    print("=" * 60)

    with GraphOp(name="rename-demo") as graph:
        d = double_renamed(val=PARENT["input"])
        d["doubled"] >> PARENT["answer"]  # Map: doubled -> answer
        START >> d >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"input": 7})

    print("  Input:  7")
    print("  Mapping: step['result'] -> PARENT['doubled'] -> PARENT['answer']")
    print(f"  Output: result['answer'] = {result['answer']}")  # 14


# =============================================================================
# Vi du 4: @graph voi nhieu tham so
# =============================================================================


@graph
def add_and_double(a, b):
    """Cong hai so roi nhan doi ket qua."""
    s = add(a=a, b=b)
    d = double(x=s["result"])
    START >> s >> d >> END


async def example_4_multi_params():
    """@graph nhan nhieu tham so."""
    print()
    print("=" * 60)
    print("Vi du 4: @graph voi nhieu tham so")
    print("=" * 60)

    with GraphOp(name="multi-param-demo") as graph:
        calc = add_and_double(a=PARENT["x"], b=PARENT["y"])
        START >> calc >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"x": 3, "y": 7})

    print("  Input:  x=3, y=7")
    print("  Logic:  (3 + 7) * 2 = 20")
    print(f"  Output: {result['result']}")  # 20


# =============================================================================
# Vi du 5: @graph long nhau (nested)
# =============================================================================


@graph
def quad_flow(val):
    """Nhan bon — dung double_flow hai lan."""
    d1 = double_flow(val=val)
    d2 = double_flow(val=d1["result"])
    START >> d1 >> d2 >> END


async def example_5_nested():
    """@graph chua graph khac."""
    print()
    print("=" * 60)
    print("Vi du 5: Nested graphs")
    print("=" * 60)

    with GraphOp(name="nested-demo") as graph:
        q = quad_flow(val=PARENT["input"])
        START >> q >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"input": 5})

    print("  Input:  5")
    print("  Logic:  quad_flow = double_flow(double_flow(5))")
    print("  Chain:  5 -> 10 -> 20")
    print(f"  Output: {result['result']}")  # 20


# =============================================================================
# Vi du 6: Explicit name va outputs
# =============================================================================


async def example_6_explicit_config():
    """@graph voi explicit name va outputs."""
    print()
    print("=" * 60)
    print("Vi du 6: Explicit name va outputs")
    print("=" * 60)

    with GraphOp(name="config-demo") as graph:
        # Explicit name
        d = double_flow(val=PARENT["input"], name="my_doubler")
        START >> d >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"input": 8})

    print("  Input:  8")
    print(f"  Op name: '{d.name}' (explicit, khong auto-named)")
    print(f"  Output: {result['result']}")  # 16


# =============================================================================
# Vi du 7: So sanh verbose vs @graph
# =============================================================================


async def example_7_comparison():
    """So sanh cach viet verbose va @graph."""
    print()
    print("=" * 60)
    print("Vi du 7: Verbose vs @graph")
    print("=" * 60)

    # --- Verbose: tao GraphOp thu cong ---
    with GraphOp(name="verbose") as graph1:
        with GraphOp(name="double_sub", inputs={"val": PARENT["input"]}) as sub:
            step = double(x=PARENT["val"])
            START >> step >> END
        START >> sub >> END

    # --- Shorthand: @graph ---
    with GraphOp(name="shorthand") as graph2:
        d = double_flow(val=PARENT["input"])
        START >> d >> END

    result1 = await Hush(graph1).run(inputs={"input": 6})
    result2 = await Hush(graph2).run(inputs={"input": 6})

    print("  Verbose:")
    print("    with GraphOp(name='double_sub', inputs={'val': ...}) as sub:")
    print("        step = double(x=PARENT['val'])")
    print("        START >> step >> END")
    print("    START >> sub >> END")
    print()
    print("  @graph:")
    print("    d = double_flow(val=PARENT['input'])")
    print("    START >> d >> END")
    print()
    print(f"  Verbose result:   {result1['result']}")
    print(f"  Shorthand result: {result2['result']}")
    print(f"  Same output: {result1['result'] == result2['result']}")


async def main():
    await example_1_basic()
    await example_2_chained()
    await example_3_renamed_outputs()
    await example_4_multi_params()
    await example_5_nested()
    await example_6_explicit_config()
    await example_7_comparison()

    print()
    print("=" * 60)
    print("@graph Summary")
    print("=" * 60)
    print("""
  @graph bien builder function thanh reusable GraphOp factory:

  1. Function params -> PARENT refs (tu dong inject)
  2. Auto-naming tu variable name (d = double_flow(...) -> d.name == "d")
  3. >> END auto-forward outputs len parent graph
  4. Ho tro name=, outputs=, description= kwargs
  5. Co the chain, nest, va tai su dung nhieu lan
    """)


if __name__ == "__main__":
    asyncio.run(main())
