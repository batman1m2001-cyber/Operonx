"""The KB over MCP (operonx_kb.mcp): the same kb_search / kb_read, scoped by the server."""

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from operonx_kb.mcp import mcp_server  # noqa: E402

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


def value(result):
    """A tool result's value: the structured content, else the JSON of its text."""
    structured = result.structured_content
    if structured is not None:
        return structured.get("result", structured)
    return json.loads(result.content[0].text)


def test_the_server_lists_two_read_only_tools(loaded):
    server = mcp_server(loaded, "docs")
    tools = {t.name: t for t in run(server.list_tools())}
    assert set(tools) == {"kb_search", "kb_read"}
    assert all(t.annotations.read_only_hint for t in tools.values())


def test_search_then_read_over_mcp(loaded):
    server = mcp_server(loaded, "docs")
    hits = value(run(server.call_tool("kb_search", {"query": "annual leave days", "k": 2})))
    assert hits[0]["key"] == "policy.md" and "twelve days" in hits[0]["text"]
    passage = value(run(server.call_tool("kb_read", {"id": hits[0]["id"]})))
    assert passage["key"] == "policy.md" and "twelve days" in passage["text"]


def test_the_servers_scope_holds_for_search_and_read(loaded):
    everyone = mcp_server(loaded, "docs")
    finance = mcp_server(loaded, "docs", scope={"fields": {"dept": "finance"}})
    hits = value(run(finance.call_tool("kb_search", {"query": "annual leave days", "k": 5})))
    assert {h["key"] for h in hits} <= {"travel.md"}
    hr_id = value(run(everyone.call_tool("kb_search", {"query": "annual leave", "k": 1})))[0]["id"]
    refused = value(run(finance.call_tool("kb_read", {"id": hr_id})))
    assert "error" in refused and "no passage" in refused["error"]


def test_an_unknown_collection_fails_when_the_server_is_built(loaded):
    with pytest.raises(Exception):
        mcp_server(loaded, "nope")
