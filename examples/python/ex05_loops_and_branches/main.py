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
    """Generator yield — parallel map (replaces MapOp).

    `.parallel()` is the consumption mode, declared where the value is
    consumed: the generator's yields fan out to concurrent `square` runs
    instead of the sequential default. The studio draws this edge with a
    `∥` — it changes the run's shape, so it belongs on the wire.
    """
    src = each_number(numbers=numbers)
    step = square(x=src["x"].parallel())
    START >> src >> step >> END


@op
def total(xs: list):
    """Takes the WHOLE stream at once — `.collect()` hands it a list."""
    return {"sum": sum(xs)}


@graph
def collect_op(numbers):
    """Generator yield — `.collect()` buffers every yield until EOF.

    The opposite trade to `.parallel()`: nothing downstream runs until the
    generator finishes, and then `total` runs exactly once with the list.
    An edge like this must not be drawn as a plain arrow, because it is
    not one — it is a barrier.
    """
    src = each_number(numbers=numbers)
    t = total(xs=src["x"].collect())
    START >> src >> t >> END


@graph
def while_loop(start_value):
    """Generator while — conditional loop (replaces WhileOp)."""
    src = halve_until(value=start_value)
    START >> src >> END


@op
def think(x: int = 0):
    """One agent step: read the loop-carried value, advance it."""
    return {"x": x + 1}


@op
def proceed():
    """Fires only on the loop's else-branch — that firing IS the iteration.

    A synthetic loop ends when no back-edge source fires in an iteration,
    so the source has to sit behind the branch. Put it on the main path and
    it fires every time, the loop never terminates, and the runaway
    ceiling (1000 iterations) is what stops the run — the first version of
    this example did exactly that.
    """
    return {"go": True}


@graph
def agent_loop():
    """The agent while-loop: an authored cycle, rewritten by the compiler.

    Three things every loop like this needs, and the first draft of this
    example got all three wrong (it ran to the 1000-iteration runaway
    ceiling with its state stuck):

    * **The loop-carried variable is a DECLARED cell.** `PARENT.declare`
      creates it, `inputs={"x": 0}` seeds it, `t["x"] >> PARENT["x"]`
      advances it. A direct ref between iterations is not a thing — the
      back-edge orders execution, it does not carry data.
    * **The branch condition reads the CELL**, `PARENT["x"] >= 3`, not the
      op's output ref — the cell is what the next iteration actually sees.
    * **The back-edge source sits behind the else-branch.** The loop ends
      when no back-edge source fires in an iteration; a source on the main
      path fires every time and the loop never ends.

    The studio reverses the compiler's rewrite for display: you see the
    ops you wrote and a `↺ loop` return edge, not one opaque
    `__loop_0__` box.
    """
    PARENT.declare(x=0)
    t = think(x=PARENT["x"])
    t["x"] >> PARENT["x"]
    again = proceed()
    START >> t
    t >> if_(PARENT["x"] >= 3, END).else_(again)
    again >> t


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
        ("collect_op", collect_op(numbers=PARENT["numbers"]), {"numbers": [1, 2, 3, 4, 5]}),
        ("agent_loop", agent_loop(), {"x": 0}),
        ("branch", branch(score=PARENT["score"]), {"score": 95}),
    ]
    for label, g, inputs in runs:
        result = await Operon(g).run(inputs=inputs)
        content = {k: v for k, v in result.items() if k != "$state"}
        print(f"[{label}] {content}")


if __name__ == "__main__":
    asyncio.run(main())

# ── the served main flow ───────────────────────────────────────────────
# One main graph, served: ingress in, every loop-and-branch technique
# composed as a nested graph, egress out. Open any nested graph on the
# studio canvas — the agent_loop container shows the ↺ authored cycle.
from operonx.app.serve import egress, ingress


@op
def unpack(item=None) -> dict:
    """Payload → the fields the flows below consume."""
    item = item if isinstance(item, dict) else {}
    return {
        "items": item.get("items", ["a", "b", "c"]),
        "numbers": item.get("numbers", [1, 2, 3, 4]),
        "start": item.get("start", 20),
        "score": item.get("score", 85),
    }


@graph
def main_flow():
    request = ingress()
    fields = unpack(item=request["item"])
    sequential = for_loop(items=fields["items"], prefix="item")
    mapped = map_op(numbers=fields["numbers"])
    collected = collect_op(numbers=fields["numbers"])
    halved = while_loop(start_value=fields["start"])
    agent = agent_loop()
    graded = branch(score=fields["score"])
    out = egress(item=graded["message"])
    START >> request >> fields >> sequential >> mapped >> collected >> END
    collected >> halved >> agent >> graded >> out >> END
