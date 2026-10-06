"""A4 against a real model (``-m live``; tiny spend).

- One approval round trip on ``qwen3.7-plus``: the model asks to refund 900,
  the call parks (the tool asks for approval over 500), the run is saved to
  SQLite and ends ``interrupted``; ``Runner.resume`` with ``Approve()`` runs
  the refund once, and the model reports it.
- The model drives the official MCP reference server's tools over
  streamable HTTP (needs ``npx``).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time

import pytest

from operonx_agents import (
    Agent,
    Approve,
    Model,
    ModelSettings,
    Runner,
    SQLiteStateStore,
    UsageLimits,
    tool,
)

REFUNDS: list = []


@tool(idempotent=False, approval=lambda ctx, args: args["amount"] > 500)
async def refund(order_id: str, amount: int) -> str:
    """Refund an amount on an order. Refunds over 500 need a manager's approval,
    which the system asks for on its own: just call the tool.

    Args:
        order_id: The order code, e.g. A1B2C3D4.
        amount: The amount to refund, in VND thousands.
    """
    REFUNDS.append((order_id, amount))
    return f"refunded {amount} on {order_id}; reference R-{len(REFUNDS):04d}"


async def test_an_approval_round_trip_on_qwen(live_hub, tmp_path):
    REFUNDS.clear()
    agent = Agent(
        name="cashier",
        model=Model("qwen3.7-plus", deadline=90, settings=ModelSettings(max_tokens=300)),
        instructions="You process refunds with the refund tool. Be brief.",
        tools=[refund],
        limits=UsageLimits(turns=4, total_tokens=12_000),
    )
    store = SQLiteStateStore(tmp_path / "runs.db")
    started = time.perf_counter()
    first = await Runner.run(agent, "Refund 900 on order A1B2C3D4.", store=store)
    asked_in = time.perf_counter() - started
    assert first.status == "interrupted", (first.status, first.error, first.output)
    (asked,) = first.interruptions
    assert asked.tool == "refund" and asked.args == {"order_id": "A1B2C3D4", "amount": 900}
    assert REFUNDS == [] and (await store.load(first.run_id)).status == "interrupted"

    started = time.perf_counter()
    done = await Runner.resume(agent, first.run_id, store=store, approvals={asked.id: Approve()})
    resumed_in = time.perf_counter() - started
    print(
        f"\nqwen3.7-plus approval round trip: interrupted after {asked_in:.1f}s "
        f"(id {asked.id}, reason {asked.reason!r}); resumed {done.status} in {resumed_in:.1f}s, "
        f"{done.turns} turns, usage {done.usage.to_dict()}, refunds {REFUNDS}\n"
        f"answer: {done.output!r}"
    )
    assert done.status == "completed", done.error
    assert REFUNDS == [("A1B2C3D4", 900)], "approved once, ran once"
    assert "R-0001" in done.output or "900" in done.output
    store.close()


@pytest.mark.skipif(shutil.which("npx") is None, reason="needs node's npx")
async def test_qwen_drives_the_mcp_reference_server(live_hub):
    from operonx_agents.tools.mcp import MCPServer, MCPToolset
    from tests.test_mcp import free_port, wait_for_port
    from tests.test_mcp_reference import PACKAGE, _node_env

    port = free_port()
    proc = subprocess.Popen(
        ["npx", "-y", PACKAGE, "streamableHttp"],
        env={**os.environ, **_node_env(), "PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        wait_for_port(port, proc, timeout=180)
        server = MCPServer("everything", url=f"http://127.0.0.1:{port}/mcp")
        async with await MCPToolset.connect(server, allow=["get-sum"]) as tools:
            agent = Agent(
                name="calc",
                model=Model("qwen3.7-plus", deadline=90, settings=ModelSettings(max_tokens=200)),
                instructions="Use the tools for arithmetic. Answer with the number only.",
                tools=[tools],
                limits=UsageLimits(turns=4, total_tokens=8_000),
            )
            res = await Runner.run(agent, "What is 1234 + 4321?")
        calls = [m for m in res.messages if m["role"] == "tool"]
        print(
            f"\nqwen3.7-plus + MCP reference server (streamable HTTP): {res.status}, "
            f"{res.turns} turns, tool results {[m['content'] for m in calls]}, "
            f"answer {res.output!r}"
        )
        assert res.status == "completed", res.error
        assert calls and calls[0]["name"] == "everything__get-sum"
        assert "5555" in res.output
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(10)
