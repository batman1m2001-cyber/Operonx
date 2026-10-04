"""K1/K2: ``child()`` — steps an op runs itself, recorded under its record.

A child's ctx is its parent's plus ``"<name>[<n>]"`` and its full name the
parent's plus ``".<name>"``, so the tree nests it with no stored link. Its
parent is the op's record — for a generator, the yield record being
produced when it opened.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx import END, START, Operon, Retry, child, graph, op, run_context
from operonx.core.runtime import invocation_key
from operonx.telemetry.consumers.langfuse import build_tree

pytestmark = pytest.mark.unit


def _by_name(trace, name):
    return [n for n in trace.nodes if n.op_name == name]


def _parents(trace):
    """{op_id: parent op_id} from the tree every consumer and the studio use."""
    return {nid: n["parent"] for nid, n in build_tree(trace).items() if n["kind"] == "record"}


@op
async def agent(question: str) -> dict:
    async with child("turn", inputs={"n": 0}, op_type="turn") as turn:
        async with child("model", inputs={"messages": [question]}, op_type="llm") as call:
            call.outputs = {"content": "use lookup"}
            call.attrs["gen_ai.operation.name"] = "chat"
        async with child("lookup", inputs={"q": question}, op_type="tool") as tool:
            tool.outputs = {"hit": 1}
            tool.attrs["gen_ai.tool.name"] = "lookup"
        turn.outputs = {"tool_calls": 1}
    return {"answer": "done"}


@graph
def solo(question):
    a = agent(question=question)
    START >> a >> END


@pytest.mark.asyncio
async def test_child_nesting():
    handle = Operon(solo, params={"question": None}).start({"question": "where?"})
    assert (await handle.result())["answer"] == "done"
    trace = handle.trace
    turn, model, lookup = (
        _by_name(trace, "turn"),
        _by_name(trace, "model"),
        _by_name(trace, "lookup"),
    )
    (turn,), (model,), (lookup,) = turn, model, lookup
    assert turn.ctx == ("main", "turn[0]") and turn.op_full_name == "solo.a.turn"
    assert model.ctx == ("main", "turn[0]", "model[0]")
    assert model.op_full_name == "solo.a.turn.model"
    assert model.op_id == "solo.a.turn.model#main.turn[0].model[0]"
    assert (model.op_type, model.inputs, model.outputs) == (
        "llm",
        {"messages": ["where?"]},
        {"content": "use lookup"},
    )
    assert model.attrs == {"gen_ai.operation.name": "chat"}
    assert lookup.attrs == {"gen_ai.tool.name": "lookup"}
    # recorded as each ends: the steps, then the turn, then the op
    order = [n.op_name for n in trace.nodes]
    assert order == ["model", "lookup", "turn", "a"]
    parents = _parents(trace)
    (op_rec,) = _by_name(trace, "a")
    assert parents[turn.op_id] == op_rec.op_id
    assert parents[model.op_id] == turn.op_id
    assert parents[lookup.op_id] == turn.op_id
    assert parents[op_rec.op_id] is None


KEYS: list = []


@op
async def keyed_steps() -> dict:
    for _ in range(2):
        async with child("tool"):
            KEYS.append(run_context())
    KEYS.append(run_context())
    return {"ok": True}


@graph
def keyed():
    k = keyed_steps()
    START >> k >> END


@pytest.mark.asyncio
async def test_each_child_has_its_own_run_context():
    KEYS.clear()
    await Operon(keyed).run({}, trace_id="r")
    first, second, own = KEYS
    assert (first.op_path, first.ctx) == ("keyed.k.tool", ("main", "tool[0]"))
    assert second.ctx == ("main", "tool[1]")
    assert first.idempotency_key == invocation_key("r", "keyed.k.tool", ("main", "tool[0]"))
    assert first.idempotency_key != second.idempotency_key
    assert own.op_path == "keyed.k", "the op's own context is back once the child ends"


@op
async def failing() -> dict:
    async with child("step", inputs={"x": 1}):
        raise ValueError("bad step")
    return {"never": True}


@graph
def fails():
    f = failing()
    START >> f >> END


@pytest.mark.asyncio
async def test_child_error_is_recorded_and_reraised():
    handle = Operon(fails).start({})
    out = await handle.result()
    assert "fails.f" in out["$errors"], "the error left the child and failed the op"
    (step,) = _by_name(handle.trace, "step")
    assert step.status == "error" and "ValueError: bad step" in step.error


STARTED = asyncio.Event()


@op
async def hangs() -> dict:
    async with child("wait"):
        STARTED.set()
        await asyncio.sleep(30)
    return {"never": True}


@graph
def hanging():
    h = hangs()
    START >> h >> END


@pytest.mark.asyncio
async def test_cancelled_child_is_marked_cancelled():
    STARTED.clear()
    handle = Operon(hanging).start({})
    await asyncio.wait_for(STARTED.wait(), 5)
    handle.cancel()
    await asyncio.sleep(0.05)
    (wait,) = _by_name(handle.trace, "wait")
    assert wait.status == "cancelled" and wait.error is None


@pytest.mark.asyncio
async def test_child_outside_a_run_records_nothing():
    async with child("loose", inputs={"a": 1}) as c:
        c.outputs = {"b": 2}
    assert run_context() is None


@pytest.mark.parametrize("bad", ["", "a.b", "a[0]", "a]", "a#b"])
def test_child_name_rejected(bad):
    with pytest.raises(ValueError, match="child name"):
        child(bad)


@op
async def stream_with_steps(n: int):
    for i in range(n):
        async with child("fetch", inputs={"i": i}) as c:
            c.outputs = {"row": i * 10}
        yield {"row": i * 10}


@graph
def streamed(n):
    s = stream_with_steps(n=n)
    START >> s >> END


@pytest.mark.asyncio
async def test_child_in_generator_hangs_under_yield_record():
    handle = Operon(streamed, params={"n": None}).start({"n": 2})
    await handle.result()
    trace = handle.trace
    fetches = _by_name(trace, "fetch")
    assert [f.ctx for f in fetches] == [
        ("main", "[0]", "fetch[0]"),
        ("main", "[1]", "fetch[0]"),
    ], "numbering restarts under each yield record"
    yields = {n.ctx: n for n in _by_name(trace, "s")}
    parents = _parents(trace)
    for f in fetches:
        assert parents[f.op_id] == yields[f.ctx[:2]].op_id


@op(transient=True)
async def transient_stream(n: int):
    for i in range(n):
        async with child("tick"):
            pass
        yield {"i": i}


@graph
def transient_g(n):
    t = transient_stream(n=n)
    START >> t >> END


@pytest.mark.asyncio
async def test_child_of_a_transient_generator_hangs_under_its_summary():
    handle = Operon(transient_g, params={"n": None}).start({"n": 3})
    await handle.result()
    trace = handle.trace
    ticks = _by_name(trace, "tick")
    assert [t.ctx for t in ticks] == [("main", f"tick[{i}]") for i in range(3)]
    (summary,) = _by_name(trace, "t")
    parents = _parents(trace)
    assert all(parents[t.op_id] == summary.op_id for t in ticks)


@op
def items(n: int):
    for i in range(n):
        yield {"i": i}


@op
async def work(i: int) -> dict:
    async with child("step", inputs={"i": i}) as c:
        await asyncio.sleep(0.01 * (3 - i))  # finish out of order
        c.outputs = {"i": i}
    return {"done": i}


@graph
def fan(n):
    g = items(n=n)
    w = work(i=g["i"].parallel())
    START >> g >> w >> END


@pytest.mark.asyncio
async def test_child_in_parallel_fan_out():
    handle = Operon(fan, params={"n": None}).start({"n": 3})
    await handle.result()
    trace = handle.trace
    steps = {s.inputs["i"]: s for s in _by_name(trace, "step")}
    works = {w.ctx: w for w in _by_name(trace, "w")}
    parents = _parents(trace)
    for i, s in steps.items():
        assert s.ctx == ("main", f"[{i}]", "step[0]")
        assert parents[s.op_id] == works[("main", f"[{i}]")].op_id


@op(exclude={"trace": ["secret"]})
async def guarded(q: str, secret: str) -> dict:
    async with child("call", inputs={"q": q, "secret": secret}) as c:
        c.outputs = {"secret": secret, "answer": q}
    return {"answer": q}


@graph
def guarded_g(q, secret):
    g = guarded(q=q, secret=secret)
    START >> g >> END


@pytest.mark.asyncio
async def test_child_exclude_honoured():
    handle = Operon(guarded_g, params={"q": None, "secret": None}).start(
        {"q": "hi", "secret": "sk-123"}
    )
    await handle.result()
    (call,) = _by_name(handle.trace, "call")
    assert call.inputs == {"q": "hi"} and call.outputs == {"answer": "hi"}


TRIES: list = []


@op(retry=Retry(max_attempts=2, initial=0.001, jitter=False))
async def retried_agent() -> dict:
    async with child("model") as c:
        c.outputs = {"try": len(TRIES)}
    TRIES.append(1)
    if len(TRIES) == 1:
        raise ConnectionError("503")
    return {"ok": True}


@graph
def retried():
    r = retried_agent()
    START >> r >> END


@pytest.mark.asyncio
async def test_child_retry_ids_unique():
    TRIES.clear()
    handle = Operon(retried).start({})
    assert (await handle.result())["ok"] is True
    trace = handle.trace
    models = _by_name(trace, "model")
    assert [(m.ctx, m.attempt) for m in models] == [
        (("main", "model[0]"), 1),
        (("main", "model[0]"), 2),
    ], "numbering restarts per attempt: the key of a retried step is stable"
    assert len({m.op_id for m in models}) == 2
    assert models[1].op_id.endswith("@2")
    parents = _parents(trace)
    failed, final = sorted(_by_name(trace, "r"), key=lambda n: n.attempt)
    assert parents[models[0].op_id] == failed.op_id  # "retried.r#main@1"
    assert parents[models[1].op_id] == final.op_id


@op
async def scalar_step() -> dict:
    async with child("calc") as c:
        c.outputs = 42
    return {"ok": True}


@graph
def scalar_g():
    s = scalar_step()
    START >> s >> END


@pytest.mark.asyncio
async def test_child_scalar_outputs_are_wrapped():
    handle = Operon(scalar_g).start({})
    await handle.result()
    (calc,) = _by_name(handle.trace, "calc")
    assert calc.outputs == {"_": 42}


# A step that stays open across an async generator's yields: a streamed
# model call. The consumer's code runs between the yields, in the same
# context, so the step must not become the current frame there.


async def streamed_model(chunks):
    async with child("model", inputs={"n": len(chunks)}, op_type="llm", current=False) as c:
        seen = []
        for chunk in chunks:
            seen.append(chunk)
            yield chunk
        c.outputs = {"content": "".join(seen)}


BETWEEN: list = []


@op
async def stream_consumer() -> dict:
    BETWEEN.clear()
    async for chunk in streamed_model(["a", "b"]):
        BETWEEN.append(run_context().op_path)
        async with child("emit", inputs={"chunk": chunk}):
            pass
    return {"ok": True}


@graph
def stream_consumed():
    s = stream_consumer()
    START >> s >> END


@pytest.mark.asyncio
async def test_child_held_across_yields_is_a_sibling_of_the_consumers_steps():
    handle = Operon(stream_consumed).start({})
    assert (await handle.result())["ok"] is True
    trace = handle.trace
    (model,) = _by_name(trace, "model")
    assert model.ctx == ("main", "model[0]") and model.outputs == {"content": "ab"}
    emits = _by_name(trace, "emit")
    assert [e.ctx for e in emits] == [("main", "emit[0]"), ("main", "emit[1]")], (
        "the consumer's steps nested under the open stream"
    )
    assert BETWEEN == ["stream_consumed.s", "stream_consumed.s"]
    parents = _parents(trace)
    (op_rec,) = _by_name(trace, "s")
    assert {parents[model.op_id], *(parents[e.op_id] for e in emits)} == {op_rec.op_id}


@op
async def stream_abandoned() -> dict:
    stream = streamed_model(["a", "b", "c"])
    async for _ in stream:
        break  # abandoned, never closed here: the loop finalizes it later
    async with child("after"):
        pass
    await stream.aclose()
    return {"ok": True}


@graph
def abandoned_g():
    s = stream_abandoned()
    START >> s >> END


@pytest.mark.asyncio
async def test_abandoned_stream_leaves_the_op_current_and_is_marked_cancelled():
    handle = Operon(abandoned_g).start({})
    assert (await handle.result())["ok"] is True
    trace = handle.trace
    (after,) = _by_name(trace, "after")
    assert after.ctx == ("main", "after[0]"), "a later step nested under the dead stream"
    (model,) = _by_name(trace, "model")
    assert model.status == "cancelled"
