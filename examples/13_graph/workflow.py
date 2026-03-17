"""Shared workflow definitions for 13_graph.

Defines ops and @graph-decorated reusable graph builders.
No API key required — uses hush-icore only.
"""

from hush.core import END, PARENT, START
from hush.core.ops.graph.graph_op import graph
from hush.core.ops.transform.func_op import op

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
