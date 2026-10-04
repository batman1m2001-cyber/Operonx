"""A cancel mid-turn writes nothing: not to the session, not to the store.

The callbot cancels a turn the caller talked over; that turn must leave no
trace. The one exception is the crash journal of a turn running a tool that
is not idempotent: the call may have taken effect, so the store keeps the
record that it was in flight (and a resume answers it "outcome unknown").
"""

from __future__ import annotations

import asyncio

import pytest

from operonx_agents import (
    InMemorySession,
    InMemoryStateStore,
    Runner,
    SQLiteSession,
    SQLiteStateStore,
)
from operonx_agents.run.runner import OUTCOME_UNKNOWN
from tests.agents import RAN, asks, make, roles, says


@pytest.fixture(autouse=True)
def _ran():
    RAN.clear()
    yield


@pytest.fixture(params=["memory", "sqlite"])
def backends(request, tmp_path):
    if request.param == "memory":
        return InMemorySession("s"), InMemoryStateStore()
    db = tmp_path / "runs.db"
    return SQLiteSession("s", db), SQLiteStateStore(db)


async def until(check, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "condition never held"
        await asyncio.sleep(0.005)


async def snapshot(session, store, run_id):
    state = await store.load(run_id)
    return await session.get_items(), state.to_dict() if state else None


async def cancel_when(task, check) -> None:
    await until(check)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_cancel_during_the_first_model_call(hub, backends):
    session, store = backends
    agent, llm = make(hub, says("never"), llm_kw={"delay": 5})
    task = asyncio.ensure_future(Runner.run(agent, "hi", session=session, store=store, run_id="r1"))
    await cancel_when(task, lambda: llm.calls == 1)
    assert await snapshot(session, store, "r1") == ([], None)


async def test_cancel_during_a_later_turn_keeps_exactly_the_committed_ones(hub, backends):
    session, store = backends
    agent, llm = make(hub, asks(("echo", {"a": 1})), says("never"))
    plain, held = llm.stream, asyncio.Event()
    after_first = {}

    async def hold_the_second(messages, **params):
        if llm.calls == 1:  # the first request has been answered and committed
            after_first["seen"] = await snapshot(session, store, "r2")
            held.set()
            await asyncio.sleep(5)
        async for chunk in plain(messages, **params):
            yield chunk

    llm.stream = hold_the_second
    task = asyncio.ensure_future(Runner.run(agent, "hi", session=session, store=store, run_id="r2"))
    await cancel_when(task, held.is_set)
    items, state = after_first["seen"]
    assert roles(items) == ["user", "assistant", "tool"]
    assert state["turn"] == 1 and state["pending"] is None
    assert await snapshot(session, store, "r2") == (items, state)


async def test_cancel_during_an_idempotent_tool(hub, backends):
    from operonx_agents import tool

    running = asyncio.Event()

    @tool(readonly=True)
    async def search(q: str) -> str:
        """Search."""
        running.set()
        await asyncio.sleep(5)
        RAN.append("search")
        return "found"

    session, store = backends
    agent, _ = make(hub, asks(("search", {"q": "x"})), says(), tools=[search])
    task = asyncio.ensure_future(Runner.run(agent, "hi", session=session, store=store, run_id="r3"))
    await cancel_when(task, running.is_set)
    assert await snapshot(session, store, "r3") == ([], None)
    assert RAN == []


async def test_cancel_while_a_non_idempotent_tool_runs_keeps_only_the_journal(hub, backends):
    from operonx_agents import tool

    running = asyncio.Event()

    @tool(idempotent=False)
    async def transfer(amount: int) -> str:
        """Move money."""
        running.set()
        await asyncio.sleep(5)
        RAN.append("transfer")
        return "moved"

    session, store = backends
    agent, _ = make(hub, asks(("transfer", {"amount": 5})), says("ok"), tools=[transfer])
    task = asyncio.ensure_future(
        Runner.run(agent, "pay", session=session, store=store, run_id="r4")
    )
    await cancel_when(task, running.is_set)
    items, state = await snapshot(session, store, "r4")
    assert items == [], "the session never holds half a turn"
    assert state["turn"] == 0 and state["pending"]["inflight"] == ["t0_0"]

    res = await Runner.resume(agent, "r4", store=store, session=session)
    assert res.status == "completed" and RAN == []
    unknown = res.messages[2]
    assert unknown["content"] == OUTCOME_UNKNOWN.format(name="transfer")
    assert roles(await session.get_items()) == ["user", "assistant", "tool", "assistant"]


async def test_a_cancel_during_the_commit_lets_the_whole_turn_land(hub):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowSession(InMemorySession):
        async def add_items(self, items):
            entered.set()
            await release.wait()
            await super().add_items(items)

    session, store = SlowSession(), InMemoryStateStore()
    agent, _ = make(hub, asks(("echo", {"a": 1})), says())
    task = asyncio.ensure_future(Runner.run(agent, "hi", session=session, store=store, run_id="r5"))
    await until(entered.is_set)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done(), "the commit is waited for"
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert roles(await session.get_items()) == ["user", "assistant", "tool"]
    assert (await store.load("r5")).turn == 1
