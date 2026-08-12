"""R4: SELF interrupt on a sequential fan-out strands every remaining item.

Same shape as tests/.../test_interrupt_default_target.py::
test_siblings_survive_an_untargeted_interrupt, but WITHOUT `.parallel()`
(i.e. the default sequential stream policy).
"""
import asyncio

from operonx.core import END, PARENT, START, GraphOp, Interrupt, Operon, op

ran = []


@op
def src(n: int):
    for i in range(6):
        yield {"i": i}


@op
async def work(i: int):
    await asyncio.sleep(0.01)
    ran.append(i)
    if i == 1:
        return Interrupt(reason="skip just this item")
    return {"j": i}


with GraphOp(name="seq_self") as g:
    s = src(n=PARENT["n"])
    w = work(i=s["i"])  # DEFAULT sequential policy
    START >> s >> w >> END


async def main():
    h = Operon(g).start(inputs={"n": 6})
    out = await asyncio.wait_for(h.collect(), timeout=20)
    print("work ran on items:", ran, "  (expected 0..5)")
    print("out j:", out.get("j"), "  (expected [0,2,3,4,5])")
    print("interrupts:", [(e.reason, repr(e.ctx_to_cancel), e.op, e.ctx) for e in h.interrupts])


asyncio.run(main())
