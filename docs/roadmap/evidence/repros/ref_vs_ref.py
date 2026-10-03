import asyncio

from operonx import END, START, Operon, graph, op
from operonx.core.ops import if_


@op
def pair(a: int, b: int) -> dict:
    return {"a": a, "b": b}


@op
def yes() -> dict:
    return {"r": "a>b"}


@op
def no() -> dict:
    return {"r": "a<=b"}


@graph
def cmp(a, b):
    p = pair(a=a, b=b)
    y, n = yes(), no()
    START >> p >> if_(p["a"] > p["b"], y).else_(n)
    y >> END
    n >> END


@graph
def cmp_inputs(a, b):
    y, n = yes(), no()
    START >> if_(a > b, y).else_(n)  # branch directly on graph params
    y >> END
    n >> END


async def main():
    for g in (cmp, cmp_inputs):
        e = Operon(g, params={"a": None, "b": None})
        print([(await e.run(inputs={"a": a, "b": b})).get("r") for a, b in ((1, 2), (3, 2))])


asyncio.run(main())
