"""The MCP client, against a real MCP server over real stdio.

Deliberately not mocked. A mock would verify the code against the contract
we assumed, and this codebase has already paid for that four times —
`glob` returning zero, `stream(mode="updates")` not being live, `exclude=`
never reaching the trace, `Interrupt()` cancelling the run. The fixture in
`mcp_fixtures/echo_server.py` speaks the actual protocol.
"""

from __future__ import annotations

import asyncio
import gc
import os
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


def _mcp_version() -> tuple[int, ...]:
    """The installed MCP SDK version, for behaviour that changed between them."""
    import importlib.metadata as metadata

    try:
        raw = metadata.version("mcp")
    except metadata.PackageNotFoundError:  # pragma: no cover
        return (0,)
    parts = []
    for chunk in raw.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _server(**kw) -> MCPServer:
    kw.setdefault("name", "echo")
    return MCPServer(command=sys.executable, args=[str(SERVER)], **kw)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _until(predicate, timeout: float = 15.0) -> None:
    """Poll for a condition. The condition is what the test asserts on, not
    how long it slept — a slow machine makes this wait longer, never flake."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        await asyncio.sleep(0.02)


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


class TestTaskOwnership:
    """The transport and session enter anyio cancel scopes, which must be
    exited by the task that entered them. A client is routinely opened in
    one task and closed in another — a FastAPI lifespan connects, a
    shutdown handler closes — so the client cannot let the caller's task
    be the one that enters them."""

    @pytest.mark.asyncio
    async def test_closing_from_another_task_leaves_the_connecting_task_alone(self):
        shutdown = asyncio.Event()
        connected = asyncio.Event()
        box: dict = {}

        async def lifespan() -> str:
            box["client"] = await MCPClient(_server()).connect()
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

    @pytest.mark.asyncio
    async def test_a_client_connected_in_a_finished_task_closes_cleanly(self):
        """The task that connected is gone by the time anyone closes. The
        scopes must still be exited properly — before, close() raised
        `Attempted to exit cancel scope in a different task` and hid it."""
        box: dict = {}

        async def opener() -> None:
            box["client"] = await MCPClient(_server()).connect()
            box["pid"] = int(await box["client"].call("pid", {}))

        await asyncio.create_task(opener())
        await box["client"].close()
        assert not _alive(box["pid"])

    @pytest.mark.asyncio
    async def test_an_abandoned_client_collected_by_gc_cancels_nothing(self):
        """A client dropped without close() used to be finalised on a GC
        task that exited its task group from foreign context — which
        cancelled the task that had connected it, still running."""
        shutdown = asyncio.Event()
        dropped = asyncio.Event()
        box: dict = {}

        async def lifespan() -> str:
            client = await MCPClient(_server()).connect()
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

    @pytest.mark.asyncio
    async def test_a_teardown_failure_is_raised_not_swallowed(self, monkeypatch):
        from mcp import ClientSession

        original = ClientSession.__aexit__

        async def broken_exit(self, *exc):
            await original(self, *exc)
            raise RuntimeError("teardown broke")

        monkeypatch.setattr(ClientSession, "__aexit__", broken_exit)
        c = await MCPClient(_server()).connect()
        with pytest.raises(MCPError, match="teardown broke"):
            await c.close()
        await c.close()  # and it is still idempotent afterwards

    @pytest.mark.asyncio
    async def test_a_teardown_failure_does_not_mask_the_error_in_flight(self, monkeypatch):
        """`async with` exiting on an exception reports that exception; a
        teardown failure on the way out is logged, not substituted."""
        from mcp import ClientSession

        original = ClientSession.__aexit__

        async def broken_exit(self, *exc):
            await original(self, *exc)
            raise RuntimeError("teardown broke")

        monkeypatch.setattr(ClientSession, "__aexit__", broken_exit)
        with pytest.raises(ValueError, match="the real error"):
            async with MCPClient(_server()):
                raise ValueError("the real error")

    @pytest.mark.asyncio
    async def test_connecting_twice_is_refused(self):
        """A second connect() used to spawn a second server and orphan the
        first — close() only knew about the latest."""
        c = await MCPClient(_server()).connect()
        try:
            with pytest.raises(MCPError, match="already connected"):
                await c.connect()
        finally:
            await c.close()


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
        """An *anticipated* failure — the server raised ToolError, so its
        text is meant for the client to read."""
        with pytest.raises(MCPError, match="boom"):
            await client.call("explode", {"reason": "boom"})

    @pytest.mark.asyncio
    async def test_an_unanticipated_crash_still_raises(self, client):
        """Invariant across SDK versions: a crash must raise, never be
        formatted as an answer."""
        with pytest.raises(MCPError):
            await client.call("crash", {"reason": "secret-internal-detail"})

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        _mcp_version() < (2, 1),
        reason="masking unanticipated crashes landed in mcp 2.1",
    )
    async def test_an_unanticipated_crash_does_not_leak_its_message(self, client):
        """mcp 2.1 masks a crash as `Error executing tool <name>` on purpose,
        so internals never reach the model. Before 2.1 the text leaked, which
        is what this fixture used to rely on."""
        with pytest.raises(MCPError) as caught:
            await client.call("crash", {"reason": "secret-internal-detail"})
        assert "secret-internal-detail" not in str(caught.value)

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
