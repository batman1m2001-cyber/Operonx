"""A5 against a real model (``-m live``; tiny spend): ``agent_service`` on a
real server, HTTP and websocket, each with an approval round trip.

A uvicorn server on a free local port serves one agent on qwen3.7-plus
twice: ``POST /cashier`` (JSON and server-sent events, ``/cashier/resume``)
and ``/cashier/ws``. Over HTTP the client reads the run's events as they
happen until the refund parks, then approves it on the resume route; over
the websocket it does the same on one connection. Each refund runs once,
after its approval. With ``A5_TRACE_DIR`` set the runs are also recorded
there (the studio screenshots read them).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time

import pytest

from operonx_agents import (
    Agent,
    Model,
    ModelSettings,
    SQLiteStateStore,
    UsageLimits,
    agent_service,
    tool,
)

REFUNDS: list = []


@tool(readonly=True)
async def order_status(order_id: str) -> str:
    """The status of an order and how much was paid.

    Args:
        order_id: The order code, e.g. A1B2C3D4.
    """
    return f"order {order_id}: delivered, paid 900"


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


def cashier() -> Agent:
    return Agent(
        name="cashier",
        model=Model("qwen3.7-plus", deadline=90, settings=ModelSettings(max_tokens=300)),
        instructions=(
            "You handle refunds. Check the order with order_status first, then refund "
            "with the refund tool. Be brief."
        ),
        tools=[order_status, refund],
        limits=UsageLimits(turns=5, total_tokens=16_000),
    )


@pytest.fixture
def server(live_hub, tmp_path):
    import uvicorn
    from operonx.app import http, websocket
    from operonx.app.serve.app import build_app

    store = SQLiteStateStore(tmp_path / "runs.db")
    trace = []
    if os.environ.get("A5_TRACE_DIR"):
        from operonx.telemetry.runs.files import FilesRunStore

        trace = [FilesRunStore(os.environ["A5_TRACE_DIR"])]
    agent = cashier()
    specs = (
        agent_service(agent, http("POST", "/cashier"), store=store, trace=trace),
        agent_service(
            agent,
            websocket("/cashier/ws"),
            store=store,
            max_inflight=16,
            name="cashier_ws",
            trace=trace,
        ),
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(
        uvicorn.Config(build_app(specs), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not srv.started:
        assert time.monotonic() < deadline, "the server did not start"
        time.sleep(0.05)
    yield f"127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=15)


async def test_http_events_then_an_approval_on_the_resume_route(server, capsys):
    import httpx

    REFUNDS.clear()
    started = time.perf_counter()
    async with httpx.AsyncClient(base_url=f"http://{server}", timeout=120) as client:
        async with client.stream(
            "POST",
            "/cashier",
            json={"input": "Please refund 900 on order A1B2C3D4."},
            headers={"accept": "text/event-stream"},
        ) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            trace_id = response.headers["x-operonx-trace-id"]
            events = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))
        parked_in = time.perf_counter() - started
        kinds = [e["type"] for e in events]
        assert kinds[0] == "RunStarted" and kinds[-1] == "RunFinished", kinds
        parked = events[-1]["result"]
        assert parked["status"] == "interrupted", parked
        asked = [e for e in events if e["type"] == "ApprovalRequired"]
        assert [a["tool"] for a in asked] == ["refund"] and REFUNDS == []
        assert asked[0]["args"] == {"order_id": "A1B2C3D4", "amount": 900}

        answer = {"run_id": parked["run_id"], "approvals": {asked[0]["id"]: "approve"}}
        done = (await client.post("/cashier/resume", json=answer)).json()
    assert done["status"] == "completed", done
    assert REFUNDS == [("A1B2C3D4", 900)] and "R-0001" in done["output"]
    with capsys.disabled():
        print(
            f"\n[live A5 http] trace {trace_id}: {len(events)} events {kinds} "
            f"parked in {parked_in:.1f}s; resumed → {done['status']}: {done['output']!r} "
            f"({done['turns']} turns, {done['usage']})"
        )


async def test_websocket_events_then_an_approval_on_the_same_connection(server, capsys):
    import websockets

    REFUNDS.clear()
    async with websockets.connect(f"ws://{server}/cashier/ws", open_timeout=15) as ws:
        await ws.send(json.dumps({"input": "Refund 700 on order Z9Y8X7W6, please."}))
        first = await _until_finished(ws)
        parked = first[-1]["result"]
        assert parked["status"] == "interrupted", parked
        (asked,) = [e for e in first if e["type"] == "ApprovalRequired"]
        assert asked["args"]["amount"] == 700 and REFUNDS == []
        answer = {"run_id": parked["run_id"], "approvals": {asked["id"]: "approve"}}
        await ws.send(json.dumps(answer))
        second = await _until_finished(ws)
    done = second[-1]["result"]
    assert second[0] == {
        "type": "RunStarted",
        "run_id": parked["run_id"],
        "agent": "cashier",
        "resumed": True,
    }
    assert done["status"] == "completed" and REFUNDS == [("Z9Y8X7W6", 700)], done
    with capsys.disabled():
        print(
            f"\n[live A5 websocket] {[e['type'] for e in first]} then "
            f"{[e['type'] for e in second]} → {done['output']!r}"
        )


async def _until_finished(ws) -> list:
    got = []
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), 120))
        got.append(frame)
        if frame.get("type") == "RunFinished":
            return got
