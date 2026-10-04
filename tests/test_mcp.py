"""The MCP client and toolset, against real MCP servers over stdio and
streamable HTTP.

Ported from ``operonx/tests/internal/agents/test_mcp.py`` and
``test_mcp_values.py``. Deliberately not mocked: a mock would verify the
code against the contract we assumed, and ``operonx.agents`` paid for that
four times. ``mcp_fixtures/echo_server.py`` speaks the actual protocol;
every client-level test runs once per transport (stdio: the server is a
child process; http: one streamable-HTTP server for the module).

The registry tests became toolset tests: there is no registry to land in,
leak from, or shadow, so "registered" reads "in the toolset", and the
all-or-nothing and unregistering cases are about a toolset being built
whole or not at all and a closed connection's tools failing loudly.
"""

from __future__ import annotations

import asyncio
import gc
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="needs operonx-agents[mcp]")

from operonx_agents import (  # noqa: E402
    Agent,
    InMemoryStateStore,
    Model,
    Runner,
    ToolPolicy,
    Toolset,
    dispatch,
    tool,
)
from operonx_agents.tools.dispatch import NO_APPROVER  # noqa: E402
from operonx_agents.tools.mcp import (  # noqa: E402
    MAX_DESCRIPTION_CHARS,
    MCPClient,
    MCPError,
    MCPServer,
    MCPToolset,
)
from tests.fakes import ScriptedLLM, completion  # noqa: E402

FIXTURES = Path(__file__).parent / "mcp_fixtures"
SERVER = FIXTURES / "echo_server.py"
CLASH_SERVER = FIXTURES / "clash_server.py"
VALUES_SERVER = FIXTURES / "values_server.py"


def _mcp_version() -> tuple:
    import importlib.metadata as metadata

    parts = []
    for chunk in metadata.version("mcp").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert proc.poll() is None, proc.stderr.read().decode() if proc.stderr else "exited"
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"nothing listened on {port} within {timeout}s")


@pytest.fixture(scope="module")
def http_echo():
    """The echo server over streamable HTTP, for the module."""
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "http", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for_port(port, proc)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(10)


@pytest.fixture(params=["stdio", "http"])
def make_server(request):
    """``make_server(**kw)`` -> an echo :class:`MCPServer` on this transport."""
    if request.param == "stdio":
        return lambda **kw: MCPServer(
            **{"name": "echo", "command": sys.executable, "args": [str(SERVER)], **kw}
        )
    url = request.getfixturevalue("http_echo")
    return lambda **kw: MCPServer(**{"name": "echo", "url": url, **kw})


def _stdio(**kw) -> MCPServer:
    kw.setdefault("name", "echo")
    return MCPServer(command=sys.executable, args=[str(SERVER)], **kw)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _until(predicate, timeout: float = 15.0) -> None:
    """Poll for a condition: a slow machine makes this wait longer, never flake."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        await asyncio.sleep(0.02)


@pytest.fixture
async def client(make_server):
    c = await MCPClient(make_server()).connect()
    yield c
    await c.close()


@pytest.fixture
async def echo_tools(make_server):
    tools = await MCPToolset.connect(make_server())
    yield tools
    await tools.close()


def _local_tool(name: str):
    @tool(name=name, description="a local tool")
    async def local() -> str:
        return "local"

    return local


class TestConfig:
    def test_a_nameless_server_is_refused(self):
        with pytest.raises(MCPError, match="needs a name"):
            MCPServer(name="  ", command="x")

    def test_a_server_needs_a_command_or_a_url(self):
        with pytest.raises(MCPError, match="needs a command to run"):
            MCPServer(name="fs", command="")
        with pytest.raises(MCPError, match="not both"):
            MCPServer(name="fs", command="x", url="http://x/mcp")

    def test_the_name_is_sanitised_because_it_becomes_a_tool_prefix(self):
        assert MCPServer(name="my server!", command="x").name == "my_server_"


class TestConnection:
    async def test_it_discovers_the_servers_tools(self, client):
        names = {t.name for t in client.tools}
        assert {"echo", "add", "explode"} <= names

    async def test_it_speaks_the_stateless_revision_to_a_server_that_does(self, client):
        assert client.protocol_version == "2026-07-28"

    async def test_a_bad_command_fails_with_the_server_named(self):
        bad = MCPServer(name="nope", command="definitely-not-a-real-binary-xyz")
        with pytest.raises(MCPError, match="nope"):
            await MCPClient(bad).connect()

    async def test_an_unreachable_url_fails_with_the_server_named(self):
        bad = MCPServer(name="gone", url=f"http://127.0.0.1:{free_port()}/mcp")
        with pytest.raises(MCPError, match="gone"):
            await MCPClient(bad).connect()

    async def test_close_is_idempotent(self, make_server):
        c = await MCPClient(make_server()).connect()
        await c.close()
        await c.close()

    async def test_calling_after_close_says_so(self, make_server):
        c = await MCPClient(make_server()).connect()
        await c.close()
        with pytest.raises(MCPError, match="not connected"):
            await c.call("echo", {"text": "hi"})

    async def test_it_works_as_a_context_manager(self, make_server):
        async with MCPClient(make_server()) as c:
            assert c.tools

    async def test_connecting_twice_is_refused(self, make_server):
        """A second connect() used to spawn a second server and orphan the
        first — close() only knew about the latest."""
        c = await MCPClient(make_server()).connect()
        try:
            with pytest.raises(MCPError, match="already connected"):
                await c.connect()
        finally:
            await c.close()


class TestTaskOwnership:
    """The transport and session enter anyio cancel scopes, which must be
    exited by the task that entered them. A client is routinely opened in
    one task and closed in another, so the caller's task cannot be the one
    that enters them. (stdio: the server process shows the shutdown.)"""

    async def test_closing_from_another_task_leaves_the_connecting_task_alone(self):
        shutdown, connected = asyncio.Event(), asyncio.Event()
        box: dict = {}

        async def lifespan() -> str:
            box["client"] = await MCPClient(_stdio()).connect()
            box["pid"] = int(await box["client"].call("pid", {}))
            connected.set()
            await shutdown.wait()
            return "finished"

        life = asyncio.create_task(lifespan())
        await asyncio.wait_for(connected.wait(), timeout=30)

        async def shutdown_handler() -> None:
            await box["client"].close()

        await asyncio.wait_for(asyncio.create_task(shutdown_handler()), timeout=30)
        assert not _alive(box["pid"]), "close() must still stop the server"
        shutdown.set()
        outcome = await asyncio.gather(life, return_exceptions=True)
        assert outcome == ["finished"], f"the connecting task was hit by close(): {outcome}"

    async def test_a_client_connected_in_a_finished_task_closes_cleanly(self):
        box: dict = {}

        async def opener() -> None:
            box["client"] = await MCPClient(_stdio()).connect()
            box["pid"] = int(await box["client"].call("pid", {}))

        await asyncio.create_task(opener())
        await box["client"].close()
        assert not _alive(box["pid"])

    async def test_an_abandoned_client_collected_by_gc_cancels_nothing(self):
        shutdown, dropped = asyncio.Event(), asyncio.Event()
        box: dict = {}

        async def lifespan() -> str:
            client = await MCPClient(_stdio()).connect()
            box["pid"] = int(await client.call("pid", {}))
            del client  # a leak, but one that must not bite anyone else
            dropped.set()
            await shutdown.wait()
            return "finished"

        life = asyncio.create_task(lifespan())
        await asyncio.wait_for(dropped.wait(), timeout=30)
        gc.collect()
        # The abandoned client's server is shut down by its own task.
        await _until(lambda: not _alive(box["pid"]))
        shutdown.set()
        outcome = await asyncio.gather(life, return_exceptions=True)
        assert outcome == ["finished"], f"GC of an abandoned client hit a live task: {outcome}"

    async def test_a_teardown_failure_is_raised_not_swallowed(self, monkeypatch):
        from mcp.client import Client

        original = Client.__aexit__

        async def broken_exit(self, *exc):
            await original(self, *exc)
            raise RuntimeError("teardown broke")

        monkeypatch.setattr(Client, "__aexit__", broken_exit)
        c = await MCPClient(_stdio()).connect()
        with pytest.raises(MCPError, match="teardown broke"):
            await c.close()
        await c.close()  # and it is still idempotent afterwards

    async def test_a_teardown_failure_does_not_mask_the_error_in_flight(self, monkeypatch):
        from mcp.client import Client

        original = Client.__aexit__

        async def broken_exit(self, *exc):
            await original(self, *exc)
            raise RuntimeError("teardown broke")

        monkeypatch.setattr(Client, "__aexit__", broken_exit)
        with pytest.raises(ValueError, match="the real error"):
            async with MCPClient(_stdio()):
                raise ValueError("the real error")


class TestCalling:
    async def test_a_call_returns_text(self, client):
        assert "echo: hello" in await client.call("echo", {"text": "hello"})

    async def test_typed_arguments_survive_the_round_trip(self, client):
        assert "7" in await client.call("add", {"a": 3, "b": 4})

    async def test_an_in_band_error_raises_rather_than_reading_as_an_answer(self, client):
        with pytest.raises(MCPError, match="reported an error"):
            await client.call("explode", {"reason": "boom"})

    async def test_the_error_carries_the_servers_message(self, client):
        with pytest.raises(MCPError, match="boom"):
            await client.call("explode", {"reason": "boom"})

    async def test_an_unanticipated_crash_still_raises(self, client):
        with pytest.raises(MCPError):
            await client.call("crash", {"reason": "secret-internal-detail"})

    @pytest.mark.skipif(_mcp_version() < (2, 1), reason="masking crashes landed in mcp 2.1")
    async def test_an_unanticipated_crash_does_not_leak_its_message(self, client):
        with pytest.raises(MCPError) as caught:
            await client.call("crash", {"reason": "secret-internal-detail"})
        assert "secret-internal-detail" not in str(caught.value)

    async def test_a_slow_call_times_out_and_names_the_tool(self, make_server):
        async with MCPClient(make_server(timeout=0.5)) as c:
            with pytest.raises(MCPError, match="echo__slow"):
                await c.call("slow", {"seconds": 5})

    async def test_an_unknown_tool_raises(self, client):
        with pytest.raises(MCPError):
            await client.call("no_such_tool", {})

    async def test_json_content_survives_intact(self, client):
        assert '{"city": "Hanoi", "temp": 30}' in await client.call("braces", {})


class TestToolset:
    async def test_tools_are_namespaced_by_server(self, echo_tools):
        assert {"echo__echo", "echo__add"} <= set(echo_tools.names)

    async def test_the_definitions_the_model_sees_are_well_formed(self, echo_tools):
        defn = next(d for d in echo_tools.definitions() if d["function"]["name"] == "echo__add")
        assert defn["function"]["description"]
        assert defn["function"]["parameters"]["type"] == "object"
        assert "a" in defn["function"]["parameters"]["properties"]

    async def test_a_tool_actually_calls_the_server(self, echo_tools):
        (message,) = await dispatch(
            [{"id": "1", "name": "echo__echo", "args": {"text": "through the toolset"}}],
            echo_tools,
        )
        assert message["status"] == "success"
        assert "echo: through the toolset" in message["content"]

    async def test_arguments_are_validated_before_the_wire(self, echo_tools):
        (message,) = await dispatch(
            [{"id": "1", "name": "echo__add", "args": {"a": "three", "b": 4}}], echo_tools
        )
        assert message["status"] == "error" and "a:" in message["content"]

    async def test_allow_restricts_the_set(self, client):
        assert MCPToolset(client, allow=["echo"]).names == ["echo__echo"]

    async def test_allowing_a_tool_the_server_lacks_raises(self, client):
        """Silently dropping it would hand the agent a narrower toolset
        than its author wrote, with nothing to explain why."""
        with pytest.raises(MCPError, match="does not provide"):
            MCPToolset(client, allow=["ghost"])

    async def test_prefix_overrides_the_namespace(self, client):
        assert MCPToolset(client, prefix="files", allow=["echo"]).names == ["files__echo"]

    async def test_a_namespace_collision_is_refused(self, client):
        """Two servers sharing a namespace would let one shadow the other."""
        first, second = MCPToolset(client, allow=["echo"]), MCPToolset(client, allow=["echo"])
        with pytest.raises(ValueError, match="prefix="):
            Toolset([first, second])

    async def test_a_server_cannot_shadow_a_local_tool(self, client):
        """The namespace is the whole defence: a third-party server that
        could take `echo` would be a remote code-execution hole."""
        local = _local_tool("echo")
        tools = Toolset([local, MCPToolset(client)])
        assert tools.get("echo") is local and "echo__echo" in tools

    async def test_two_toolsets_over_one_client(self, client):
        both = Toolset([MCPToolset(client, allow=["echo"]), MCPToolset(client, prefix="again")])
        assert {"echo__echo", "again__echo", "again__add"} <= set(both.names)

    async def test_a_toolset_needs_a_connected_client(self, make_server):
        with pytest.raises(MCPError, match="not connected"):
            MCPToolset(MCPClient(make_server()))

    async def test_long_descriptions_are_truncated(self, client):
        for t in MCPToolset(client):
            assert len(t.spec.description) <= MAX_DESCRIPTION_CHARS + 2


class TestAllOrNothing:
    """A toolset is built whole or not at all, and a failed build closes
    the connection: no dead proxy is ever offered, no child process leaks."""

    async def test_a_clash_with_a_local_tool_builds_nothing(self, client):
        squatter = _local_tool("echo__explode")
        with pytest.raises(ValueError, match="two tools named 'echo__explode'"):
            Toolset([squatter, MCPToolset(client)])

    async def test_a_failed_connect_closes_the_connection(self, monkeypatch):
        closed = []
        original = MCPClient.close

        async def spy(self):
            closed.append(self.server.name)
            await original(self)

        monkeypatch.setattr(MCPClient, "close", spy)
        with pytest.raises(MCPError, match="does not provide"):
            await MCPToolset.connect(_stdio(), allow=["ghost"])
        assert closed == ["echo"], "a bad allow= must not leak a child process"

    async def test_two_tools_sanitised_to_one_name_build_nothing(self):
        clash = MCPServer(name="clash", command=sys.executable, args=[str(CLASH_SERVER)])
        with pytest.raises(MCPError, match="clash__read_file"):
            await MCPToolset.connect(clash)

    async def test_a_closed_toolsets_tools_fail_loudly(self, make_server):
        tools = await MCPToolset.connect(make_server())
        await tools.close()
        (message,) = await dispatch(
            [{"id": "1", "name": "echo__echo", "args": {"text": "x"}}], tools
        )
        assert message["status"] == "error" and "not connected" in message["content"]

    async def test_closing_leaves_local_tools_alone(self, make_server):
        local = _local_tool("local_tool")
        mcp_tools = await MCPToolset.connect(make_server())
        tools = Toolset([local, mcp_tools])
        await mcp_tools.close()
        (message,) = await dispatch([{"id": "1", "name": "local_tool", "args": {}}], tools)
        assert message["content"] == "local"


class TestPermissionDefaults:
    @pytest.mark.parametrize("name", ["echo__echo", "echo__add", "echo__braces"])
    async def test_a_read_only_hint_is_honoured(self, echo_tools, name):
        spec = echo_tools.get(name).spec
        assert (spec.readonly, spec.destructive, spec.sequential) == (True, False, False)

    async def test_an_unannotated_tool_is_gated(self, echo_tools):
        """Absent hints mean *unknown*, and unknown third-party code asks a
        human; it is not idempotent either (a crash answers it "unknown")."""
        spec = echo_tools.get("echo__unannotated").spec
        assert (spec.destructive, spec.idempotent) == (True, False)

    async def test_a_destructive_hint_is_gated(self, echo_tools):
        assert echo_tools.get("echo__explode").spec.destructive is True


class TestInsideAnAgent:
    async def test_a_failing_mcp_tool_becomes_a_tool_message(self, echo_tools):
        allow_all = ToolPolicy(default="allow", destructive="allow")
        (message,) = await dispatch(
            [{"id": "1", "name": "echo__explode", "args": {"reason": "x"}}],
            echo_tools,
            policy=allow_all,
        )
        assert message["status"] == "error" and message["tool_call_id"] == "1"
        assert "deliberate failure" in message["content"]

    async def test_a_destructive_mcp_tool_is_gated(self, echo_tools):
        """With no one to ask it is refused; in a run with a store it parks."""
        (message,) = await dispatch(
            [{"id": "1", "name": "echo__explode", "args": {"reason": "x"}}], echo_tools
        )
        assert message["content"] == NO_APPROVER.format(name="echo__explode")

    async def test_an_agent_runs_mcp_tools_and_parks_the_gated_one(self, hub, echo_tools):
        calls = [
            {"id": "c1", "name": "echo__echo", "args": {"text": "hi"}},
            {"id": "c2", "name": "echo__unannotated", "args": {"x": "y"}},
        ]
        hub(m=ScriptedLLM(completion("", tool_calls=calls, finish_reason="tool_calls")))
        agent = Agent(name="ops", model=Model("m"), tools=[echo_tools])
        store = InMemoryStateStore()
        res = await Runner.run(agent, "go", store=store)
        (asked,) = res.interruptions
        assert (res.status, asked.tool) == ("interrupted", "echo__unannotated")
        saved = await store.load(res.run_id)
        assert saved.pending.results["c1"]["content"] == "echo: hi"


class TestValues:
    """``call_value``: a tool's value, for code — not its text, for a model."""

    @pytest.fixture
    async def values(self):
        c = await MCPClient(
            MCPServer(name="values", command=sys.executable, args=[str(VALUES_SERVER)])
        ).connect()
        yield c
        await c.close()

    @pytest.mark.parametrize("n", [0, 1, 2])
    async def test_a_list_stays_a_list_whatever_its_length(self, values, n):
        got = await values.call_value("people", {"n": n})
        assert isinstance(got, list) and len(got) == n

    async def test_the_text_alone_could_not_tell(self, values):
        one = await values.call("people", {"n": 1})
        record = await values.call("person", {"name": "Linh"})
        assert one == record

    async def test_a_record_and_a_scalar_come_back_as_values(self, values):
        assert await values.call_value("person", {"name": "Bao"}) == {"name": "Bao", "role": "IT"}
        assert await values.call_value("count", {}) == 2

    async def test_plain_text_stays_text(self, values):
        assert await values.call_value("note", {}) == "no structure here"
