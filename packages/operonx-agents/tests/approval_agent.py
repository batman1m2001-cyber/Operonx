"""The agent the approval-restart test runs in a process of its own.

``python -m tests.approval_agent <dir> <store>`` asks to refund 900 (a call
whose tool asks for approval over 500), so the run ends ``interrupted``;
the process writes the result to ``<dir>/result.json`` and exits. Every refund that
actually runs appends a line to ``<dir>/refunds.log``, so the test can see
in which process money moved.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from operonx_agents import Agent, Model, RedisStateStore, Runner, SQLiteStateStore, tool

RUN_ID = "approval-run"


def build(log: Path) -> Agent:
    @tool(idempotent=False, approval=lambda ctx, args: args["amount"] > 500)
    async def refund(order_id: str, amount: int, api_key: str = "") -> str:
        """Refund an order (over 500 needs a human)."""
        with open(log, "a") as f:
            f.write(f"{os.getpid()} {order_id} {amount}\n")
        return f"refunded {amount} on {order_id}"

    return Agent(name="cashier", model=Model("m"), tools=[refund])


def store(directory: Path, kind: str):
    if kind == "sqlite":
        return SQLiteStateStore(directory / "runs.db")
    return RedisStateStore(url=os.environ["REDIS_URL"], prefix=f"approval-test:{directory.name}:")


async def main(directory: Path, kind: str) -> None:
    from operonx.core.registry.resource_hub import ResourceHub

    from tests.fakes import FakeHub, ScriptedLLM, completion

    call = {
        "id": "c_refund",
        "name": "refund",
        "args": {"order_id": "A1", "amount": 900, "api_key": "sk-abcdefghijklmnop12345"},
    }
    ResourceHub.set_instance(
        FakeHub(m=ScriptedLLM(completion("", tool_calls=[call], finish_reason="tool_calls")))
    )
    res = await Runner.run(
        build(directory / "refunds.log"), "refund A1", store=store(directory, kind), run_id=RUN_ID
    )
    out = {"pid": os.getpid(), "status": res.status}
    out["interruptions"] = [i.to_json() for i in res.interruptions]
    (directory / "result.json").write_text(json.dumps(out))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
