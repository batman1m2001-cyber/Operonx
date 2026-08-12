"""Repros for MCP client hypotheses. Read-only w.r.t. the repo."""
import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/home/thanglq/Operon")

from operonx.agents.mcp import MCPClient, MCPServer  # noqa: E402

SERVER = Path("/home/thanglq/Operon/tests/internal/agents/mcp_fixtures/echo_server.py")


def _server(**kw):
    kw.setdefault("name", "echo")
    return MCPServer(command=sys.executable, args=[str(SERVER)], **kw)


def _child_pids():
    out = subprocess.run(
        ["pgrep", "-f", "echo_server.py"], capture_output=True, text=True
    ).stdout.split()
    return set(out)


async def h_a_close_from_different_task():
    print("\n=== A: connect in task A, close in task B ===")
    before = _child_pids()
    c = None

    async def opener():
        nonlocal c
        c = await MCPClient(_server()).connect()

    await asyncio.create_task(opener())
    spawned = _child_pids() - before
    print("spawned child pids:", spawned)

    async def closer():
        await c.close()

    await asyncio.create_task(closer())
    await asyncio.sleep(0.5)
    still = _child_pids() & spawned
    print("close() returned without raising:", True)
    print("child STILL ALIVE after close():", still)
    for p in still:
        subprocess.run(["kill", "-9", p])


async def h_b_call_from_different_task():
    print("\n=== B: connect in task A, call from task B (mimics _pump) ===")
    c = await MCPClient(_server()).connect()
    try:

        async def caller():
            return await c.call("echo", {"text": "from another task"})

        try:
            out = await asyncio.wait_for(asyncio.create_task(caller()), timeout=10)
            print("cross-task call OK:", out)
        except Exception as e:
            print("cross-task call FAILED:", type(e).__name__, e)
    finally:
        await c.close()


async def h_c_close_while_call_in_flight():
    print("\n=== C: close() while a call is in flight ===")
    before = _child_pids()
    c = await MCPClient(_server(timeout=30)).connect()
    spawned = _child_pids() - before
    t = asyncio.create_task(c.call("slow", {"seconds": 3}))
    await asyncio.sleep(0.5)
    try:
        await asyncio.wait_for(c.close(), timeout=10)
        print("close() returned")
    except Exception as e:
        print("close() raised:", type(e).__name__, e)
    try:
        print("in-flight call result:", await asyncio.wait_for(t, timeout=10))
    except Exception as e:
        print("in-flight call raised:", type(e).__name__, e)
    await asyncio.sleep(0.3)
    still = _child_pids() & spawned
    print("child still alive:", still)
    for p in still:
        subprocess.run(["kill", "-9", p])


async def h_d_timeout_then_reuse():
    print("\n=== D: a call times out, then the session is reused ===")
    c = await MCPClient(_server(timeout=0.5)).connect()
    try:
        try:
            await c.call("slow", {"seconds": 3})
        except Exception as e:
            print("timeout raised as expected:", type(e).__name__, str(e)[:80])
        # Is the session usable afterwards?
        for i in range(3):
            try:
                out = await asyncio.wait_for(c.call("echo", {"text": f"after-{i}"}), timeout=8)
                print(f"  reuse {i}: OK -> {out!r}")
            except Exception as e:
                print(f"  reuse {i}: FAILED {type(e).__name__}: {str(e)[:120]}")
    finally:
        await c.close()


async def h_e_structured_content():
    print("\n=== E: structured content / result shapes ===")
    c = await MCPClient(_server()).connect()
    try:
        raw = await c._session.call_tool("add", {"a": 3, "b": 4})
        print("type:", type(raw).__name__)
        print("content:", raw.content)
        print("structured_content:", getattr(raw, "structured_content", "<none>"))
        print("operonx sees:", await c.call("add", {"a": 3, "b": 4}))
    finally:
        await c.close()


async def h_f_two_connects_same_client():
    print("\n=== F: connect() twice on one client ===")
    before = _child_pids()
    c = MCPClient(_server())
    await c.connect()
    await c.connect()
    spawned = _child_pids() - before
    print("children spawned by two connect() calls:", len(spawned), spawned)
    await c.close()
    await asyncio.sleep(0.4)
    still = _child_pids() & spawned
    print("orphans after one close():", still)
    for p in still:
        subprocess.run(["kill", "-9", p])


async def main():
    for h in (
        h_b_call_from_different_task,
        h_e_structured_content,
        h_d_timeout_then_reuse,
        h_c_close_while_call_in_flight,
        h_f_two_connects_same_client,
        h_a_close_from_different_task,
    ):
        try:
            await asyncio.wait_for(h(), timeout=60)
        except Exception as e:
            print(f"!! {h.__name__} blew up: {type(e).__name__}: {e}")


import os

if __name__ == "__main__":
    which = sys.argv[1]
    asyncio.run(asyncio.wait_for(globals()[which](), timeout=45))
