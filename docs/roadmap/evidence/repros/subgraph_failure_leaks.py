"""Repro: an op that fails inside a subgraph does not stop the op after the
subgraph; it runs with its default (None), unlike the flat case."""
import asyncio

from operonx import END, START, Operon, graph, op


@op
def boom(x: int) -> dict:
    raise ValueError("boom")
    return {"y": x}


@op
def after(y: int = None) -> dict:
    return {"z": f"after ran with y={y!r}"}


@graph
def inner(x):
    b = boom(x=x)
    START >> b >> END


@graph
def nested(x):
    s = inner(x=x)
    a = after(y=s["y"])
    START >> s >> a >> END


@graph
def flat(x):
    b = boom(x=x)
    a = after(y=b["y"])
    START >> b >> a >> END


async def main():
    for g in (flat, nested):
        out = await Operon(g, params={"x": None}).run(inputs={"x": 1})
        print("z:", out.get("z"), "| errors:", list(out.get("$errors", {})))


asyncio.run(main())
