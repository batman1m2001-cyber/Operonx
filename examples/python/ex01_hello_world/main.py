"""01 Hello World — three tiny Operonx graphs, no API keys.

Run from this directory:

    uv sync
    uv run python main.py
"""

from __future__ import annotations

import asyncio

from operonx.core import END, START, Operon, graph, op


@op
def greet(who: str):
    return {"greeting": f"Xin chào, {who}!"}


@op
def greet_en(who: str):
    return {"greeting": f"Hello, {who}!"}


@op
def upper(text: str):
    return {"result": text.upper()}


@op
def step_a():
    return {"a_result": "Kết quả A"}


@op
def step_b():
    return {"b_result": "Kết quả B"}


@op
def merge(a: str, b: str):
    return {"combined": f"{a} + {b}"}


@graph
def hello_world(who):
    """Single op."""
    g = greet(who=who)
    START >> g >> END


@graph
def two_steps(who):
    """Two ops in series — greet_en → upper."""
    g = greet_en(who=who)
    u = upper(text=g["greeting"])
    START >> g >> u >> END


@graph
def fan_out_in():
    """Fan-out then fan-in — step_a + step_b → merge."""
    a = step_a()
    b = step_b()
    m = merge(a=a["a_result"], b=b["b_result"])
    START >> a >> m >> END
    START >> b >> m


async def main() -> None:
    runs = [
        ("hello", hello_world(who="Operon")),
        ("chain", two_steps(who="Operon User")),
        ("parallel", fan_out_in()),
    ]
    for label, g in runs:
        result = await Operon(g).run(inputs={})
        print(f"[{label}] {result}")


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
    return {"reply": f"ex01 saw: {item!r}"}


@graph
def served():
    request = ingress()
    a = answer(item=request["item"])
    out = egress(item=a["reply"])
    START >> request >> a >> out >> END

