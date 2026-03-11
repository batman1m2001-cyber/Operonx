"""Shared workflow definitions for 05_loops_and_branches.

Defines generator ops, branch ops, and graph builders.
No API keys required — pure compute.
"""

from hush.core import END, PARENT, START, GraphOp
from hush.core.ops import if_, op


# =============================================================================
# Generator ops (yield-based iteration)
# =============================================================================


@op(rust="./rust_ops::iteration::each_item_with_prefix")
def each_item(items: list, prefix: str):
    """Yield từng item — downstream ops tự động chạy per item."""
    for item in items:
        yield {"item": item, "prefix": prefix}


@op(rust="./rust_ops::text::process_item_text")
def process_item(item: str, prefix: str):
    """Xử lý 1 item."""
    return {"result": f"{prefix}: {item}"}


@op(rust="./rust_ops::iteration::each_number")
def each_number(numbers: list):
    """Yield từng số — scheduler tự động song song hóa."""
    for x in numbers:
        yield {"x": x}


@op(rust="./rust_ops::math::square_named")
def square(x: int):
    """Bình phương số."""
    return {"squared": x * x}


@op(rust="./rust_ops::iteration::halve_until")
def halve_until(value: int):
    """Chia đôi cho đến khi < 5 — while loop trong generator."""
    while value >= 5:
        value = value // 2
        yield {"value": value}


# =============================================================================
# Branch ops
# =============================================================================


@op(rust="./rust_ops::pipeline::excellent")
def excellent():
    return {"grade": "A", "message": "Xuất sắc!"}


@op(rust="./rust_ops::pipeline::good")
def good():
    return {"grade": "B", "message": "Tốt!"}


@op(rust="./rust_ops::pipeline::average_grade")
def average():
    return {"grade": "C", "message": "Trung bình"}


@op(rust="./rust_ops::pipeline::fail_grade")
def fail():
    return {"grade": "F", "message": "Cần cải thiện"}


# =============================================================================
# Graph builders
# =============================================================================


def build_for_loop():
    """Generator yield — sequential iteration (replaces ForOp)."""
    with GraphOp(name="for-loop") as graph:
        src = each_item(items=PARENT["items"], prefix=PARENT["prefix"])
        step = process_item(item=src["item"], prefix=src["prefix"])
        START >> src >> step >> END
    return graph


def build_map_op():
    """Generator yield — parallel map (replaces MapOp)."""
    with GraphOp(name="map-op") as graph:
        src = each_number(numbers=PARENT["numbers"])
        step = square(x=src["x"])
        START >> src >> step >> END
    return graph


def build_while_loop():
    """Generator while — conditional loop (replaces WhileOp)."""
    with GraphOp(name="while-loop") as graph:
        src = halve_until(value=PARENT["start_value"])
        START >> src >> END
    return graph


def build_branch():
    """if_() — conditional routing with soft edges."""
    with GraphOp(name="grade-workflow") as graph:
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
        [ex, gd, av, fl] >> ~END
    return graph
