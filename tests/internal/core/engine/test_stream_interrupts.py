"""``stream()`` surfaces ``InterruptOp`` suspensions, and lets you answer them (roadmap C9).

``InterruptOp``'s docstring promised that ``engine.stream(mode="updates")``
yields an ``InterruptEvent`` when the op suspends, to be answered with
``run.resume(value)``. Neither existed: the stream yielded only update
dicts and then blocked on the suspended op, and a stream consumer holds
no handle to resume through. Measured by
``evidence/probes/p2_interrupt_cache_ckpt.py``: types seen before the
block, ``{'dict'}``.

Now ``mode="updates"`` yields the ``InterruptEvent`` in order with the
updates, ``mode="interrupts"`` yields only those events, and the event
answers itself: ``event.resume(value)``.
"""

from __future__ import annotations

import asyncio

from operonx import END, START, InterruptOp, Operon, graph, op
from operonx.checkpoint import InterruptEvent, bind_interrupt_bus


@op
def plan(x: int) -> dict:
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


async def _consume(engine, mode: str, answer="yes") -> list:
    seen = []
    async for item in engine.stream({"x": 1}, mode=mode):
        seen.append(item)
        if isinstance(item, InterruptEvent):
            assert item.resume(answer) is True
    return seen


async def test_stream_updates_surfaces_interrupt_event():
    engine = Operon(hitl, params={"x": None})
    seen = await asyncio.wait_for(_consume(engine, "updates"), timeout=2)

    events = [s for s in seen if isinstance(s, InterruptEvent)]
    assert len(events) == 1
    event = events[0]
    assert event.op == f"{engine.name}.ask"
    assert event.payload == "do 1"

    # In order: the plan's update before the event, the answer after it.
    at = seen.index(event)
    before = {op for batch in seen[:at] for op in batch}
    after = [batch for batch in seen[at + 1 :] if f"{engine.name}.ex" in batch]
    assert f"{engine.name}.p" in before
    assert after and after[-1][f"{engine.name}.ex"]["done"] == "yes"


async def test_stream_interrupts_yields_only_the_events():
    engine = Operon(hitl, params={"x": None})
    seen = await asyncio.wait_for(_consume(engine, "interrupts"), timeout=2)

    assert [type(s) for s in seen] == [InterruptEvent]
    assert seen[0].payload == "do 1"


async def test_resume_reports_whether_the_op_was_still_waiting():
    engine = Operon(hitl, params={"x": None})
    async for event in engine.stream({"x": 1}, mode="interrupts"):
        assert event.resume("first") is True
        assert event.resume("second") is False


async def test_events_from_the_bus_resume_too():
    """One construction site: a handle's bus listener gets the same event."""
    engine = Operon(hitl, params={"x": None})
    handle = engine.start({"x": 1})
    events: list = []
    bind_interrupt_bus(handle.state, sink=events.append)
    while not events:
        await asyncio.sleep(0.01)
    assert events[0].resume("ok") is True
    assert (await asyncio.wait_for(handle.result(), timeout=2))["done"] == "ok"


@graph
def plain(x):
    p = plan(x=x)
    START >> p >> END


async def test_updates_without_an_interrupt_op_are_only_dicts():
    engine = Operon(plain, params={"x": None})
    seen = [item async for item in engine.stream({"x": 1}, mode="updates")]
    assert seen and all(isinstance(s, dict) for s in seen)
