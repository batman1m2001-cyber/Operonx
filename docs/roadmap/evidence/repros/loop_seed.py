"""How do I seed a loop cell from a graph input?"""
import asyncio

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import if_


@op
def step(n: int) -> dict:
    n = n // 2 if n % 2 == 0 else 3 * n + 1
    return {"n": n, "done": n == 1}


@graph
def a_ref_seed(seed):
    PARENT.declare(n=seed)  # (a) a Ref as the initial value
    c = step(n=PARENT["n"])
    c["n"] >> PARENT["n"]
    START >> c >> if_(c["done"] == True, END).else_(c)  # noqa: E712


@graph
def b_param_is_cell(n):
    PARENT.declare(n=0)  # (b) declare the parameter's own name
    c = step(n=n)
    c["n"] >> PARENT["n"]
    START >> c >> if_(c["done"] == True, END).else_(c)  # noqa: E712


@graph
def c_no_declare(n):
    c = step(n=n)  # (c) write back into the parameter without declaring
    c["n"] >> PARENT["n"]
    START >> c >> if_(c["done"] == True, END).else_(c)  # noqa: E712


async def main():
    for g, key in ((a_ref_seed, "seed"), (b_param_is_cell, "n"), (c_no_declare, "n")):
        try:
            out = await Operon(g, params={key: None}).run(inputs={key: 6})
            print(key, "path:", out.get("n"), "| errors:", list(out.get("$errors", {})))
        except Exception as e:
            print(key, "RAISED", type(e).__name__, str(e)[:300])


asyncio.run(main())
