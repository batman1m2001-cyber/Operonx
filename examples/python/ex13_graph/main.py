"""13 Graph — `@graph` reusable components, composition, nesting.

Tier 1 — pure compute, no API keys. Run from this directory:

    uv sync
    uv run python main.py
"""

from __future__ import annotations

import asyncio

from operonx.core import END, PARENT, START, Operon, graph, op


@op
def double(x: int):
    return {"result": x * 2}


@op
def add(a: int, b: int):
    return {"result": a + b}


# ── Reusable graph components ───────────────────────────────────────────


@graph
def double_flow(val):
    step = double(x=val)
    START >> step >> END


@graph
def double_renamed(val):
    step = double(x=val)
    step["result"] >> PARENT["doubled"]
    START >> step >> END


@graph
def add_and_double(a, b):
    s = add(a=a, b=b)
    d = double(x=s["result"])
    START >> s >> d >> END


@graph
def quad_flow(val):
    d1 = double_flow(val=val)
    d2 = double_flow(val=d1["result"])
    START >> d1 >> d2 >> END


# ── Top-level scenarios that compose the @graphs above ──────────────────


@graph
def basic(val):
    d = double_flow(val=val)
    START >> d >> END


@graph
def chained(val):
    d1 = double_flow(val=val)
    d2 = double_flow(val=d1["result"])
    d3 = double_flow(val=d2["result"])
    START >> d1 >> d2 >> d3 >> END


@graph
def renamed(val):
    d = double_renamed(val=val)
    d["doubled"] >> PARENT["answer"]
    START >> d >> END


@graph
def multi_params(x, y):
    calc = add_and_double(a=x, b=y)
    START >> calc >> END


@graph
def nested(val):
    q = quad_flow(val=val)
    START >> q >> END


async def main() -> None:
    runs = [
        ("basic", basic(val=PARENT["val"]), {"val": 5}),
        ("chained", chained(val=PARENT["val"]), {"val": 3}),
        ("renamed", renamed(val=PARENT["val"]), {"val": 7}),
        ("multi_params", multi_params(x=PARENT["x"], y=PARENT["y"]), {"x": 3, "y": 7}),
        ("nested", nested(val=PARENT["val"]), {"val": 5}),
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
    return {"reply": f"ex13 saw: {item!r}"}


@graph
def served():
    request = ingress()
    a = answer(item=request["item"])
    out = egress(item=a["reply"])
    START >> request >> a >> out >> END

