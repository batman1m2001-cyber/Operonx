"""The MCP client, against a real MCP server over real stdio.

Deliberately not mocked. A mock would verify the code against the contract
we assumed, and this codebase has already paid for that four times —
`glob` returning zero, `stream(mode="updates")` not being live, `exclude=`
never reaching the trace, `Interrupt()` cancelling the run. The fixture in
`mcp_fixtures/echo_server.py` speaks the actual protocol.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from operonx.agents.tool import TOOL_REGISTRY, clear_registry, get_tool_definitions

pytestmark = pytest.mark.unit

mcp_mod = pytest.importorskip("mcp", reason="needs operonx[mcp]")

from operonx.agents.mcp import (  # noqa: E402
    MCPClient,
    MCPError,
    MCPServer,
    connect_mcp,
    register_mcp_tools,
)

SERVER = Path(__file__).parent / "mcp_fixtures" / "echo_server.py"


def _server(**kw) -> MCPServer:
    kw.setdefault("name", "echo")
    return MCPServer(command=sys.executable, args=[str(SERVER)], **kw)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
async def client():
    c = await MCPClient(_server()).connect()
    yield c
    await c.close()


class TestConfig:
    def test_a_nameless_server_is_refused(self):
        with pytest.raises(MCPError, match="needs a name"):
            MCPServer(name="  ", command="x")

    def test_a_commandless_server_is_refused(self):
        with pytest.raises(MCPError, match="needs a command"):
            MCPServer(name="fs", command="")

    def test_the_name_is_sanitised_because_it_becomes_a_tool_prefix(self):
        assert MCPServer(name="my server!", command="x").name == "my_server_"


class TestConnection:
    @pytest.mark.asyncio
    async def test_it_discovers_the_servers_tools(self, client):
        names = {t.name for t in client.tools}
        assert {"echo", "add", "explode"} <= names

    @pytest.mark.asyncio
    async def test_a_bad_command_fails_with_the_server_named(self):
        bad = MCPServer(name="nope", command="definitely-not-a-real-binary-xyz")
        with pytest.raises(MCPError, match="nope"):
            await MCPClient(bad).connect()

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        c = await MCPClient(_server()).connect()
        await c.close()
        await c.close()

    @pytest.mark.asyncio
    async def test_calling_after_close_says_so(self):
        c = await MCPClient(_server()).connect()
        await c.close()
        with pytest.raises(MCPError, match="not connected"):
            await c.call("echo", {"text": "hi"})

    @pytest.mark.asyncio
    async def test_it_works_as_a_context_manager(self):
        async with MCPClient(_server()) as c:
            assert c.tools


class TestCalling:
    @pytest.mark.asyncio
    async def test_a_call_returns_text(self, client):
        assert "echo: hello" in await client.call("echo", {"text": "hello"})

    @pytest.mark.asyncio
    async def test_typed_arguments_survive_the_round_trip(self, client):
        assert "7" in await client.call("add", {"a": 3, "b": 4})

    @pytest.mark.asyncio
    async def test_an_in_band_error_raises_rather_than_reading_as_an_answer(self, client):
        """MCP reports failures as `isError` on an otherwise ordinary
        result. Returning its content would hand the model an error
        message formatted as a result — the one thing a tool must not do."""
        with pytest.raises(MCPError, match="reported an error"):
            await client.call("explode", {"reason": "boom"})

    @pytest.mark.asyncio
    async def test_the_error_carries_the_servers_message(self, client):
        with pytest.raises(MCPError, match="boom"):
            await client.call("explode", {"reason": "boom"})

    @pytest.mark.asyncio
    async def test_a_slow_call_times_out_and_names_the_tool(self):
        async with MCPClient(_server(timeout=0.5)) as c:
            with pytest.raises(MCPError, match="echo__slow"):
                await c.call("slow", {"seconds": 5})

    @pytest.mark.asyncio
    async def test_an_unknown_tool_raises(self, client):
        with pytest.raises(MCPError):
            await client.call("no_such_tool", {})

    @pytest.mark.asyncio
    async def test_json_content_survives_intact(self, client):
        """The braces that used to poison the next model call — the tool
        result travels as data now, so it must arrive unmangled."""
        assert '{"city": "Hanoi", "temp": 30}' in await client.call("braces", {})


class TestRegistration:
    @pytest.mark.asyncio
    async def test_tools_are_namespaced_by_server(self, client):
        names = await register_mcp_tools(client)
        assert "echo__echo" in names
        assert "echo__add" in names

    @pytest.mark.asyncio
    async def test_they_land_in_the_operonx_registry(self, client):
        await register_mcp_tools(client)
        assert "echo__echo" in TOOL_REGISTRY

    @pytest.mark.asyncio
    async def test_the_definitions_the_model_sees_are_well_formed(self, client):
        await register_mcp_tools(client)
        defn = next(d for d in get_tool_definitions() if d["function"]["name"] == "echo__add")
        assert defn["function"]["description"]
        assert defn["function"]["parameters"]["type"] == "object"
        assert "a" in defn["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_a_registered_tool_actually_calls_the_server(self, client):
        await register_mcp_tools(client)
        factory = TOOL_REGISTRY["echo__echo"]
        out = await factory.__wrapped__(text="through the registry")
        assert "echo: through the registry" in out["result"]

    @pytest.mark.asyncio
    async def test_allow_restricts_the_set(self, client):
        names = await register_mcp_tools(client, allow=["echo"])
        assert names == ["echo__echo"]
        assert "echo__add" not in TOOL_REGISTRY

    @pytest.mark.asyncio
    async def test_allowing_a_tool_the_server_lacks_raises(self, client):
        """Silently dropping it would hand the agent a narrower toolset
        than its author wrote, with nothing to explain why."""
        with pytest.raises(MCPError, match="does not provide"):
            await register_mcp_tools(client, allow=["ghost"])

    @pytest.mark.asyncio
    async def test_prefix_overrides_the_namespace(self, client):
        names = await register_mcp_tools(client, prefix="files", allow=["echo"])
        assert names == ["files__echo"]

    @pytest.mark.asyncio
    async def test_a_namespace_collision_is_refused(self, client):
        await register_mcp_tools(client, allow=["echo"])
        with pytest.raises(MCPError, match="already registered"):
            await register_mcp_tools(client, allow=["echo"])

    @pytest.mark.asyncio
    async def test_a_server_cannot_shadow_a_local_tool(self, client):
        """The namespace is the whole defence: a third-party server that
        could register `bash` would be a remote code-execution hole."""
        from operonx.agents.tool import tool

        @tool(name="echo", description="local", schema={"type": "object", "properties": {}})
        async def local_echo() -> dict:
            return {"local": True}

        await register_mcp_tools(client)
        assert TOOL_REGISTRY["echo"] is local_echo
        assert "echo__echo" in TOOL_REGISTRY


class TestPermissionDefaults:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["echo__echo", "echo__add", "echo__braces"])
    async def test_a_read_only_hint_is_honoured(self, client, name):
        await register_mcp_tools(client)
        meta = TOOL_REGISTRY[name]._tool_meta
        assert meta["readonly"] is True
        assert meta["destructive"] is False

    @pytest.mark.asyncio
    async def test_an_unannotated_tool_is_gated(self, client):
        """Absent hints mean *unknown*, and unknown third-party code asks a
        human. The alternative is that a server omitting its annotations
        gets to run unattended."""
        await register_mcp_tools(client)
        meta = TOOL_REGISTRY["echo__unannotated"]._tool_meta
        assert meta["destructive"] is True

    @pytest.mark.asyncio
    async def test_a_destructive_hint_is_gated(self, client):
        await register_mcp_tools(client)
        assert TOOL_REGISTRY["echo__explode"]._tool_meta["destructive"] is True


class TestConnectMcp:
    @pytest.mark.asyncio
    async def test_it_connects_and_registers(self):
        client, names = await connect_mcp(_server())
        try:
            assert "echo__echo" in names
            assert "echo__echo" in TOOL_REGISTRY
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_a_registration_failure_closes_the_connection(self):
        """Otherwise a bad `allow=` leaks a child process per attempt."""
        with pytest.raises(MCPError):
            await connect_mcp(_server(), allow=["ghost"])


class TestInsideAnAgent:
    @pytest.mark.asyncio
    async def test_an_mcp_tool_dispatches_like_a_local_one(self):
        """The point of the whole module: once registered, nothing
        downstream should be able to tell the difference."""
        from operonx.agents.graphs.dispatch import build_dispatch
        from operonx.core import Operon

        client, _ = await connect_mcp(_server())
        try:
            built = build_dispatch()(call=None)
            result = await asyncio.wait_for(
                Operon(built).run(
                    inputs={
                        "call": {
                            "id": "1",
                            "name": "echo__echo",
                            "args": {"text": "via dispatch"},
                        }
                    }
                ),
                timeout=30,
            )
            message = result["tool_message"]
            assert message["status"] == "success"
            assert "echo: via dispatch" in message["content"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_a_failing_mcp_tool_becomes_a_tool_message(self):
        """A remote failure must reach the model as something it can
        recover from, not end the run."""
        from operonx.agents.graphs.dispatch import build_dispatch
        from operonx.agents.policy import ToolPolicy
        from operonx.core import Operon

        client, _ = await connect_mcp(_server())
        try:
            # `explode` is destructiveHint=True, so the default policy would
            # gate it and this would time out waiting for a human — which is
            # how the gating test below came to exist.
            allow_all = ToolPolicy(default="allow", destructive="allow")
            built = build_dispatch(policy=allow_all)(call=None)
            result = await asyncio.wait_for(
                Operon(built).run(
                    inputs={"call": {"id": "1", "name": "echo__explode", "args": {"reason": "x"}}}
                ),
                timeout=30,
            )
            message = result["tool_message"]
            assert message["status"] == "error"
            assert message["tool_call_id"] == "1"
            assert "boom" in message["content"] or "failure" in message["content"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_a_destructive_mcp_tool_is_gated(self):
        """End-to-end proof the annotation reaches the permission gate.

        `explode` declares `destructiveHint=True`, so dispatch must ask
        before running it. Found by a test that forgot to approve and hung
        for its full approval timeout.
        """
        from operonx.agents.graphs.dispatch import build_dispatch
        from operonx.checkpoint import bind_interrupt_bus
        from operonx.core import Operon

        client, _ = await connect_mcp(_server())
        try:
            built = build_dispatch(approval_timeout=10)(call=None)
            handle = Operon(built).start(
                inputs={"call": {"id": "1", "name": "echo__explode", "args": {"reason": "x"}}}
            )
            asked: list = []

            def sink(event):
                asked.append(getattr(event, "payload", {}))
                handle.state.resume_interrupt(event.interrupt_id, {"approved": False})

            bind_interrupt_bus(handle.state, sink=sink)
            await asyncio.wait_for(handle.result(), timeout=30)

            assert asked, "a destructive MCP tool must ask before running"
            assert asked[0]["tool"] == "echo__explode"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_a_read_only_mcp_tool_is_not_gated(self):
        """The other half — a server that annotates read-only must not make
        its user approve every call."""
        from operonx.agents.graphs.dispatch import build_dispatch
        from operonx.core import Operon

        client, _ = await connect_mcp(_server())
        try:
            built = build_dispatch(approval_timeout=5)(call=None)
            result = await asyncio.wait_for(
                Operon(built).run(
                    inputs={"call": {"id": "1", "name": "echo__echo", "args": {"text": "hi"}}}
                ),
                timeout=20,
            )
            assert result["tool_message"]["status"] == "success"
        finally:
            await client.close()
