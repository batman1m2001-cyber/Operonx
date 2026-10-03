"""Does asyncio.wait_for(engine.run(...)) actually stop the graph?"""
import asyncio

from operonx import END, START, Operon, graph, op

SEEN = {"slow_done": 0, "after": 0}


@op
async def slow(x: int) -> dict:
    await asyncio.sleep(1.0)
    SEEN["slow_done"] += 1
    return {"y": x}


@op
async def after(y: int) -> dict:
    SEEN["after"] += 1  # a side effect: charge a card, send an email...
    return {"z": y}


@graph
def flow(x):
    s = slow(x=x)
    a = after(y=s["y"])
    START >> s >> a >> END


async def main():
    engine = Operon(flow, params={"x": None})
    try:
        await asyncio.wait_for(engine.run(inputs={"x": 1}), timeout=0.3)
    except asyncio.TimeoutError:
        print("timed out at 0.3s")
    await asyncio.sleep(2.0)
    print("1.7s later -> slow finished:", SEEN["slow_done"], "| after ran:", SEEN["after"])


asyncio.run(main())
