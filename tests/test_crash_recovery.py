"""Crash recovery: SIGKILL a real process between "in flight persisted" and
"tool finished", then resume in another process.

The turn calls three tools at once. When the process dies, ``note`` has
finished (its result is journaled), ``lookup`` (idempotent) and ``charge``
(not idempotent) are running. The resume must:

- keep ``note``'s journaled result and not run it again,
- re-run ``lookup``,
- not run ``charge``, and answer it "outcome unknown",
- commit the turn whole to the session, then carry on to an answer.

Runs against SQLite always, and against Redis when ``REDIS_URL`` is set.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from operonx_agents import Runner
from operonx_agents.run.runner import OUTCOME_UNKNOWN
from tests import crash_agent
from tests.agents import roles
from tests.fakes import ScriptedLLM, completion

ROOT = Path(__file__).resolve().parents[1]

KINDS = [
    "sqlite",
    pytest.param(
        "redis",
        marks=pytest.mark.skipif(
            not os.environ.get("REDIS_URL"), reason="REDIS_URL not set (a throwaway Redis)"
        ),
    ),
]


def ran(directory: Path) -> list:
    log = directory / "ran.log"
    return log.read_text().split() if log.exists() else []


@pytest.mark.parametrize("kind", KINDS)
async def test_sigkill_mid_turn_then_resume(tmp_path, kind, hub):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), *sys.path])}
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.crash_agent", str(tmp_path), kind],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    session, store = crash_agent.backends(tmp_path, kind)
    try:
        deadline = time.monotonic() + 30
        while True:
            assert proc.poll() is None, proc.stdout.read().decode()
            state = await store.load(crash_agent.RUN_ID)
            started = ran(tmp_path)
            if (
                state is not None
                and state.pending is not None
                and sorted(state.pending.inflight) == ["c_charge", "c_lookup"]
                and {"lookup", "charge", "note"} <= set(started)
            ):
                break
            assert time.monotonic() < deadline, f"never in flight: {started}, {state}"
            await asyncio.sleep(0.02)
        proc.send_signal(signal.SIGKILL)
        proc.wait(10)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == -signal.SIGKILL

    crashed = await store.load(crash_agent.RUN_ID)
    assert crashed.status == "running" and crashed.turn == 0
    assert list(crashed.pending.results) == ["c_note"], "note's result was journaled"
    assert await session.get_items() == [], "the session never holds half a turn"
    assert sorted(ran(tmp_path)) == ["charge", "lookup", "note"]

    # A new process: the same agent, tools that now finish, the model's next answer.
    hub(m=ScriptedLLM(completion("Charged? Checking first.")))
    agent = crash_agent.build(tmp_path / "ran.log", block=False)
    res = await Runner.resume(agent, crash_agent.RUN_ID, store=store, session=session)

    assert sorted(ran(tmp_path)) == ["charge", "lookup", "lookup", "note"], (
        "lookup re-ran; charge and note did not"
    )
    assert res.status == "completed" and res.output == "Charged? Checking first."
    tools = {m["tool_call_id"]: m for m in res.messages if m["role"] == "tool"}
    assert tools["c_lookup"]["content"] == "A1: shipped"
    assert tools["c_charge"]["content"] == OUTCOME_UNKNOWN.format(name="charge")
    assert tools["c_charge"]["status"] == "error"
    assert tools["c_note"]["content"] == "noted"
    assert roles(await session.get_items()) == [
        "user",
        "assistant",
        "tool",
        "tool",
        "tool",
        "assistant",
    ]
    assert (await store.load(crash_agent.RUN_ID)).status == "completed"
