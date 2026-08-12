"""A real model driving real MCP tools.

`test_mcp.py` calls the client directly and through `build_dispatch`. That
proves the protocol layer. It does not prove the piece that actually
matters in production: a model, given MCP tool definitions it has never
seen, deciding to call one and reading what comes back.

Three seams meet here and nowhere else — the server's JSON Schema becoming
a tool definition the provider accepts, the model's arguments surviving
the round trip, and the server's text landing in the conversation without
breaking the next turn.

    export OPERONX_TEST_LLM_URL=... OPERONX_TEST_LLM_KEY=... OPERONX_TEST_LLM_MODEL=...
    uv run pytest tests/internal/agents/test_live_mcp.py -m integration
"""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
from pathlib import Path

import pytest

from operonx.agents import (
    agent_result,
    build_react_agent,
    clear_registry,
    get_tool_definitions,
)
from operonx.agents.ops.model_ops import make_llm_caller
from operonx.core import Operon
from operonx.core.registry import ResourceHub

pytestmark = pytest.mark.integration

pytest.importorskip("mcp", reason="needs operonx[mcp]")

from operonx.agents.mcp import MCPServer, connect_mcp  # noqa: E402

URL = os.getenv("OPERONX_TEST_LLM_URL")
KEY = os.getenv("OPERONX_TEST_LLM_KEY", "")
MODEL = os.getenv("OPERONX_TEST_LLM_MODEL", "")

requires_llm = pytest.mark.skipif(
    not (URL and MODEL),
    reason="set OPERONX_TEST_LLM_URL / _KEY / _MODEL to run live MCP tests",
)

RESOURCE = "mcplive"
SERVER = Path(__file__).parent / "mcp_fixtures" / "echo_server.py"


@pytest.fixture(scope="module", autouse=True)
def _hub(tmp_path_factory):
    if not (URL and MODEL):
        yield
        return
    path = tmp_path_factory.mktemp("mcplive") / "resources.yaml"
    path.write_text(
        textwrap.dedent(f"""
            llm:
              {RESOURCE}:
                api_type: openai
                api_key: {KEY or "unused"}
                base_url: {URL}
                model: {MODEL}
        """).strip(),
        encoding="utf-8",
    )
    try:
        previous = ResourceHub.instance()
    except RuntimeError:
        previous = None
    ResourceHub.set_instance(ResourceHub.from_yaml(str(path)))
    yield
    if previous is not None:
        ResourceHub.set_instance(previous)


# `explode` is the only fixture tool marked destructive, so it is the only
# one the default policy gates. That is the point of the annotation mapping
# — and forgetting it is why an earlier version of this file sat out a
# 300-second approval timeout per test with nobody to answer.


@pytest.fixture
async def mcp_agent():
    """A live server, its tools registered, and an agent that can see them."""
    clear_registry()
    client, names = await connect_mcp(
        MCPServer(name="echo", command=sys.executable, args=[str(SERVER)])
    )
    try:
        yield client, names
    finally:
        await client.close()
        clear_registry()


def _build(names, *, max_turns=8, **kw):
    caller = make_llm_caller(
        RESOURCE, tools=get_tool_definitions(names), max_tokens=768, temperature=0
    )
    return build_react_agent(call_model=caller, max_turns=max_turns, **kw)(messages=None)


async def _run(agent, text, timeout=240):
    result = await asyncio.wait_for(
        Operon(agent).run(inputs={"messages": [{"role": "user", "content": text}]}),
        timeout=timeout,
    )
    return agent_result(result, agent)


@requires_llm
@pytest.mark.asyncio
async def test_the_provider_accepts_a_servers_schema(mcp_agent):
    """The server's inputSchema becomes a tool definition sent to the
    provider. A schema the provider rejects fails the whole request, not
    just the tool — and the error names the request, not the server."""
    _client, _names = mcp_agent
    defs = get_tool_definitions(["echo__add"])
    assert defs[0]["function"]["parameters"]["type"] == "object"
    result = await _run(_build(["echo__add"]), "What is 17 plus 25? Use the tool.")
    assert (result["final"] or {}).get("role") == "assistant"


@requires_llm
@pytest.mark.asyncio
async def test_a_model_calls_an_mcp_tool_and_uses_the_result(mcp_agent):
    """The whole point of the module, exercised the way it will be used."""
    _client, _names = mcp_agent
    result = await _run(
        _build(["echo__add"]), "Use the add tool to compute 17 + 25, then tell me the number."
    )
    answer = (result["final"] or {}).get("content", "")
    assert "42" in answer, f"expected 42 from the MCP tool, got: {answer!r}"


@requires_llm
@pytest.mark.asyncio
async def test_typed_arguments_survive_the_model_and_the_protocol(mcp_agent):
    """`add(a: int, b: int)` — the model emits JSON, the SDK validates
    against the server's schema. A synthesised signature that got the
    types wrong would surface here as a validation error from the server
    rather than an answer."""
    _client, _names = mcp_agent
    result = await _run(
        _build(["echo__add"]), "Add 1000 and 234 using the tool. Reply with just the number."
    )
    assert "1234" in (result["final"] or {}).get("content", "")


@requires_llm
@pytest.mark.asyncio
async def test_mcp_json_output_does_not_break_the_next_turn(mcp_agent):
    """`braces` returns `{"city": "Hanoi", "temp": 30}`. Through the old
    `prompt=` path that killed the following model call."""
    _client, _names = mcp_agent
    result = await _run(
        _build(["echo__braces"]),
        "Call the braces tool and tell me which city it mentions.",
    )
    assert (result["final"] or {}).get("role") == "assistant"
    assert result["turns"] >= 2, "the model must have called the tool and come back"


@requires_llm
@pytest.mark.asyncio
async def test_a_failing_mcp_tool_lets_the_model_recover(mcp_agent):
    """The server reports failure in-band. If that reads as success the
    model believes a lie; if it ends the run the model never gets to
    explain. It must become a tool message and the turn must continue."""
    from operonx.agents import ToolPolicy

    _client, _names = mcp_agent
    result = await _run(
        _build(
            ["echo__explode", "echo__echo"], policy=ToolPolicy(default="allow", destructive="allow")
        ),
        "Call the explode tool with reason 'test'. If it fails, say the word FAILED and stop.",
    )
    assert (result["final"] or {}).get("role") == "assistant"
    tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
    assert tool_msgs, "the failure must reach the model as a tool message"
    assert any(m.get("status") == "error" for m in tool_msgs), (
        "an in-band MCP error must be marked as an error, not a result"
    )


@requires_llm
@pytest.mark.asyncio
async def test_local_and_mcp_tools_coexist(mcp_agent):
    """The namespace exists so a server cannot shadow a local tool. With
    both offered, the model has to pick — and both have to work."""
    from operonx.agents import tool

    _client, _names = mcp_agent

    @tool(
        name="local_secret",
        description="Return the local secret word.",
        schema={"type": "object", "properties": {}},
        readonly=True,
    )
    async def local_secret() -> dict:
        return {"word": "marmalade"}

    result = await _run(
        _build(["echo__add", "local_secret"], max_turns=10),
        "First call local_secret for the secret word, then use add to compute 2+2. Report both.",
    )
    blob = str(result["messages"])
    assert "marmalade" in blob, "the local tool was never reached"
    assert "4" in (result["final"] or {}).get("content", "")
