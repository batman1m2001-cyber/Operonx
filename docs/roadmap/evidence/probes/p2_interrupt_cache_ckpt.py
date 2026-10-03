import asyncio, inspect
from operonx import END, START, InterruptOp, Operon, graph, op
from operonx.checkpoint import InMemoryCheckpointer, InterruptEvent, bind_interrupt_bus

SIDE = {"n": 0}

@op
def plan(x: int) -> dict:
    SIDE["n"] += 1          # side effect before the interrupt
    return {"plan": f"do {x}"}

@op
def execute(response: str = None) -> dict:
    return {"done": response}

@graph
def hitl(x):
    p = plan(x=x)
    ask = InterruptOp(payload=p["plan"])
    ex = execute(response=ask["response"])
    START >> p >> ask >> ex >> END

# cache collision: two different graphs, same engine var name, same op var name
@op(cache=True)
def a_impl(x: int) -> dict:
    return {"r": f"A{x}"}

@op(cache=True)
def b_impl(x: int) -> dict:
    return {"r": f"B{x}"}

@graph
def ga(x):
    c = a_impl(x=x)
    START >> c >> END

@graph
def gb(x):
    c = b_impl(x=x)
    START >> c >> END

async def main():
    # 1) Does stream(mode="updates") surface InterruptEvent as the docstring claims?
    eng = Operon(hitl, params={"x": None})
    seen_types = set()
    async def consume():
        async for item in eng.stream({"x": 1}, mode="updates"):
            seen_types.add(type(item).__name__)
    try:
        await asyncio.wait_for(consume(), timeout=1.0)
        print("P4 stream finished; types:", seen_types)
    except asyncio.TimeoutError:
        print("P4 stream(mode=updates) blocked on InterruptOp; types seen before block:", seen_types,
              "| InterruptEvent seen:", "InterruptEvent" in seen_types)

    # 2) resume works only in-process via handle.state + bus
    SIDE["n"] = 0
    events = []
    h = eng.start({"x": 2})
    unsub = bind_interrupt_bus(h.state, sink=events.append)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if events: break
    print("P4 interrupt events:", len(events))
    h.cancel()  # simulate process restart / worker death
    await asyncio.sleep(0.05)
    print("P4 after cancel, resume_interrupt ->", h.state.resume_interrupt(events[0].interrupt_id, "yes") if events else None)
    # "resume" = new run from scratch: side effect re-executes
    h2 = eng.start({"x": 2}); ev2 = []
    bind_interrupt_bus(h2.state, sink=ev2.append)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if ev2: break
    h2.state.resume_interrupt(ev2[0].interrupt_id, "yes")
    out = await h2.result()
    print("P4 rerun result done=", out.get("done"), "| plan() side effects executed:", SIDE["n"])

    # 3) Operon.start signature: any way to start from a checkpoint/thread?
    print("P5 start params:", list(inspect.signature(Operon.start).parameters))
    cp = InMemoryCheckpointer()
    await Operon(ga, params={"x": None}).run(inputs={"x": 1}, checkpointer=cp)
    print("P5 checkpointer methods:", [m for m in dir(cp) if not m.startswith("_")])

    # 4) cache collision across engines named the same
    engine = Operon(ga, params={"x": None})
    r1 = (await engine.run(inputs={"x": 7}))["r"]
    engine = Operon(gb, params={"x": None})   # different graph, same var name
    r2 = (await engine.run(inputs={"x": 7}))["r"]
    print("P6 cache: ga ->", r1, "| gb ->", r2, "(expected B7)")

asyncio.run(main())
