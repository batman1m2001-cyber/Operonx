import asyncio, time
from typing import TypedDict
from langgraph.graph import StateGraph, START
T0 = 0
class S(TypedDict, total=False):
    a: int; b: int; a2: float
async def a(s): await asyncio.sleep(0.01); return {"a": 1}
async def b(s): await asyncio.sleep(0.5); return {"b": 1}
async def a2(s): return {"a2": round(time.perf_counter() - T0, 3)}
g = StateGraph(S); g.add_node("a", a); g.add_node("b", b); g.add_node("a2", a2)
g.add_edge(START, "a"); g.add_edge(START, "b"); g.add_edge("a", "a2")
T0 = time.perf_counter()
print("LG7 a2 started at s:", asyncio.run(g.compile().ainvoke({}))["a2"])
