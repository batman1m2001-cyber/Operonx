"""Shared workflow definitions for ex13_graph.

``@graph``-decorated reusable graph components. No API keys required.
"""

from operon.core import END, PARENT, START, GraphOp, op
from operon.core.ops.graph.graph_op import graph

# =============================================================================
# Ops
# =============================================================================


@op
def double(x: int):
    """Nhân đôi giá trị."""
    return {"result": x * 2}


@op
def add(a: int, b: int):
    """Cộng hai số."""
    return {"result": a + b}


# =============================================================================
# @graph definitions
# =============================================================================


@graph
def double_flow(val):
    """Graph nhân đôi một giá trị."""
    step = double(x=val)
    START >> step >> END


@graph
def double_renamed(val):
    """Graph với output được rename."""
    step = double(x=val)
    step["result"] >> PARENT["doubled"]
    START >> step >> END


@graph
def add_and_double(a, b):
    """Cộng hai số rồi nhân đôi kết quả."""
    s = add(a=a, b=b)
    d = double(x=s["result"])
    START >> s >> d >> END


@graph
def quad_flow(val):
    """Nhân bốn — dùng double_flow hai lần."""
    d1 = double_flow(val=val)
    d2 = double_flow(val=d1["result"])
    START >> d1 >> d2 >> END


# =============================================================================
# Builders that wrap the @graph factories in a top-level GraphOp
# =============================================================================


def build_basic() -> GraphOp:
    """@graph cơ bản — auto-naming và >> END auto-forward."""
    with GraphOp(name="basic-demo") as g:
        d = double_flow(val=PARENT["input"])
        START >> d >> END
    return g


def build_chained() -> GraphOp:
    """Chain nhiều @graph nối tiếp."""
    with GraphOp(name="chain-demo") as g:
        d1 = double_flow(val=PARENT["input"])
        d2 = double_flow(val=d1["result"])
        d3 = double_flow(val=d2["result"])
        START >> d1 >> d2 >> d3 >> END
    return g


def build_renamed() -> GraphOp:
    """Rename outputs trong graph."""
    with GraphOp(name="rename-demo") as g:
        d = double_renamed(val=PARENT["input"])
        d["doubled"] >> PARENT["answer"]
        START >> d >> END
    return g


def build_multi_params() -> GraphOp:
    """@graph nhận nhiều tham số."""
    with GraphOp(name="multi-param") as g:
        calc = add_and_double(a=PARENT["x"], b=PARENT["y"])
        START >> calc >> END
    return g


def build_nested() -> GraphOp:
    """@graph chứa graph khác."""
    with GraphOp(name="nested-demo") as g:
        q = quad_flow(val=PARENT["input"])
        START >> q >> END
    return g
