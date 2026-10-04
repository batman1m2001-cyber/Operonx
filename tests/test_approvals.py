"""Approvals as interruptions: a call that needs a human parks the run.

The gate (AGENTS_V2_PLAN §4, A4): **an approval survives a process
restart** — one process runs until a call needs approval and exits; this
process resumes the saved run with the answer, and the call runs here.
Then the cases around it: a denial, an expiry, deny is not ask, an
argument-dependent rule, partial answers, ids that are not waited on.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from operonx_agents import (
    Approve,
    Deny,
    InMemorySession,
    InMemoryStateStore,
    Runner,
    ToolPolicy,
    tool,
)
from operonx_agents.run.interruption import interruption_id
from operonx_agents.run.runner import EXPIRED
from operonx_agents.tools.dispatch import DENIED, NO_APPROVER
from tests import approval_agent
from tests.agents import RAN, asks, make, roles, says, tool_ids

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


@pytest.fixture(autouse=True)
def _ran():
    RAN.clear()
    yield


@tool(idempotent=False, approval=lambda ctx, args: args["amount"] > 500)
async def refund(order_id: str, amount: int) -> str:
    """Refund an order (over 500 needs a human)."""
    RAN.append(f"refund:{order_id}:{amount}")
    return f"refunded {amount}"


@tool(approval="always")
async def wipe(path: str) -> str:
    """Delete a path."""
    RAN.append(f"wipe:{path}")
    return "gone"


def refunds(directory: Path) -> list:
    log = directory / "refunds.log"
    return [line.split() for line in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.parametrize("kind", KINDS)
async def test_an_approval_survives_a_process_restart(tmp_path, kind, hub):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), *sys.path])}
    done = subprocess.run(
        [sys.executable, "-m", "tests.approval_agent", str(tmp_path), kind],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr.decode()
    first = json.loads((tmp_path / "result.json").read_text())
    assert first["pid"] != os.getpid() and first["status"] == "interrupted"
    (asked,) = first["interruptions"]
    run_id = approval_agent.RUN_ID
    assert asked["id"] == interruption_id(run_id, "cashier", "refund", 1, "c_refund")
    assert asked["path"] == ["cashier"] and asked["tool"] == "refund"
    assert asked["args"]["amount"] == 900
    assert "sk-abcdefghijklmnop12345" not in json.dumps(asked), "a human is shown redacted args"
    assert refunds(tmp_path) == [], "nothing ran before the approval"

    # This process: a new interpreter state, the same store, the model's next reply.
    from tests.fakes import ScriptedLLM, completion

    hub(m=ScriptedLLM(completion("Refunded 900 on A1.")))
    store = approval_agent.store(tmp_path, kind)
    saved = await store.load(run_id)
    assert saved.status == "interrupted" and saved.pending.inflight == []
    agent = approval_agent.build(tmp_path / "refunds.log")
    res = await Runner.resume(agent, run_id, store=store, approvals={asked["id"]: Approve()})

    assert res.status == "completed" and res.output == "Refunded 900 on A1."
    assert refunds(tmp_path) == [[str(os.getpid()), "A1", "900"]], "it ran once, here"
    (message,) = [m for m in res.messages if m["role"] == "tool"]
    assert message["content"] == "refunded 900 on A1"
    assert (await store.load(run_id)).status == "completed"
    if kind == "redis":
        await store.delete(run_id)


class TestDecisions:
    async def test_approve_runs_the_call_and_the_run_goes_on(self, hub):
        agent, llm = make(hub, asks(("wipe", {"path": "/tmp/x"})), says("wiped"), tools=[wipe])
        store, session = InMemoryStateStore(), InMemorySession()
        res = await Runner.run(agent, "wipe it", store=store, session=session)
        assert (res.status, res.output, RAN, llm.calls) == ("interrupted", None, [], 1)
        assert await session.get_items() == [], "a parked turn is not committed"
        (asked,) = res.interruptions
        assert asked.reason == "'wipe' always asks for approval"
        done = await Runner.resume(
            agent, res.run_id, store=store, session=session, approvals={asked.id: Approve()}
        )
        assert (done.status, done.output, RAN) == ("completed", "wiped", ["wipe:/tmp/x"])
        assert roles(await session.get_items()) == ["user", "assistant", "tool", "assistant"]

    async def test_deny_refuses_the_call_with_the_reason(self, hub):
        agent, llm = make(hub, asks(("wipe", {"path": "/"})), says("ok, not wiping"), tools=[wipe])
        store = InMemoryStateStore()
        res = await Runner.run(agent, "wipe /", store=store)
        (asked,) = res.interruptions
        done = await Runner.resume(
            agent, res.run_id, store=store, approvals={asked.id: Deny("root is off limits")}
        )
        assert (done.status, done.output, RAN) == ("completed", "ok, not wiping", [])
        (message,) = [m for m in done.messages if m["role"] == "tool"]
        assert message["status"] == "error"
        assert message["content"] == (
            DENIED.format(name="wipe") + " Their reason: root is off limits"
        )
        assert llm.requests[-1]["messages"][-1]["content"] == message["content"]

    async def test_an_expired_approval_is_refused_even_if_approved_later(self, hub):
        agent, _ = make(
            hub, asks(("wipe", {"path": "/x"})), says("noted"), tools=[wipe], approval_ttl=0.05
        )
        store = InMemoryStateStore()
        res = await Runner.run(agent, "wipe", store=store)
        (asked,) = res.interruptions
        assert asked.expires_at is not None and not asked.expired
        await asyncio.sleep(0.08)
        assert asked.expired
        done = await Runner.resume(agent, res.run_id, store=store, approvals={asked.id: Approve()})
        assert done.status == "completed" and RAN == []
        (message,) = [m for m in done.messages if m["role"] == "tool"]
        assert message["content"] == EXPIRED.format(name="wipe")

    async def test_an_expired_approval_with_no_answer_is_refused(self, hub):
        agent, _ = make(
            hub, asks(("wipe", {"path": "/x"})), says("noted"), tools=[wipe], approval_ttl=0.01
        )
        store = InMemoryStateStore()
        res = await Runner.run(agent, "wipe", store=store)
        await asyncio.sleep(0.03)
        done = await Runner.resume(agent, res.run_id, store=store)
        assert done.status == "completed" and RAN == []
        assert EXPIRED.format(name="wipe") in [m["content"] for m in done.messages]

    async def test_deny_is_not_ask_a_policy_refusal_never_reaches_a_human(self, hub):
        """`wipe` asks for approval, and the policy denies it: the model is
        refused at once and no human is asked to approve the forbidden."""
        agent, _ = make(
            hub,
            asks(("wipe", {"path": "/"})),
            says("cannot"),
            tools=[wipe],
            policy=ToolPolicy(default="allow", rules={"wipe": "deny"}),
        )
        res = await Runner.run(agent, "wipe", store=InMemoryStateStore())
        assert (res.status, res.interruptions, RAN) == ("completed", [], [])
        (message,) = [m for m in res.messages if m["role"] == "tool"]
        assert (
            "policy forbids" in message["content"]
            and "no human will be asked" in (message["content"])
        )

    async def test_a_policy_ask_parks_the_call(self, hub):
        @tool
        async def send(to: str) -> str:
            """Send an email."""
            RAN.append(f"send:{to}")
            return "sent"

        agent, _ = make(
            hub, asks(("send", {"to": "a@b"})), says("sent"), tools=[send],
            policy=ToolPolicy(default="ask"),
        )  # fmt: skip
        res = await Runner.run(agent, "email", store=InMemoryStateStore())
        (asked,) = res.interruptions
        assert asked.reason == "the policy asks before running 'send'"

    async def test_argument_dependent_approval(self, hub):
        """Only the refund over 500 waits; the one before it runs at once."""
        agent, _ = make(
            hub,
            asks(
                ("refund", {"order_id": "A", "amount": 100}),
                ("refund", {"order_id": "B", "amount": 900}),
            ),
            says("both done"),
            tools=[refund],
        )  # fmt: skip
        store = InMemoryStateStore()
        res = await Runner.run(agent, "refund both", store=store)
        assert res.status == "interrupted" and RAN == ["refund:A:100"]
        (asked,) = res.interruptions
        assert (asked.call_id, asked.args) == ("t0_1", {"order_id": "B", "amount": 900})
        assert asked.reason == "'refund' asks for approval for these arguments"
        assert list((await store.load(res.run_id)).pending.results) == ["t0_0"]
        done = await Runner.resume(agent, res.run_id, store=store, approvals={asked.id: Approve()})
        assert RAN == ["refund:A:100", "refund:B:900"], "the first did not run twice"
        assert done.status == "completed" and tool_ids(done.messages) == ["t0_0", "t0_1"]

    async def test_a_waiting_call_holds_the_sequential_calls_after_it(self, hub):
        agent, _ = make(
            hub,
            asks(
                ("refund", {"order_id": "B", "amount": 900}),
                ("refund", {"order_id": "A", "amount": 100}),
            ),
            says("done"),
            tools=[refund],
        )  # fmt: skip
        store = InMemoryStateStore()
        res = await Runner.run(agent, "refund both", store=store)
        assert res.status == "interrupted" and RAN == [], "order holds across the wait"
        done = await Runner.resume(
            agent, res.run_id, store=store, approvals={res.interruptions[0].id: Approve()}
        )
        assert RAN == ["refund:B:900", "refund:A:100"] and done.status == "completed"

    async def test_an_unanswered_interruption_keeps_waiting_with_its_id(self, hub):
        agent, llm = make(
            hub,
            asks(("refund", {"order_id": "A", "amount": 700}), ("wipe", {"path": "/x"})),
            says("done"),
            tools=[refund, wipe],
        )
        store = InMemoryStateStore()
        res = await Runner.run(agent, "go", store=store)
        (first,) = res.interruptions  # wipe waits behind refund: sequential
        again = await Runner.resume(agent, res.run_id, store=store)
        assert again.status == "interrupted" and again.interruptions == [first]
        second = await Runner.resume(
            agent, res.run_id, store=store, approvals={first.id: Approve()}
        )
        (then,) = second.interruptions
        assert then.tool == "wipe" and RAN == ["refund:A:700"] and llm.calls == 1
        done = await Runner.resume(agent, res.run_id, store=store, approvals={then.id: Deny()})
        assert done.status == "completed" and RAN == ["refund:A:700"]

    async def test_answers_for_ids_the_run_does_not_wait_on_are_refused(self, hub):
        agent, _ = make(hub, asks(("wipe", {"path": "/x"})), says(), tools=[wipe])
        store = InMemoryStateStore()
        res = await Runner.run(agent, "wipe", store=store)
        with pytest.raises(ValueError, match="is not waiting on"):
            await Runner.resume(agent, res.run_id, store=store, approvals={"nope": Approve()})
        with pytest.raises(TypeError, match="Approve\\(\\) or Deny"):
            await Runner.resume(
                agent, res.run_id, store=store, approvals={res.interruptions[0].id: True}
            )

    async def test_without_a_store_the_call_is_refused(self, hub):
        agent, _ = make(hub, asks(("wipe", {"path": "/x"})), says("no"), tools=[wipe])
        res = await Runner.run(agent, "wipe")
        assert res.status == "completed" and RAN == []
        assert NO_APPROVER.format(name="wipe") in [m["content"] for m in res.messages]

    async def test_durability_exit_still_saves_an_interrupted_run(self, hub):
        agent, _ = make(hub, asks(("wipe", {"path": "/x"})), says("wiped"), tools=[wipe])
        store = InMemoryStateStore()
        res = await Runner.run(agent, "wipe", store=store, durability="exit")
        assert (await store.load(res.run_id)).status == "interrupted"
        done = await Runner.resume(
            agent, res.run_id, store=store, approvals={res.interruptions[0].id: Approve()}
        )
        assert done.status == "completed" and RAN == ["wipe:/x"]


async def test_the_stream_says_what_waits(hub):
    agent, _ = make(hub, asks(("wipe", {"path": "/x"})), says("wiped"), tools=[wipe])
    store = InMemoryStateStore()
    events = [e async for e in Runner.stream(agent, "wipe", store=store)]
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["RunStarted", "TurnStarted", "ApprovalRequired", "RunFinished"]
    asked, finished = events[2], events[-1]
    assert (
        finished.result.status == "interrupted" and asked.id == finished.result.interruptions[0].id
    )
    assert asked.to_json()["path"] == ["agent"]
    resumed = [
        type(e).__name__
        async for e in Runner.resume_stream(
            agent, finished.result.run_id, store=store, approvals={asked.id: Approve()}
        )
    ]
    assert resumed == [
        "RunStarted", "TurnStarted", "ToolCallStarted", "ToolCallFinished", "TurnFinished",
        "TurnStarted", "TextDelta", "TextDelta", "TurnFinished", "RunFinished",
    ]  # fmt: skip
