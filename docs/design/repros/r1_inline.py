"""R1/R2: sync (inline) ops are never swept by _sweep_ctx."""
import asyncio

from operonx.core import END, PARENT, START, GraphOp, Interrupt, Operon, op

ran = []


@op
def seed(n: int):
    return {"x": n}


@op
def a(x: int):
    ran.append("a")
    return Interrupt(ctx_to_cancel=Interrupt.ALL, reason="stop everything")


@op
def b(x: int):
    ran.append("b")
    return {"b": x + 1}


@op
def c(x: int):
    ran.append("c")
    return {"c": x + 2}


@op
def d(b: int, c: int):
    ran.append("d")
    return {"out": b + c}


with GraphOp(name="allsync") as g:
    s = seed(n=PARENT["n"])
    oa = a(x=s["x"])
    ob = b(x=s["x"])
    oc = c(x=s["x"])
    od = d(b=ob["b"], c=oc["c"])
    START >> s >> [oa, ob, oc]
    [ob, oc] >> od
    od >> END


async def main():
    res = await Operon(g).run(inputs={"n": 1})
    print("bounds:", {n: o.bound for n, o in g._ops.items()})
    print("ran:", ran)
    print("result:", {k: v for k, v in res.items() if k != "$state"})


asyncio.run(main())
