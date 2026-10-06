"""MCP against the official reference server, run locally.

``@modelcontextprotocol/server-everything`` (pinned) over streamable HTTP
and over stdio: the server the MCP project ships to exercise its clients.
It negotiates the initialize-handshake revision, where the Python
fixtures in ``test_mcp.py`` negotiate the stateless 2026-07-28 one, so the
two files cover both. Needs ``npx`` (the first run downloads the package).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess

import pytest

pytest.importorskip("mcp", reason="needs operonx-agents[mcp]")

from operonx_agents import Agent, Model, Runner, dispatch  # noqa: E402
from operonx_agents.tools.mcp import MCPClient, MCPServer, MCPToolset  # noqa: E402
from tests.fakes import ScriptedLLM, completion  # noqa: E402
from tests.test_mcp import free_port, wait_for_port  # noqa: E402

PACKAGE = "@modelcontextprotocol/server-everything@2026.8.31"

pytestmark = pytest.mark.skipif(shutil.which("npx") is None, reason="needs node's npx")


def _node_env() -> dict:
    """Node 18 has no global ``crypto``, which the server's HTTP transport
    uses for session ids; 19+ has it, and drops the flag."""
    out = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout
    major = int(out.strip().lstrip("v").split(".")[0])
    return {"NODE_OPTIONS": "--experimental-global-webcrypto"} if major < 19 else {}


@pytest.fixture(scope="module")
def everything_url():
    port = free_port()
    proc = subprocess.Popen(
        ["npx", "-y", PACKAGE, "streamableHttp"],
        env={**os.environ, **_node_env(), "PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,  # npx runs the server as its child: stop the group
    )
    try:
        wait_for_port(port, proc, timeout=180)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(10)


@pytest.fixture(params=["http", "stdio"])
def everything(request):
    if request.param == "http":
        return MCPServer("everything", url=request.getfixturevalue("everything_url"))
    return MCPServer("everything", command="npx", args=["-y", PACKAGE, "stdio"], env=_node_env())


async def test_it_connects_and_lists_the_reference_tools(everything):
    async with MCPClient(everything) as client:
        assert client.protocol_version == "2025-11-25"
        assert {"echo", "get-sum", "get-structured-content"} <= {t.name for t in client.tools}


async def test_calls_text_and_values(everything):
    async with MCPClient(everything) as client:
        assert await client.call("echo", {"message": "hi"}) == "Echo: hi"
        assert await client.call("get-sum", {"a": 1, "b": 2}) == "The sum of 1 and 2 is 3."
        weather = await client.call_value("get-structured-content", {"location": "New York"})
        assert set(weather) == {"temperature", "conditions", "humidity"}
        assert "image omitted" in await client.call("get-tiny-image", {})


async def test_annotations_gate_as_declared(everything):
    async with await MCPToolset.connect(everything) as tools:
        echo = tools.get("everything__echo").spec
        research = tools.get("everything__simulate-research-query").spec
        assert (echo.readonly, echo.destructive) == (True, False)
        # readOnlyHint false, destructiveHint false: runs, but not with its siblings.
        assert (research.readonly, research.destructive, research.sequential) == (
            False,
            False,
            True,
        )


async def test_an_agent_drives_the_reference_server(hub, everything):
    calls = [
        {"id": "c1", "name": "everything__get-sum", "args": {"a": 20, "b": 22}},
        {"id": "c2", "name": "everything__echo", "args": {"message": "done"}},
    ]
    llm = ScriptedLLM(
        completion("", tool_calls=calls, finish_reason="tool_calls"), completion("42")
    )
    hub(m=llm)
    async with await MCPToolset.connect(everything, allow=["get-sum", "echo"]) as tools:
        agent = Agent(name="calc", model=Model("m"), tools=[tools])
        res = await Runner.run(agent, "what is 20 + 22?")
    assert (res.status, res.output) == ("completed", "42")
    answers = {m["tool_call_id"]: m["content"] for m in res.messages if m["role"] == "tool"}
    assert answers == {"c1": "The sum of 20 and 22 is 42.", "c2": "Echo: done"}
    offered = [t["function"]["name"] for t in llm.requests[0]["tools"]]
    assert offered == ["everything__echo", "everything__get-sum"]


async def test_a_bad_argument_never_reaches_the_server(everything):
    async with await MCPToolset.connect(everything, allow=["get-sum"]) as tools:
        (message,) = await dispatch(
            [{"id": "1", "name": "everything__get-sum", "args": {"a": "one", "b": 2}}], tools
        )
    assert message["status"] == "error" and message["content"].startswith(
        "Error: invalid arguments for 'everything__get-sum': a:"
    )
