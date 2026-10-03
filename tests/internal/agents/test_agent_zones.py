"""The ReAct turn in zones: ``build_context`` → ``model`` → ``run_tools``.

``run_tools`` gathers its tool messages with a ``.collect()`` inside the
subgraph, and the loop writes the list straight into ``messages``. Until
1.12.2 such a collect handed its result up twice, so these tests count:
every tool call is dispatched once and answered by exactly one tool
message, over several calls per turn and several turns. Tool messages
carry no ``id``, so ``add_messages`` would append a duplicate rather than
upsert it — a doubled collect shows up here.
"""

from __future__ import annotations

from collections import Counter

import pytest

from operonx import END, START, Operon, graph, op
from operonx.agents import agent_result, build_react_agent, tool
from operonx.agents.tool import clear_registry

pytestmark = pytest.mark.unit

NUM = {"type": "object", "properties": {"a": {"type": "number"}}, "required": ["a"]}


@pytest.fixture(autouse=True)
def runs():
    clear_registry()
    seen: list = []

    @tool(name="echo", description="Echo a number.", schema=NUM, readonly=True)
    async def echo(a: float) -> dict:
        seen.append(a)
        return {"a": a}

    yield seen
    clear_registry()


def calls(turn: int, n: int) -> list:
    return [{"id": f"t{turn}_{i}", "name": "echo", "args": {"a": turn * 10 + i}} for i in range(n)]


def scripted(script):
    """Turn i asks for ``script[i]`` calls; past the end it answers."""
    state = {"i": 0}

    @op
    def call_model(messages: list = None) -> dict:
        i = state["i"]
        state["i"] += 1
        asked = script[i] if i < len(script) else []
        return {
            "assistant_message": [
                {"id": f"a{i}", "role": "assistant", "content": "" if asked else "done"}
                | ({"tool_calls": asked} if asked else {})
            ],
            "tool_calls": asked,
            "done": not asked,
        }

    return call_model


async def run(script, max_turns=25):
    built = build_react_agent(call_model=scripted(script), max_turns=max_turns)(messages=None)
    out = await Operon(built).run(inputs={"messages": [{"role": "user", "content": "go"}]})
    return out, agent_result(out, built)


@pytest.mark.parametrize("per_turn", [[1], [3], [3, 2], [1, 4, 2], [8, 8], [9]])
async def test_each_call_is_answered_exactly_once(per_turn, runs):
    script = [calls(t, n) for t, n in enumerate(per_turn)]
    out, result = await run(script)
    assert "$errors" not in out

    asked = [c["id"] for turn in script for c in turn]
    answered = [m["tool_call_id"] for m in result["messages"] if m.get("role") == "tool"]
    assert Counter(answered) == Counter(asked)  # none missing, none doubled
    assert len(runs) == len(asked)  # each tool ran once
    assert result["turns"] == len(per_turn) + 1
    assert result["final"]["content"] == "done"


async def test_tool_messages_follow_the_turn_that_asked(runs):
    script = [calls(0, 3), calls(1, 2)]
    _, result = await run(script)
    roles = [m["role"] for m in result["messages"]]
    assert roles == ["user", "assistant"] + ["tool"] * 3 + ["assistant"] + ["tool"] * 2 + [
        "assistant"
    ]


async def test_budget_semantics_are_unchanged(runs):
    script = [calls(t, 2) for t in range(10)]
    _, result = await run(script, max_turns=3)
    assert result["turns"] == 3
    assert result["stopped_early"] is True
    assert result["truncated"] is False
    tool_ids = [m["tool_call_id"] for m in result["messages"] if m.get("role") == "tool"]
    # two turns dispatched; the last turn's calls answered "not run", once each
    assert Counter(tool_ids) == Counter(c["id"] for t in range(3) for c in calls(t, 2))
    assert len(runs) == 4


@op
def ask(q: str) -> dict:
    return {"messages": [{"role": "user", "content": q}]}


@op
def use(final: dict = None, messages: list = None) -> dict:
    return {
        "answer": (final or {}).get("content"),
        "tools": sum(1 for m in messages or [] if m.get("role") == "tool"),
    }


async def test_as_a_node_named_after_its_variable_and_shows_final(runs):
    script = [calls(0, 3), calls(1, 2)]
    holder = {}

    @graph
    def outer(q):
        a = ask(q=q)
        research = build_react_agent(call_model=scripted(script), max_turns=5)(
            messages=a["messages"]
        )
        holder["node"] = research
        u = use(final=research["final"], messages=research["messages"])
        START >> a >> research >> u >> END

    out = await Operon(outer, params={"q": None}).run(inputs={"q": "go"})
    assert "$errors" not in out
    assert out["answer"] == "done"
    assert out["tools"] == 5
    assert holder["node"].name == "research"
    assert tuple(holder["node"].show_keys) == ("final",)


def test_the_factory_still_reads_as_a_graph():
    """The serve layer and Studio's extraction inspect a declared entry: its
    parameters become input ports, and ``_operonx_graph`` tells a ``@graph``
    from a factory. The wrapper must not hide either."""
    import inspect

    factory = build_react_agent(call_model=scripted([]))
    assert list(inspect.signature(factory).parameters) == ["messages"]
    assert getattr(factory, "_operonx_graph", False) is True
    assert factory.__name__ == "react"


def test_an_explicit_show_keys_wins(runs):
    node = build_react_agent(call_model=scripted([]))(messages=None, show_keys="messages")
    assert tuple(node.show_keys) == ("messages",)


@pytest.mark.xfail(
    strict=True,
    reason="A call whose dispatch failed at the op level — not a tool exception, "
    "which becomes an error tool message — has no tool message, and nothing "
    "answers it: the next turn runs on a history with that call unanswered.",
)
async def test_a_turn_whose_every_dispatch_fails_still_continues(runs):
    import asyncio

    from operonx.agents import ToolPolicy
    from operonx.checkpoint import bind_interrupt_bus

    @tool(name="wipe", description="Delete.", schema=NUM, destructive=True)
    async def wipe(a: float) -> dict:
        return {"gone": a}

    built = build_react_agent(
        call_model=scripted([[{"id": "w0", "name": "wipe", "args": {"a": 1}}]]),
        max_turns=4,
        approval_timeout=5,
        policy=ToolPolicy(destructive="ask"),
    )(messages=None)
    handle = Operon(built).start(inputs={"messages": [{"role": "user", "content": "go"}]})

    def broken_sink(event):
        raise RuntimeError("the approval channel is down")

    bind_interrupt_bus(handle.state, sink=broken_sink)
    await asyncio.wait_for(handle.result(), timeout=30)
    result = agent_result(handle.state, built)
    assert result["turns"] == 2
    assert result["final"]["content"] == "done"
    answers = [m for m in result["messages"] if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in answers] == ["w0"]
    assert answers[0]["status"] == "error"
