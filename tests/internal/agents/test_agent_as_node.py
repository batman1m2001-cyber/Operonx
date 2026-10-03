"""An agent is one node in a workflow: the next op reads its answer.

Before, an agent placed inside a graph lost the question it was given and
doubled every message of its conversation, and it had no output carrying
its answer — that was reachable only through ``agent_result`` after the
whole run. Now the agent graph ends on ``final``, and its ``messages`` are
the same conversation a standalone run holds.
"""

from __future__ import annotations

import pytest

from operonx import END, START, Operon, graph, op
from operonx.agents import agent_result, build_react_agent, tool
from operonx.agents.tool import clear_registry

pytestmark = pytest.mark.unit

CALL = {
    "id": "c1",
    "type": "function",
    "function": {"name": "lookup_company", "arguments": '{"q": "Lotus"}'},
}


@pytest.fixture(autouse=True)
def _tools():
    clear_registry()

    @tool(
        name="lookup_company",
        description="Look up a company.",
        schema={"type": "object", "properties": {"q": {"type": "string"}}},
        readonly=True,
    )
    def lookup_company(q: str) -> dict:
        return {"fact": "bonded warehouses in Hai Phong"}

    yield
    clear_registry()


@op
def scripted(messages: list) -> dict:
    """Turn 1 asks for the tool; turn 2 answers."""
    if not any(m.get("role") == "tool" for m in messages):
        return {
            "assistant_message": [{"role": "assistant", "content": "", "tool_calls": [CALL]}],
            "tool_calls": [CALL],
            "done": False,
        }
    return {
        "assistant_message": [{"role": "assistant", "content": "Lotus runs bonded warehouses."}],
        "tool_calls": [],
        "done": True,
    }


@op
def ask(company: str) -> dict:
    return {"messages": [{"role": "user", "content": f"research {company}"}]}


@op
def use(final: dict = None, messages: list = None) -> dict:
    return {"answer": (final or {}).get("content"), "roles": [m["role"] for m in messages or []]}


def roles(messages):
    return [m["role"] for m in messages]


@graph
def brief(company):
    a = ask(company=company)
    research = build_react_agent(call_model=scripted, max_turns=4)(messages=a["messages"])
    u = use(final=research["final"], messages=research["messages"])
    START >> a >> research >> u >> END


async def test_the_next_op_reads_the_agents_answer():
    out = await Operon(brief, params={"company": None}).run(inputs={"company": "Lotus"})
    assert "$errors" not in out
    assert out["answer"] == "Lotus runs bonded warehouses."


async def test_nested_conversation_matches_a_standalone_run():
    nested = await Operon(brief, params={"company": None}).run(inputs={"company": "Lotus"})

    alone = build_react_agent(call_model=scripted, max_turns=4)(messages=None)
    out = await Operon(alone).run(
        inputs={"messages": [{"role": "user", "content": "research Lotus"}]}
    )
    standalone = roles(agent_result(out, alone)["messages"])

    assert standalone == ["user", "assistant", "tool", "assistant"]
    assert nested["roles"] == standalone  # the question kept, nothing doubled


async def test_standalone_run_also_returns_final():
    alone = build_react_agent(call_model=scripted, max_turns=4)(messages=None)
    out = await Operon(alone).run(
        inputs={"messages": [{"role": "user", "content": "research Lotus"}]}
    )
    assert out["final"]["content"] == "Lotus runs bonded warehouses."
    assert agent_result(out, alone)["final"] == out["final"]
