"""The agent the crash-recovery test kills, run as its own process.

``python -m tests.crash_agent <dir> <store>`` runs one turn that calls three
tools at once, and never lets two of them finish:

- ``lookup`` (idempotent) and ``charge`` (not idempotent) block forever,
- ``note`` (idempotent) finishes at once, so its result is journaled.

Each tool appends ``<name>`` to ``<dir>/ran.log`` (flushed, fsynced) when it
starts, so the test can see what ran in which process. The test SIGKILLs
the process once both blocked tools are running and the journal is in the
store, then resumes the run in its own process with ``block=False``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from operonx_agents import (
    Agent,
    Model,
    RedisSession,
    RedisStateStore,
    SQLiteSession,
    SQLiteStateStore,
    tool,
)

RUN_ID = "crash-run"


def build(log: Path, *, block: bool) -> Agent:
    def started(name: str) -> None:
        with open(log, "a") as f:
            f.write(name + "\n")
            f.flush()
            os.fsync(f.fileno())

    @tool(sequential=False)
    async def lookup(order_id: str) -> str:
        """Look an order up (safe to repeat)."""
        started("lookup")
        if block:
            await asyncio.sleep(3600)
        return f"{order_id}: shipped"

    @tool(idempotent=False, sequential=False)
    async def charge(amount: int) -> str:
        """Charge the card (not safe to repeat)."""
        started("charge")
        if block:
            await asyncio.sleep(3600)
        return f"charged {amount}"

    @tool(sequential=False)
    async def note(text: str) -> str:
        """Write a note (safe to repeat)."""
        started("note")
        return "noted"

    return Agent(name="cashier", model=Model("m"), tools=[lookup, charge, note])


def backends(directory: Path, kind: str):
    if kind == "sqlite":
        db = directory / "runs.db"
        return SQLiteSession("chat", db), SQLiteStateStore(db)
    url = os.environ["REDIS_URL"]
    prefix = f"crash-test:{directory.name}:"
    return (
        RedisSession("chat", url=url, prefix=prefix + "session:"),
        RedisStateStore(url=url, prefix=prefix + "run:"),
    )


async def main(directory: Path, kind: str) -> None:
    from operonx.core.registry.resource_hub import ResourceHub

    from tests.fakes import FakeHub, ScriptedLLM, completion

    calls = [
        {"id": "c_lookup", "name": "lookup", "args": {"order_id": "A1"}},
        {"id": "c_charge", "name": "charge", "args": {"amount": 5}},
        {"id": "c_note", "name": "note", "args": {"text": "paid"}},
    ]
    ResourceHub.set_instance(
        FakeHub(m=ScriptedLLM(completion("", tool_calls=calls, finish_reason="tool_calls")))
    )
    session, store = backends(directory, kind)
    agent = build(directory / "ran.log", block=True)
    from operonx_agents import Runner

    await Runner.run(agent, "charge 5 for A1", session=session, store=store, run_id=RUN_ID)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
