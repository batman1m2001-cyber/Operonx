"""Repro: .collect() on one output of an op re-stores ALL its outputs at the
collect ctx, so a reducer cell fed by a sibling output gets every item twice."""
import asyncio
import operator

from operonx import END, PARENT, START, Operon, graph, op


@op
def each(n: int):
    for i in range(n):
        yield {"i": i}


@op
def work(i: int) -> dict:
    return {"row": i, "log": [f"item{i}"]}


@op
def report(rows: list) -> dict:
    return {"count": len(rows)}


@graph
def flow(n):
    PARENT.declare(log=[], reducers={"log": operator.add})
    e = each(n=n)
    w = work(i=e["i"])
    w["log"] >> PARENT["log"]
    r = report(rows=w["row"].collect())
    START >> e >> w >> r >> END


@graph
def flow_no_collect(n):
    PARENT.declare(log=[], reducers={"log": operator.add})
    e = each(n=n)
    w = work(i=e["i"])
    w["log"] >> PARENT["log"]
    START >> e >> w >> END


async def main():
    for g in (flow, flow_no_collect):
        engine = Operon(g, params={"n": None})
        out = await engine.run(inputs={"n": 3})
        st = out["$state"]
        print("cell log:", st.get(engine.name, "log"), "| errors:", list((out.get("$errors") or {})))


asyncio.run(main())
