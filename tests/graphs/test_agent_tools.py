"""The KB as operonx-agents tools (PLAN K7): kb_search / kb_read, scoped by the caller."""

import asyncio
import json

import pytest

pytest.importorskip("operonx_agents")

from operonx_agents import Agent, Model, RunContext, Runner  # noqa: E402
from operonx_agents.testing import ScriptedLLM, asks, says, scripted  # noqa: E402

from operonx_kb.tools import kb_tools  # noqa: E402

POLICY = "# Leave policy\n\n## Annual leave\n\nEvery employee has twelve days of annual leave per year.\n"
TRAVEL = (
    "# Travel policy\n\n## Taxi\n\nTaxi fares on business trips are refunded up to fifty euros.\n"
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def loaded(kbx, tmp_path):
    for name, text, dept in (("policy.md", POLICY, "hr"), ("travel.md", TRAVEL, "finance")):
        (tmp_path / name).write_text(text, encoding="utf-8")
        run(kbx.add("docs", str(tmp_path / name), key=name, metadata={"dept": dept}))
    return kbx


def ctx(**deps):
    from types import SimpleNamespace

    return RunContext(deps=SimpleNamespace(**deps))


def test_search_then_read_return_cited_passages(loaded):
    tools = kb_tools(loaded, "docs")
    assert tools.names == ["kb_search", "kb_read"]
    assert all(t.spec.readonly for t in tools)
    hits = run(tools.get("kb_search")(ctx(), "how many days of annual leave", k=2))
    assert hits[0]["key"] == "policy.md" and "twelve days" in hits[0]["text"]
    assert hits[0]["section"].endswith("Annual leave") and len(hits) <= 2
    page = run(tools.get("kb_read")(ctx(), hits[0]["id"]))
    assert page["key"] == "policy.md" and "twelve days" in page["text"]


def test_the_scope_is_the_callers_and_read_checks_it_too(loaded):
    finance_id = run(kb_tools(loaded, "docs").get("kb_search")(ctx(), "taxi fares", k=1))[0]["id"]
    scoped = kb_tools(loaded, "docs", scope=lambda c: {"fields": {"dept": c.deps.dept}})
    hr = ctx(dept="hr")
    keys = {h["key"] for h in run(scoped.get("kb_search")(hr, "taxi fares refunded", k=10))}
    assert keys == {"policy.md"}  # finance's document is out of this user's scope
    denied = run(scoped.get("kb_read")(hr, finance_id))  # an id learned elsewhere reads nothing
    assert "error" in denied and "text" not in denied
    assert "text" in run(scoped.get("kb_read")(ctx(dept="finance"), finance_id))
    assert "error" in run(scoped.get("kb_read")(hr, "ch_made_up"))


def test_the_model_cannot_widen_the_scope_or_the_page_size(loaded):
    search = kb_tools(loaded, "docs", scope={"fields": {"dept": "hr"}}, max_k=3).get("kb_search")
    props = search.spec.definition()["function"]["parameters"]["properties"]
    assert set(props) == {"query", "k"}  # no filter argument to fill in
    assert len(run(search(ctx(), "policy", k=50))) <= 3


def test_an_agent_searches_with_the_tools(loaded):
    tools = kb_tools(loaded, "docs")

    def answer(messages, params):
        found = json.loads(messages[-1]["content"])
        return says(f"{found[0]['text']} [{found[0]['key']}]")

    llm = ScriptedLLM(asks(("kb_search", {"query": "annual leave days"})), answer)
    with scripted(assistant=llm):
        res = run(Runner.run(Agent(name="hr", model=Model("assistant"), tools=[tools]),
                             "How many days of leave do I get?"))  # fmt: skip
    assert "twelve days" in res.output and "[policy.md]" in res.output
