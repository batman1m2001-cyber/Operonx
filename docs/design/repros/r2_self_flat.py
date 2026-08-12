"""R1: Interrupt() (SELF) from a plain root-context async op == Interrupt.ALL."""
import asyncio

from operonx.core import END, PARENT, START, GraphOp, Interrupt, Operon, op

ran = []


@op
async def seed(n: int):
    return {"x": n}


@op
async def guard(x: int):
    await asyncio.sleep(0.01)
    ran.append("guard")
    return Interrupt(reason="just my branch, honest")


@op
async def sibling(x: int):
    await asyncio.sleep(0.05)
    ran.append("sibling")
    return {"s": x * 10}


@op
async def after_sibling(s: int):
    ran.append("after_sibling")
    return {"out": s}


with GraphOp(name="flat_self") as g:
    sd = seed(n=PARENT["n"])
    gd = guard(x=sd["x"])
    sb = sibling(x=sd["x"])
    af = after_sibling(s=sb["s"])
    START >> sd >> [gd, sb]
    sb >> af >> END


async def main():
    res = await Operon(g).run(inputs={"n": 3})
    print("ran:", ran)
    print("result:", {k: v for k, v in res.items() if k != "$state"})
    ev = res.get("__interrupt__")
    print("resolved target:", ev.ctx_to_cancel if ev else None)


asyncio.run(main())
