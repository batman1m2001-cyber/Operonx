"""A subgraph holding a stream that a branch can go around.

The subgraph below streams its misses through a per-item op and collects
them, or, when there is none, routes straight to its last op. Either way its
last op runs once and the subgraph has an output, so the op after it in the
parent must run. It used to run only when the stream ran: a subgraph with a
generator handed its parent one output per stream context, and a run that
took the branch around the stream had none, so the parent's next op never
ran and the run reported nothing.
"""

from operonx import END, START, Operon, graph, op
from operonx.core.ops import if_


@op
def look(xs: list) -> dict:
    return {"misses": xs, "count": len(xs)}


@op
def each(xs: list):
    for x in xs:
        yield {"x": x}


@op
def work(x: int) -> dict:
    return {"y": x * 10}


@op
def saved(ys: list) -> dict:
    return {"fresh": ys}


@op
def done(fresh: list = None) -> dict:
    return {"answers": fresh or []}


@graph
def stage(xs):
    lk = look(xs=xs)
    ea = each(xs=lk["misses"])
    wk = work(x=ea["x"])
    sv = saved(ys=wk["y"].collect())
    dn = done(fresh=sv["fresh"])
    START >> lk >> if_(lk["count"] > 0, ea).else_(dn)  # noqa: E712
    ea >> wk >> sv >> dn
    dn >> END


@op
def after(answers: list) -> dict:
    return {"final": ["after", *answers]}


@graph
def outer(xs):
    st = stage(xs=xs)
    af = after(answers=st["answers"])
    START >> st >> af >> END


@op
def nothing(xs: list):
    for x in xs:
        yield {"x": x}


@graph
def empty_stream(xs):
    g = nothing(xs=xs)
    wk = work(x=g["x"])
    START >> g >> wk >> END


@op
def count(y: int = -1) -> dict:
    return {"seen": y}


@graph
def outer_empty(xs):
    st = empty_stream(xs=xs)
    c = count(y=st["y"])
    START >> st >> c >> END


async def test_the_op_after_the_subgraph_runs_when_the_branch_skips_the_stream():
    engine = Operon(outer, params={"xs": None})
    out = await engine.run(inputs={"xs": [1, 2]})
    assert out["final"] == ["after", 10, 20]
    out = await engine.run(inputs={"xs": []})
    assert "$errors" not in out
    assert out["final"] == ["after"]


async def test_a_subgraph_whose_stream_is_empty_still_hands_on_nothing():
    """Unchanged: a generator that yields nothing has no stream, so nothing after it runs."""
    out = await Operon(outer_empty, params={"xs": None}).run(inputs={"xs": []})
    assert "seen" not in out and "$errors" not in out
