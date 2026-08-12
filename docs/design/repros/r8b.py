"""R8b: a fatal (BaseException) run failure is swallowed by stream(mode='updates')
but raised by run()/collect()."""
import asyncio

from operonx.checkpoint import ObserveBudgetExceeded
from operonx.core import END, PARENT, START, GraphOp, Operon, op


def build():
    with GraphOp(name="fatal_g") as g:
        PARENT.declare(count=0)

        @op(observe_max=1)
        def burst():
            return {"a": 1, "b": 2}

        b = burst(name="burst")
        b["a"] >> PARENT["count"]
        START >> b >> END
    return g

async def main():
    e = Operon(build())
    print("--- run() ---")
    try:
        await e.run(inputs={})
        print("run() returned normally (!)")
    except ObserveBudgetExceeded as ex:
        print("run() raised:", type(ex).__name__)

    e2 = Operon(build())
    print("--- stream(updates) ---")
    got = []
    try:
        async for u in e2.stream({}, mode="updates"):
            got.append(u)
        print("stream ended cleanly, updates:", got)
    except BaseException as ex:
        print("stream raised:", type(ex).__name__)

asyncio.run(main())
