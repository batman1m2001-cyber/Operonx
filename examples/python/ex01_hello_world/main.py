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
