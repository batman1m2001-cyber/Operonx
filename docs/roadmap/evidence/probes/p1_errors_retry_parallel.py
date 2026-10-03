"""P1: op raise -> $errors, no retry. P2: parallel LWW/reducer determinism. P3: loop cap silent."""
import asyncio, operator
from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import if_

ATTEMPTS = {"n": 0}

@op
async def flaky(x: int) -> dict:
    ATTEMPTS["n"] += 1
    raise RuntimeError("transient 503")
    return {"y": x}

@op
def after(y: int = -1) -> dict:
    return {"z": y}

@graph
def g_err(x):
    f = flaky(x=x)
    a = after(y=f["y"])
    START >> f >> a >> END

# --- P2 parallel writers to a declared LWW cell / reducer cell
@op
async def w_fast(d: float) -> dict:
    await asyncio.sleep(d)
    return {"v": "fast", "acc": ["fast"]}

@op
async def w_slow(d: float) -> dict:
    await asyncio.sleep(d)
    return {"v": "slow", "acc": ["slow"]}

@op
def read(v: str = None, acc: list = None) -> dict:
    return {"final_v": v, "final_acc": acc}

@graph
def g_par(d1, d2):
    PARENT.declare(v=None, acc=[], reducers={"acc": operator.add})
    a, b = w_fast(d=d1), w_slow(d=d2)
    a["v"] >> PARENT["v"]; b["v"] >> PARENT["v"]
    a["acc"] >> PARENT["acc"]; b["acc"] >> PARENT["acc"]
    r = read(v=PARENT["v"], acc=PARENT["acc"])
    START >> [a, b]
    a >> r
    b >> r
    r >> END

# --- P3 loop that never terminates by itself
@op
def step(n: int) -> dict:
    return {"n": n + 1, "done": False}

@graph
def g_loop():
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    START >> s >> if_(s["done"] == True, END).else_(s)  # noqa

async def main():
    out = await Operon(g_err, params={"x": None}).run(inputs={"x": 1})
    print("P1 keys:", sorted(k for k in out if not k.startswith("$state")))
    print("P1 errors:", {k: v.strip().splitlines()[-1] for k, v in out.get("$errors", {}).items()})
    print("P1 attempts:", ATTEMPTS["n"], "| downstream z present:", "z" in out)

    e = Operon(g_par, params={"d1": None, "d2": None})
    o1 = await e.run(inputs={"d1": 0.01, "d2": 0.05})
    o2 = await e.run(inputs={"d1": 0.05, "d2": 0.01})
    print("P2 run1 final_v/acc:", o1.get("final_v"), o1.get("final_acc"), "errors:", "$errors" in o1)
    print("P2 run2 final_v/acc:", o2.get("final_v"), o2.get("final_acc"), "errors:", "$errors" in o2)

    o3 = await Operon(g_loop).run(inputs={})
    ns = o3.get("n")
    print("P3 iterations:", len(ns) if isinstance(ns, list) else ns, "last:", ns[-1] if isinstance(ns, list) else None,
          "| $errors:", o3.get("$errors"))

asyncio.run(main())
