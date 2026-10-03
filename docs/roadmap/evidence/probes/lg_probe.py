import asyncio, operator, os, sqlite3, tempfile
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command, RetryPolicy
from langgraph.errors import InvalidUpdateError, GraphRecursionError
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.func import entrypoint, task

class S(TypedDict, total=False):
    v: str
    acc: Annotated[list, operator.add]

async def fast(s): await asyncio.sleep(0.01); return {"v": "fast", "acc": ["fast"]}
async def slow(s): await asyncio.sleep(0.05); return {"v": "slow", "acc": ["slow"]}

def build(lww=True):
    b = StateGraph(S)
    if lww:
        b.add_node("a", fast); b.add_node("b", slow)
    else:
        b.add_node("a", lambda s: {"acc": ["a"]}); b.add_node("b", lambda s: {"acc": ["b"]})
    b.add_edge(START, "a"); b.add_edge(START, "b"); b.add_edge("a", END); b.add_edge("b", END)
    return b.compile()

async def acc_only():
    class T(TypedDict): acc: Annotated[list, operator.add]
    async def a(s): await asyncio.sleep(0.05); return {"acc": ["a"]}
    async def b_(s): await asyncio.sleep(0.01); return {"acc": ["b"]}
    b = StateGraph(T); b.add_node("a", a); b.add_node("b", b_)
    b.add_edge(START, "a"); b.add_edge(START, "b")
    return await b.compile().ainvoke({"acc": []})

async def main():
    try:
        await build().ainvoke({})
        print("LG1 LWW concurrent writes: no error")
    except InvalidUpdateError as e:
        print("LG1 LWW concurrent writes ->", type(e).__name__)
    print("LG2 reducer order (a slower than b):", await acc_only())

    # recursion limit
    class C(TypedDict): n: int
    b = StateGraph(C); b.add_node("s", lambda s: {"n": s["n"] + 1})
    b.add_edge(START, "s"); b.add_conditional_edges("s", lambda s: "s")
    try:
        b.compile().invoke({"n": 0}, {"recursion_limit": 50}); print("LG3 no error")
    except GraphRecursionError as e:
        print("LG3 loop cap ->", type(e).__name__)

    # retry policy
    calls = {"n": 0}
    def flaky(s):
        calls["n"] += 1
        if calls["n"] < 3: raise ConnectionError("503")
        return {"n": 1}
    b = StateGraph(C); b.add_node("f", flaky, retry_policy=RetryPolicy(initial_interval=0.01, jitter=False))
    b.add_edge(START, "f")
    print("LG4 retry:", b.compile().invoke({"n": 0}), "attempts", calls["n"])

asyncio.run(main())

# durable interrupt across "process restart" (new graph + new saver over same sqlite file)
db = os.path.join(tempfile.mkdtemp(), "cp.sqlite")
SIDE = {"plan": 0}
class H(TypedDict, total=False):
    plan: str; answer: str
def plan(s): SIDE["plan"] += 1; return {"plan": "do X"}
def ask(s): return {"answer": interrupt(s["plan"])}
def mk(conn):
    b = StateGraph(H); b.add_node("plan", plan); b.add_node("ask", ask)
    b.add_edge(START, "plan"); b.add_edge("plan", "ask")
    return b.compile(checkpointer=SqliteSaver(conn))
cfg = {"configurable": {"thread_id": "t1"}}
c1 = sqlite3.connect(db, check_same_thread=False); g1 = mk(c1)
r = g1.invoke({}, cfg); print("LG5 interrupted:", "__interrupt__" in r); c1.close()
c2 = sqlite3.connect(db, check_same_thread=False); g2 = mk(c2)   # "new process"
print("LG5 pending next:", g2.get_state(cfg).next)
print("LG5 resumed:", g2.invoke(Command(resume="yes"), cfg), "| plan side effects:", SIDE["plan"])
print("LG5 history len:", len(list(g2.get_state_history(cfg))))

# functional API: completed task results are not recomputed on resume
SIDE2 = {"t": 0}
@task
def expensive(x): SIDE2["t"] += 1; return x * 2
c3 = sqlite3.connect(db, check_same_thread=False)
@entrypoint(checkpointer=SqliteSaver(c3))
def wf(x):
    y = expensive(x).result()
    ok = interrupt(f"approve {y}?")
    return {"y": y, "ok": ok}
cfg2 = {"configurable": {"thread_id": "t2"}}
wf.invoke(3, cfg2); print("LG6 after resume:", wf.invoke(Command(resume=True), cfg2), "| task executions:", SIDE2["t"])
