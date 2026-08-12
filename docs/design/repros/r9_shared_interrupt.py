"""R9: _resolve_interrupt_target mutates the Interrupt in place -> a reused
instance keeps the FIRST emitter's ctx."""
import asyncio

from operonx.core import END, PARENT, START, GraphOp, Interrupt, Operon, op

STOP = Interrupt(reason="skip this item")   # module-level constant, reused

@op
def src(n: int):
    for i in range(6):
        yield {"i": i}

@op
async def work(i: int):
    await asyncio.sleep(0.01)
    if i in (1, 3):
        return STOP
    return {"j": i}

with GraphOp(name="shared_int") as g:
    s = src(n=PARENT["n"])
    w = work(i=s["i"].parallel())
    START >> s >> w >> END

async def main():
    h = Operon(g).start(inputs={"n": 6})
    out = await asyncio.wait_for(h.collect(), timeout=20)
    print("out j:", out.get("j"), " (expected [0,2,4,5])")
    print("targets:", [e.ctx_to_cancel for e in h.interrupts])

asyncio.run(main())
