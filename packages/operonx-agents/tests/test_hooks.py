"""Hooks: a tripwire ends the run ``blocked``; replacements are honoured.

The gate bullets (AGENTS_V2_PLAN §A4): a hook tripwire ends the run
``blocked`` (and the turn it cut writes nothing), and a ``before_tool``
replacement is what the tool runs with. Then the merge rule — deny > ask >
allow, a hook can tighten the policy and never loosen it — and the other
four methods.
"""

from __future__ import annotations

import pytest

from operonx_agents import (
    Approve,
    Ask,
    Deny,
    Hooks,
    InMemorySession,
    InMemoryStateStore,
    ModelRequest,
    Runner,
    ToolPolicy,
    Tripwire,
    tool,
)
from operonx_agents.tools.dispatch import HOOK_DENIED
from tests.agents import RAN, asks, make, says


@pytest.fixture(autouse=True)
def _ran():
    RAN.clear()
    yield


@tool
async def refund(order_id: str, amount: int, currency: str = "USD") -> str:
    """Refund an order."""
    RAN.append(f"refund:{order_id}:{amount}:{currency}")
    return f"refunded {amount} {currency}"


class Cap(Hooks):
    """Refunds over 500 trip the wire; others get their currency fixed."""

    async def before_tool(self, ctx, call):
        if call.name == "refund" and call.args["amount"] > 500:
            raise Tripwire(f"refund of {call.args['amount']} requested")
        if call.name == "refund":
            return call.replace(args={**call.args, "currency": "VND"})
        return None


class TestTripwire:
    async def test_a_before_tool_tripwire_ends_the_run_blocked_and_writes_nothing(self, hub):
        agent, llm = make(
            hub, asks(("refund", {"order_id": "A", "amount": 900})), says(), tools=[refund],
            hooks=[Cap()],
        )  # fmt: skip
        session, store = InMemorySession(), InMemoryStateStore()
        await session.add_items([{"role": "user", "content": "earlier"}])
        res = await Runner.run(agent, "refund", session=session, store=store)
        assert (res.status, res.output, RAN) == ("blocked", None, [])
        assert res.error == "Tripwire: refund of 900 requested"
        assert await session.get_items() == [{"role": "user", "content": "earlier"}]
        saved = await store.load(res.run_id)
        assert (saved.status, saved.turn) == ("blocked", 0)
        again = await Runner.resume(agent, res.run_id, store=store)
        assert again.status == "blocked" and llm.calls == 1, "blocked is final"

    async def test_an_input_guardrail_trips_before_the_model_is_called(self, hub):
        class NoSecrets(Hooks):
            async def before_model(self, ctx, request):
                if any("password" in str(m.get("content")) for m in request.messages):
                    raise Tripwire("the user sent a password")

        agent, llm = make(hub, says("never"), hooks=[NoSecrets()])
        res = await Runner.run(agent, "my password is hunter2")
        assert (res.status, res.error, llm.calls) == (
            "blocked",
            "Tripwire: the user sent a password",
            0,
        )

    async def test_the_stream_ends_with_the_blocked_result(self, hub):
        agent, _ = make(
            hub, asks(("refund", {"order_id": "A", "amount": 900})), says(), tools=[refund],
            hooks=[Cap()],
        )  # fmt: skip
        events = [e async for e in Runner.stream(agent, "refund")]
        assert type(events[-1]).__name__ == "RunFinished"
        assert events[-1].result.status == "blocked"
        assert "TurnFinished" not in [type(e).__name__ for e in events]


class TestBeforeTool:
    async def test_a_replacement_is_what_the_tool_runs_with(self, hub):
        agent, llm = make(
            hub, asks(("refund", {"order_id": "A", "amount": 100})), says("done"),
            tools=[refund], hooks=[Cap()],
        )  # fmt: skip
        events = [e async for e in Runner.stream(agent, "refund")]
        assert RAN == ["refund:A:100:VND"]
        started = next(e for e in events if type(e).__name__ == "ToolCallStarted")
        assert started.args == {"order_id": "A", "amount": 100, "currency": "VND"}
        assert llm.requests[1]["messages"][-1]["content"] == "refunded 100 VND"

    async def test_a_replacement_is_validated(self, hub):
        class Bad(Hooks):
            async def before_tool(self, ctx, call):
                return call.replace(args={**call.args, "amount": "lots"})

        agent, _ = make(
            hub, asks(("refund", {"order_id": "A", "amount": 1})), says(), tools=[refund],
            hooks=[Bad()],
        )  # fmt: skip
        res = await Runner.run(agent, "refund")
        assert res.status == "failed" and "amount" in res.error and RAN == []

    async def test_a_hook_may_not_change_which_tool_runs(self, hub):
        from operonx_agents import ToolCall

        class Swap(Hooks):
            async def before_tool(self, ctx, call):
                return ToolCall(call.id, "echo", {"a": 1})

        agent, _ = make(
            hub, asks(("refund", {"order_id": "A", "amount": 1})), says(), hooks=[Swap()],
            tools=[refund],
        )  # fmt: skip
        res = await Runner.run(agent, "refund")
        assert res.status == "failed" and "not which call it is" in res.error

    async def test_deny_is_a_refusal_the_model_reads_never_a_question(self, hub):
        class Never(Hooks):
            async def before_tool(self, ctx, call):
                return Deny("refunds are frozen today")

        agent, _ = make(
            hub, asks(("refund", {"order_id": "A", "amount": 1})), says("ok"), tools=[refund],
            hooks=[Never()],
        )  # fmt: skip
        res = await Runner.run(agent, "refund", store=InMemoryStateStore())
        assert (res.status, res.interruptions, RAN) == ("completed", [], [])
        (message,) = [m for m in res.messages if m["role"] == "tool"]
        assert message["content"] == HOOK_DENIED.format(
            reason="refunds are frozen today.", name="refund"
        )

    async def test_ask_parks_the_call_with_the_hooks_reason(self, hub):
        class Check(Hooks):
            async def before_tool(self, ctx, call):
                return Ask("first refund for this customer")

        agent, _ = make(
            hub, asks(("refund", {"order_id": "A", "amount": 1})), says("done"), tools=[refund],
            hooks=[Check()],
        )  # fmt: skip
        store = InMemoryStateStore()
        res = await Runner.run(agent, "refund", store=store)
        (asked,) = res.interruptions
        assert asked.reason == "first refund for this customer"
        done = await Runner.resume(agent, res.run_id, store=store, approvals={asked.id: Approve()})
        assert done.status == "completed" and RAN == ["refund:A:1:USD"]

    async def test_verdicts_merge_deny_over_ask(self, hub):
        class Asks(Hooks):
            async def before_tool(self, ctx, call):
                return Ask("maybe")

        class Denies(Hooks):
            async def before_tool(self, ctx, call):
                return Deny("no")

        agent, _ = make(
            hub, asks(("refund", {"order_id": "A", "amount": 1})), says(), tools=[refund],
            hooks=[Asks(), Denies()],
        )  # fmt: skip
        res = await Runner.run(agent, "refund", store=InMemoryStateStore())
        assert res.interruptions == [] and "Blocked: no." in res.messages[2]["content"]

    async def test_a_hook_cannot_loosen_the_policy(self, hub):
        """The policy denies; a hook replacing the call (its only way to say
        "go") does not get it run."""
        agent, _ = make(
            hub, asks(("refund", {"order_id": "A", "amount": 1})), says(), tools=[refund],
            hooks=[Cap()], policy=ToolPolicy(default="allow", rules={"refund": "deny"}),
        )  # fmt: skip
        res = await Runner.run(agent, "refund")
        assert RAN == [] and "policy forbids" in res.messages[2]["content"]


class TestOtherHooks:
    async def test_after_tool_replaces_what_the_model_reads(self, hub):
        class Shorten(Hooks):
            async def after_tool(self, ctx, call, content):
                return content.upper()

        agent, llm = make(
            hub, asks(("refund", {"order_id": "A", "amount": 1})), says(), tools=[refund],
            hooks=[Shorten()],
        )  # fmt: skip
        await Runner.run(agent, "refund")
        assert llm.requests[1]["messages"][-1]["content"] == "REFUNDED 1 USD"

    async def test_before_model_replaces_the_request_not_the_history(self, hub):
        class Pirate(Hooks):
            async def before_model(self, ctx, request):
                extra = {"role": "user", "content": "Answer like a pirate."}
                return ModelRequest([*request.messages, extra], request.tools)

        agent, llm = make(hub, says("arr"), hooks=[Pirate()])
        res = await Runner.run(agent, "hi")
        assert llm.requests[0]["messages"][-1]["content"] == "Answer like a pirate."
        assert [m["content"] for m in res.messages] == ["hi", "arr"]

    async def test_after_model_and_on_output(self, hub):
        class Edit(Hooks):
            async def after_model(self, ctx, response):
                import dataclasses

                return dataclasses.replace(response, content=response.content + "!")

            async def on_output(self, ctx, output):
                return output.strip("!") + " (checked)"

        agent, _ = make(hub, says("fine"), hooks=[Edit()])
        res = await Runner.run(agent, "hi")
        assert res.output == "fine (checked)" and res.messages[-1]["content"] == "fine!"

    def test_hooks_must_be_hooks(self, hub):
        with pytest.raises(TypeError, match="Hooks instances"):
            make(hub, says(), hooks=[lambda ctx: None])
