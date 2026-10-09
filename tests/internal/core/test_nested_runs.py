"""A run started inside an op body records under the step that started it.

``invoke(target, ...)`` (and a plain ``Operon(g).run()``) inside an op body
is a step of the caller's run: the nested run's own record (op_type
``"graph"``) is shaped like a ``child()`` step of the caller, its ops carry
that record's full name and ctx as prefixes, and ``build_tree`` places them
under it. A failure inside it fails the caller only through the caller.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx import END, PARENT, START, Operon, OpFailed, child, graph, invoke, op
from operonx.core.ops import if_
from operonx.telemetry.consumers.langfuse import build_tree

pytestmark = pytest.mark.unit


@op
def lookup(order_id: str) -> dict:
    return {"status": f"{order_id}: shipped"}


@op
def shout(text: str) -> dict:
    return {"text": text.upper()}


@op
def boom(x: int) -> dict:
    raise ValueError(f"bad x {x}")


@graph
def lookup_flow(order_id):
    first = lookup(order_id=order_id)
    loud = shout(text=first["status"])
    START >> first >> loud >> END


@graph
def inner(text):
    loud = shout(text=text)
    START >> loud >> END


@graph
def outer_flow(order_id):
    first = lookup(order_id=order_id)
    sub = inner(text=first["status"])
    START >> first >> sub >> END


@op
def items(n: int):
    for i in range(n):
        yield {"i": i}


@op
def square(i: int) -> dict:
    return {"sq": i * i}


@graph
def fan(n):
    it = items(n=n)
    sq = square(i=it["i"])
    START >> it >> sq >> END


@op
def step(k: int) -> dict:
    return {"k": k + 1, "done": k + 1 >= 3}


@graph
def counter():
    PARENT.declare(k=0)
    s = step(k=PARENT["k"])
    s["k"] >> PARENT["k"]
    START >> s >> if_(s["done"] == True, END).else_(s)  # noqa: E712


def _tree(trace):
    """{op_id: node} with parents, the tree the studio and Langfuse read."""
    return build_tree(trace)


def _ancestors(nodes, nid):
    out = []
    while nodes[nid]["parent"] is not None:
        nid = nodes[nid]["parent"]
        out.append(nid)
    return out


def _one(trace, full):
    found = [n for n in trace.nodes if n.op_full_name == full]
    assert len(found) == 1, (full, [n.op_full_name for n in trace.nodes])
    return found[0]


def _one_ctx(trace, full, ctx):
    found = [n for n in trace.nodes if n.op_full_name == full and tuple(n.ctx) == tuple(ctx)]
    assert len(found) == 1, (full, ctx)
    return found[0]


async def _run(g, **inputs):
    handle = Operon(g, params={k: None for k in inputs}).start(inputs)
    out = await handle.result()
    return out, handle.trace


# ── invoke ──────────────────────────────────────────────────────────────


@op
async def host_op(order_id: str) -> dict:
    found = await invoke(lookup, order_id=order_id)
    return {"status": found["status"]}


@graph
def host_app(order_id):
    h = host_op(order_id=order_id)
    START >> h >> END


@pytest.mark.asyncio
async def test_invoke_an_op_runs_it_and_records_it_under_the_caller():
    out, trace = await _run(host_app, order_id="A1")
    assert out["status"] == "A1: shipped"
    root = _one(trace, "host_app.h.lookup")
    node = _one(trace, "host_app.h.lookup.lookup")
    assert root.op_type == "graph" and root.ctx == ("main", "lookup[0]")
    assert root.inputs == {"order_id": "A1"}
    assert root.outputs == {"status": "A1: shipped"}
    assert node.ctx == ("main", "lookup[0]") and node.op_type == "code"
    nodes = _tree(trace)
    assert nodes[root.op_id]["parent"] == "host_app.h#main"
    assert nodes[node.op_id]["parent"] == root.op_id
    assert trace.status == "ok"


@op
async def graph_host(order_id: str) -> dict:
    a = await invoke(outer_flow, order_id=order_id)
    b = await invoke(outer_flow, order_id=order_id + "!")
    return {"a": a["text"], "b": b["text"]}


@graph
def graph_app(order_id):
    h = graph_host(order_id=order_id)
    START >> h >> END


@pytest.mark.asyncio
async def test_a_graph_with_a_subgraph_nests_and_each_call_has_its_own_slot():
    out, trace = await _run(graph_app, order_id="A1")
    assert (out["a"], out["b"]) == ("A1: SHIPPED", "A1!: SHIPPED")
    first, second = sorted(
        (n for n in trace.nodes if n.op_full_name == "graph_app.h.outer_flow"), key=lambda n: n.ctx
    )
    assert first.ctx == ("main", "outer_flow[0]") and second.ctx == ("main", "outer_flow[1]")
    nodes = _tree(trace)
    loud = [n for n in trace.nodes if n.op_full_name == "graph_app.h.outer_flow.sub.loud"]
    assert len(loud) == 2
    for rec in loud:
        chain = _ancestors(nodes, rec.op_id)
        # the subgraph's container, then the nested run, then the caller
        assert nodes[chain[0]]["kind"] == "container" and nodes[chain[0]]["name"] == "sub"
        assert chain[1] in (first.op_id, second.op_id)
        assert chain[2] == "graph_app.h#main"
    # upstreams are renamed by the same rule as the records: in a plain run
    # `outer_flow.sub.loud#main` reads `outer_flow.sub#main` (the subgraph's
    # input, which no record holds), here under the call's slot
    loud0 = _one_ctx(trace, "graph_app.h.outer_flow.sub.loud", first.ctx)
    assert [u.from_op_id for u in loud0.upstreams] == [
        "graph_app.h.outer_flow.sub#main.outer_flow[0]"
    ]
    first0 = _one_ctx(trace, "graph_app.h.outer_flow.first", first.ctx)
    sub_in = _one_ctx(trace, "graph_app.h.outer_flow.sub.loud", second.ctx)
    assert sub_in.upstreams[0].from_op_full_name == "graph_app.h.outer_flow.sub"
    assert first0.op_id == "graph_app.h.outer_flow.first#main.outer_flow[0]"


@op
async def fan_host(n: int) -> dict:
    out = await invoke(fan, n=n)
    loops = await invoke(counter)
    return {"sq": out["sq"], "k": loops["k"]}


@graph
def fan_app(n):
    h = fan_host(n=n)
    START >> h >> END


@pytest.mark.asyncio
async def test_a_generator_and_a_loop_inside_a_nested_run_stay_inside_it():
    out, trace = await _run(fan_app, n=3)
    assert out["sq"] == [0, 1, 4]
    assert out["k"] == [1, 2, 3]
    nodes = _tree(trace)
    fan_root = _one(trace, "fan_app.h.fan")
    loop_root = _one(trace, "fan_app.h.counter")
    for n in trace.nodes:
        if n.op_full_name.startswith("fan_app.h.fan."):
            assert fan_root.op_id in _ancestors(nodes, n.op_id), n.op_id
        if n.op_full_name.startswith("fan_app.h.counter."):
            assert loop_root.op_id in _ancestors(nodes, n.op_id), n.op_id
    assert not [n for n in nodes.values() if n["kind"] == "stand-in"]


@op
async def stepper(order_id: str) -> dict:
    async with child("tool", inputs={"order_id": order_id}, op_type="tool") as t:
        out = await Operon(lookup_flow, params={"order_id": None}).run({"order_id": order_id})
        t.outputs = {"text": out["text"]}
    return {"text": out["text"]}


@graph
def step_app(order_id):
    h = stepper(order_id=order_id)
    START >> h >> END


@pytest.mark.asyncio
async def test_a_plain_run_inside_a_child_step_nests_under_the_step():
    out, trace = await _run(step_app, order_id="A1")
    assert out["text"] == "A1: SHIPPED"
    tool = _one(trace, "step_app.h.tool")
    root = _one(trace, "step_app.h.tool.lookup_flow")
    assert root.ctx == ("main", "tool[0]", "lookup_flow[0]")
    nodes = _tree(trace)
    assert nodes[root.op_id]["parent"] == tool.op_id
    for name in ("first", "loud"):
        rec = _one(trace, f"step_app.h.tool.lookup_flow.{name}")
        assert nodes[rec.op_id]["parent"] == root.op_id


# ── failures ────────────────────────────────────────────────────────────


@op
async def catches(x: int) -> dict:
    try:
        await invoke(boom, x=x)
    except OpFailed as e:
        return {"error": str(e)}
    return {"error": None}


@graph
def catch_app(x):
    h = catches(x=x)
    START >> h >> END


@pytest.mark.asyncio
async def test_a_failure_the_caller_handles_does_not_fail_the_run():
    out, trace = await _run(catch_app, x=1)
    assert "bad x 1" in out["error"]
    root = _one(trace, "catch_app.h.boom")
    node = _one(trace, "catch_app.h.boom.boom")
    assert root.status == "error" and node.status == "error"
    assert "ValueError" in (node.error or "")
    assert trace.status == "ok"


@op
async def lets_it_fail(x: int) -> dict:
    await invoke(boom, x=x)
    return {"never": True}


@graph
def fail_app(x):
    h = lets_it_fail(x=x)
    START >> h >> END


@pytest.mark.asyncio
async def test_a_failure_that_reaches_the_caller_fails_the_run():
    handle = Operon(fail_app, params={"x": None}).start({"x": 2})
    out = await handle.result()
    assert "$errors" in out
    trace = handle.trace
    assert _one(trace, "fail_app.h").status == "error"
    assert trace.status == "error"


@op
async def slow(t: float) -> dict:
    await asyncio.sleep(t)
    return {"t": t}


@op
async def waits(t: float) -> dict:
    await invoke(slow, t=t)
    return {"done": True}


@graph
def wait_app(t):
    h = waits(t=t)
    START >> h >> END


@pytest.mark.asyncio
async def test_a_cancelled_caller_cancels_the_nested_run():
    handle = Operon(wait_app, params={"t": None}).start({"t": 30})
    await asyncio.sleep(0.05)
    handle.cancel()
    await asyncio.sleep(0.05)
    trace = handle.trace
    root = _one(trace, "wait_app.h.slow")
    assert root.status == "cancelled"
    assert _one(trace, "wait_app.h.slow.slow").status == "cancelled"


# ── live task events ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_events_of_a_nested_run_reach_the_callers_stream():
    engine = Operon(host_app, params={"order_id": None})
    events = [e async for e in engine.stream({"order_id": "A1"}, mode="tasks")]
    seen = [(type(e).__name__, e.op, e.ctx) for e in events]
    g, slot = engine.name, ("main", "lookup[0]")
    assert seen.index(("TaskStarted", f"{g}.h.lookup", slot)) < seen.index(
        ("TaskStarted", f"{g}.h.lookup.lookup", slot)
    )
    assert seen.index(("TaskFinished", f"{g}.h.lookup.lookup", slot)) < seen.index(
        ("TaskFinished", f"{g}.h.lookup", slot)
    )


# ── the trap ────────────────────────────────────────────────────────────


@op
async def calls_op_directly(order_id: str) -> dict:
    lookup(order_id=order_id)
    return {"never": True}


@graph
def trap_app(order_id):
    h = calls_op_directly(order_id=order_id)
    START >> h >> END


@pytest.mark.asyncio
async def test_calling_an_op_inside_a_running_op_says_how_to_run_it():
    handle = Operon(trap_app, params={"order_id": None}).start({"order_id": "A1"})
    out = await handle.result()
    err = out["$errors"]["trap_app.h"]
    assert err["type"] == "TypeError"
    assert "operonx.invoke(lookup" in err["message"]


def test_building_a_graph_still_calls_op_functions():
    g = lookup_flow(order_id=None)
    assert g is not None


@pytest.mark.asyncio
async def test_invoke_outside_an_op_is_a_run_of_its_own():
    assert await invoke(lookup, order_id="X") == {"status": "X: shipped"}
    assert (await invoke(lookup_flow, order_id="y"))["text"] == "Y: SHIPPED"


@pytest.mark.asyncio
async def test_invoke_refuses_a_plain_function():
    def plain(x):
        return x

    with pytest.raises(TypeError, match="@op function or a @graph"):
        await invoke(plain, x=1)


@op
async def sees_its_run(x: int) -> dict:
    from operonx.core.workflow_trace import _current_trace

    trace = _current_trace.get()
    return {"root": trace.root.trace_id, "nested": trace.parent is not None}


@op
async def outer_sees(x: int) -> dict:
    from operonx.core.workflow_trace import _current_trace

    inner = await invoke(sees_its_run, x=x)
    return {"mine": _current_trace.get().trace_id, "root": inner["root"], "nested": inner["nested"]}


@graph
def root_app(x):
    h = outer_sees(x=x)
    START >> h >> END


@pytest.mark.asyncio
async def test_a_nested_runs_trace_knows_the_run_it_is_a_step_of():
    handle = Operon(root_app, params={"x": None}).start({"x": 1}, trace_id="the-run")
    out = await handle.result()
    assert out["mine"] == "the-run"
    assert out["nested"] is True and out["root"] == "the-run"
    assert handle.trace.parent is None and handle.trace.root is handle.trace
