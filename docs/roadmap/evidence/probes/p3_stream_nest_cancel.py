import asyncio, time
from operonx import END, START, Operon, graph, op

@op
def gen(n: int):
    for i in range(n):
        if i == 2:
            raise ValueError("boom at 2")
        yield {"i": i}

@op
def use(i: int) -> dict:
    return {"o": i * 10}

@graph
def g_gen(n):
    g = gen(n=n)
    u = use(i=g["i"])
    START >> g >> u >> END

@op
def leaf(x: int) -> dict:
    return {"y": x + 1}

@graph
def inner(x):
    l = leaf(x=x)
    START >> l >> END

@graph
def outer(x):
    s = inner(x=x)
    START >> s >> END

DONE = {"cpu": 0}

@op(bound="cpu")
def blocking(x: int) -> dict:
    time.sleep(0.3)
    DONE["cpu"] += 1   # side effect after cancel?
    return {"y": x}

@graph
def g_cpu(x):
    b = blocking(x=x)
    START >> b >> END

# concurrency: nested graphs each get their own semaphore?
LIVE = {"now": 0, "max": 0}

@op
def fan(n: int):
    for i in range(n):
        yield {"i": i}

@op
async def work(i: int) -> dict:
    LIVE["now"] += 1; LIVE["max"] = max(LIVE["max"], LIVE["now"])
    await asyncio.sleep(0.05)
    LIVE["now"] -= 1
    return {"r": i}

@graph
def inner_fan(n):
    f = fan(n=n)
    w = work(i=f["i"].parallel())
    START >> f >> w >> END

@op
def outer_fan_src(m: int):
    for j in range(m):
        yield {"n": 4}

@graph
def outer_fan(m):
    s = outer_fan_src(m=m)
    sub = inner_fan(n=s["n"].parallel(), concurrency=2)
    START >> s >> sub >> END

async def main():
    out = await Operon(g_gen, params={"n": None}).run(inputs={"n": 5})
    print("P7 gen mid-stream error: o=", out.get("o"), "| $errors keys:", list(out.get("$errors", {})))
    seen = []
    async for b in Operon(outer, params={"x": None}).stream({"x": 1}, mode="updates"):
        seen.extend(b.keys())
    print("P8 updates op names (nested):", seen)
    h = Operon(g_cpu, params={"x": None}).start({"x": 1})
    await asyncio.sleep(0.05); h.cancel()
    await asyncio.sleep(0.5)
    print("P9 cpu op side effect after cancel:", DONE["cpu"])
    try:
        await Operon(outer_fan(m=None, concurrency=2)).run(inputs={"m": 4})
        print("P10 nested concurrency=2 each, observed max concurrent leaf ops:", LIVE["max"])
    except TypeError as e:
        print("P10 graph(concurrency=) not accepted:", e)

asyncio.run(main())
