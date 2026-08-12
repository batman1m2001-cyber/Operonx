import asyncio

from operonx.checkpoint import ObserveBudgetExceeded
from operonx.core import END, PARENT, START, GraphOp, Operon, op


@op(observe_max=1)
async def burst_async(n: int):
    return {"a": 1, "b": 2}

@op
async def tail(a: int):
    return {"out": a}

with GraphOp(name="ga2") as g:
    b = burst_async(n=PARENT["n"], name="burst")
    t = tail(a=b["a"], name="tail")
    START >> b >> t >> END

async def main():
    try:
        res = await asyncio.wait_for(Operon(g).run(inputs={"n": 1}), timeout=3)
        print("NO EXCEPTION:", {k: v for k, v in res.items() if k != "$state"})
    except asyncio.TimeoutError:
        print("HANG: scheduler deadlocked, no ObserveBudgetExceeded surfaced")
    except ObserveBudgetExceeded as e:
        print("raised:", e)

asyncio.run(main())
