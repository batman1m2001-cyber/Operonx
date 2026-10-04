"""The event stream is complete and ordered, and ``stream()`` gives what
``run()`` gives: a property test over scripted models.

Hypothesis draws a conversation (turns of tool calls — good, unknown,
badly-typed, failing — then an answer, or a model error), and caps. Each
script runs once through ``Runner.stream`` and once through
``Runner.run``; the events must describe the run exactly:

- ``RunStarted`` first, ``RunFinished`` last, once each, carrying the
  same result ``run()`` returned;
- turns numbered 1, 2, …; a ``TurnFinished`` for every committed turn and
  only for those; only the last turn may lack one;
- each turn's ``TextDelta`` pieces join into its answer's text;
- one ``ToolCallFinished`` per tool message the run committed, with its
  ``ok``; a ``ToolCallStarted`` before it exactly when the tool ran.
"""

from __future__ import annotations

import asyncio
from typing import Any, List

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from operonx.core.registry.resource_hub import ResourceHub

from operonx_agents import (
    Agent,
    Model,
    RunFinished,
    Runner,
    RunStarted,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    TurnStarted,
    UsageLimits,
    tool,
)
from tests.fakes import FakeHub, ScriptedLLM, StatusError, completion

RAN: List[str] = []


@tool(readonly=True)
async def echo(a: int) -> dict:
    """Echo."""
    RAN.append("echo")
    await asyncio.sleep(0)
    return {"a": a}


@tool
async def boom() -> str:
    """Raises."""
    RAN.append("boom")
    raise RuntimeError("disk on fire")


TOOLS = [echo, boom]

CALL = st.sampled_from(
    [
        ("echo", {"a": 1}),  # runs
        ("echo", {"a": "x"}),  # bad arguments: never runs
        ("boom", {}),  # runs, fails
        ("nope", {}),  # unknown tool
    ]
)
TURN = st.lists(CALL, min_size=1, max_size=3)
TEXT = st.text(alphabet="abc xyz", min_size=0, max_size=12)


@st.composite
def scripts(draw):
    turns = draw(st.lists(st.tuples(TURN, TEXT), max_size=4))
    replies: List[Any] = []
    for t, (specs, text) in enumerate(turns):
        calls = [{"id": f"t{t}_{i}", "name": n, "args": a} for i, (n, a) in enumerate(specs)]
        replies.append(completion(text, tool_calls=calls, finish_reason="tool_calls"))
    ending = draw(st.sampled_from(["answer", "error"]))
    replies.append(completion(draw(TEXT) or "done") if ending == "answer" else StatusError(400))
    limits = UsageLimits(
        turns=draw(st.integers(1, 6)),
        tool_calls=draw(st.one_of(st.none(), st.integers(1, 6))),
        total_tokens=draw(st.one_of(st.none(), st.integers(20, 80))),
    )
    return replies, limits


async def both(replies, limits):
    out = []
    for mode in ("stream", "run"):
        ResourceHub.set_instance(FakeHub(m=ScriptedLLM(*replies, *[replies[-1]])))
        agent = Agent(name="a", model=Model("m"), tools=TOOLS, limits=limits)
        if mode == "stream":
            events = [e async for e in Runner.stream(agent, "go", run_id="r")]
            out.append(events)
        else:
            out.append(await Runner.run(agent, "go", run_id="r"))
    return out


def check(events, result) -> None:
    assert isinstance(events[0], RunStarted) and isinstance(events[-1], RunFinished)
    assert sum(isinstance(e, (RunStarted, RunFinished)) for e in events) == 2
    assert events[-1].result.to_dict() == result.to_dict(), "stream() differs from run()"

    # Turns: numbered from 1, each finished one committed, only the last may be open.
    starts = [e.turn for e in events if isinstance(e, TurnStarted)]
    finishes = [e.turn for e in events if isinstance(e, TurnFinished)]
    assert starts == list(range(1, len(starts) + 1))
    assert finishes == list(range(1, result.turns + 1))
    assert len(starts) - len(finishes) in (0, 1)

    # Per turn: the text deltas join into its answer; tool events match its messages.
    assistants = [m for m in result.new_items if m["role"] == "assistant"]
    tools = [m for m in result.new_items if m["role"] == "tool"]
    turn, text, open_calls = 0, {}, {}
    finished: List[ToolCallFinished] = []
    for event in events[1:-1]:
        if isinstance(event, TurnStarted):
            turn = event.turn
            text[turn] = ""
        elif isinstance(event, TurnFinished):
            assert event.turn == turn
        elif isinstance(event, TextDelta):
            text[turn] += event.text
        elif isinstance(event, ToolCallStarted):
            assert turn and event.call_id not in open_calls
            open_calls[event.call_id] = event
        elif isinstance(event, ToolCallFinished):
            assert turn
            finished.append(event)
        else:
            assert type(event).__name__ in ("ReasoningDelta", "Compacted"), event
    for n, message in enumerate(assistants, start=1):
        assert text[n] == message["content"]
    # Concurrent calls finish in any order; each is finished exactly once.
    assert sorted(f.call_id for f in finished) == sorted(m["tool_call_id"] for m in tools)
    by_id = {m["tool_call_id"]: m for m in tools}
    for f in finished:
        assert f.ok == (by_id[f.call_id]["status"] == "success")
        # Started exactly when the tool ran: it exists and its arguments validated.
        message = by_id[f.call_id]["content"]
        refused = message.startswith(("Error: no tool", "Error: invalid arguments", "Not run"))
        assert (f.call_id in open_calls) != refused, message


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(scripts())
def test_events_are_complete_and_ordered_and_stream_equals_run(script):
    replies, limits = script
    try:
        events, result = asyncio.run(both(replies, limits))
    finally:
        ResourceHub.reset_instance()
    check(events, result)
