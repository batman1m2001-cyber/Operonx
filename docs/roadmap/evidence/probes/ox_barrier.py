import asyncio, time
from operonx import END, START, Operon, graph, op
T = {"0": 0.0}
@op
async def a() -> dict:
    await asyncio.sleep(0.01); return {"x": 1}
@op
async def b() -> dict:
    await asyncio.sleep(0.5); return {"y": 1}
@op
async def a2(x: int) -> dict:
    return {"t": round(time.perf_counter() - T["0"], 3)}
@graph
def g():
    p, q = a(), b()
    r = a2(x=p["x"])
    START >> [p, q]
    p >> r >> END
    q >> END
async def main():
    e = Operon(g)
    T["0"] = time.perf_counter()
    print("OX7 a2 started at s:", (await e.run(inputs={}))["t"])
asyncio.run(main())
