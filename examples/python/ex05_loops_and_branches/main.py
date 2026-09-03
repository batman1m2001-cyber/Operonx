"""05 Loops & Branches — generator ops + if_() routing, no API keys.

Run from this directory:

    uv sync
    uv run python main.py
"""

from __future__ import annotations

import asyncio

from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.ops.flow.branch_op import if_

# ── Generator ops (yield-based iteration) ───────────────────────────────


@op
def each_item(items: list, prefix: str):
    for item in items:
        yield {"item": item, "prefix": prefix}


@op
def process_item(item: str, prefix: str):
    return {"result": f"{prefix}: {item}"}


@op
def each_number(numbers: list):
    for x in numbers:
        yield {"x": x}


@op
def square(x: int):
    return {"squared": x * x}


@op
def halve_until(value: int):
    while value >= 5:
        value = value // 2
        yield {"value": value}


# ── Branch leaves ───────────────────────────────────────────────────────


@op
def excellent():
    return {"grade": "A", "message": "Xuất sắc!"}


@op
def good():
    return {"grade": "B", "message": "Tốt!"}


@op
def average():
    return {"grade": "C", "message": "Trung bình"}


@op
def fail():
    return {"grade": "F", "message": "Cần cải thiện"}


# ── Graphs ──────────────────────────────────────────────────────────────


@graph
def for_loop(items, prefix):
    """Generator yield — sequential iteration (replaces ForOp)."""
    src = each_item(items=items, prefix=prefix)
    step = process_item(item=src["item"], prefix=src["prefix"])
    START >> src >> step >> END


@graph
def map_op(numbers):
    """Generator yield — parallel map (replaces MapOp)."""
    src = each_number(numbers=numbers)
    step = square(x=src["x"])
    START >> src >> step >> END


@graph
def while_loop(start_value):
    """Generator while — conditional loop (replaces WhileOp)."""
    src = halve_until(value=start_value)
    START >> src >> END


@graph
def branch(score):
    """if_() — conditional routing with soft edges."""
    grade_router = if_(score >= 90, "ex").if_(score >= 70, "gd").if_(score >= 50, "av").else_("fl")

    ex = excellent()
    gd = good()
    av = average()
    fl = fail()
    for leaf in (ex, gd, av, fl):
        leaf["grade"] >> PARENT["grade"]
        leaf["message"] >> PARENT["message"]

    START >> grade_router
    grade_router >> [ex, gd, av, fl]
    [ex, gd, av, fl] >> END


async def main() -> None:
    runs = [
        (
            "for_loop",
            for_loop(items=PARENT["items"], prefix=PARENT["prefix"]),
            {"items": ["apple", "banana", "cherry"], "prefix": "Fruit"},
        ),
        ("map_op", map_op(numbers=PARENT["numbers"]), {"numbers": [1, 2, 3, 4, 5]}),
        ("while_loop", while_loop(start_value=PARENT["start_value"]), {"start_value": 256}),
        ("branch", branch(score=PARENT["score"]), {"score": 95}),
    ]
    for label, g, inputs in runs:
        result = await Operon(g).run(inputs=inputs)
        content = {k: v for k, v in result.items() if k != "$state"}
        print(f"[{label}] {content}")


if __name__ == "__main__":
    asyncio.run(main())

# ── the served front door ───────────────────────────────────────────────
# Every operonx project serves. The [[serve]] block in operonx.toml names
# this graph, `operonx-serve` boots it, and the studio draws it as the
# entry node feeding the flow — no pipeline begins from nowhere.
#
# `ingress` yields one item per request payload and `egress` writes the
# reply back to the caller. Neither names a resource: the run was minted
# by a transport and already carries its session — and with no session the
# same graph still runs under a plain `engine.start()`, so serving costs
# the example nothing.
from operonx.core.serve import egress, ingress


@op
def answer(item=None) -> dict:
    """One request in, this example's reply out."""
    return {"reply": f"ex05 saw: {item!r}"}


@graph
def served():
    request = ingress()
    a = answer(item=request["item"])
    out = egress(item=a["reply"])
    START >> request >> a >> out >> END

