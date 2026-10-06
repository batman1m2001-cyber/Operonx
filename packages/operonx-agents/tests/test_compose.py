"""Composition: an agent as another agent's tool, and as a graph's op.

- A child agent's approval surfaces on the parent with a path, and one
  resume of the parent completes both runs.
- **Isolation regression** (track3 §1.2 item 1): a model naming a tool
  another agent in the process owns gets "no tool named", run end to end.
- ``as_op`` outputs bind downstream; ``as_op(stream=True)`` events reach
  ``engine.stream(mode="custom")`` through an ``EmitOp``.
"""

from __future__ import annotations

import pytest
from operonx import END, START, EmitOp, Operon, graph, op
from operonx.checkpoint import CustomEvent
from operonx.telemetry.consumers.langfuse import build_tree

from operonx_agents import (
    Agent,
    Approve,
    InMemoryStateStore,
    Model,
    RunFinished,
    Runner,
    TextDelta,
    ToolCallFinished,
    tool,
)
from operonx_agents.run.interruption import child_run_id
from tests.agents import RAN, asks, says
from tests.fakes import ScriptedLLM


@pytest.fixture(autouse=True)
def _ran():
    RAN.clear()
    yield


@tool(approval="always")
async def refund(order_id: str, amount: int) -> str:
    """Refund an order."""
    RAN.append(f"refund:{order_id}:{amount}")
    return f"refunded {amount} on {order_id}"


@tool(readonly=True)
async def balance(account: str) -> str:
    """An account's balance."""
    RAN.append(f"balance:{account}")
    return "1200"


def billing_and_support(hub, parent_script, child_script):
    parent_llm, child_llm = ScriptedLLM(*parent_script), ScriptedLLM(*child_script)
    hub(p=parent_llm, b=child_llm)
    billing = Agent(
        name="billing", model=Model("b"), instructions="You do refunds.", tools=[refund, balance]
    )
    support = Agent(
        name="support",
        model=Model("p"),
        tools=[billing.as_tool(name="ask_billing", description="Billing: refunds, balances.")],
    )
    return support, billing, parent_llm, child_llm


class TestAgentAsTool:
    async def test_the_child_answers_with_its_output_not_its_transcript(self, hub):
        support, _, parent, child = billing_and_support(
            hub,
            [asks(("ask_billing", {"task": "balance of acct 7"})), says("It is 1200.")],
            [asks(("balance", {"account": "7"})), says("Balance: 1200")],
        )
        res = await Runner.run(support, "what is my balance?")
        assert (res.status, res.output, RAN) == ("completed", "It is 1200.", ["balance:7"])
        (message,) = [m for m in res.messages if m["role"] == "tool"]
        assert message["content"] == "Balance: 1200"
        seen = [(m["role"], m["content"]) for m in child.requests[0]["messages"]]
        assert seen == [("system", "You do refunds."), ("user", "balance of acct 7")], (
            "the child sees its task and nothing else of the conversation"
        )
        assert res.usage.requests == 4, "the child's two requests count toward the parent"

    async def test_a_childs_approval_surfaces_on_the_parent_and_one_resume_finishes_both(self, hub):
        support, billing, parent, child = billing_and_support(
            hub,
            [asks(("ask_billing", {"task": "refund 900 on A1"})), says("Refunded.")],
            [asks(("refund", {"order_id": "A1", "amount": 900})), says("Done: refunded 900")],
        )
        store = InMemoryStateStore()
        res = await Runner.run(support, "refund A1", store=store)
        assert res.status == "interrupted" and RAN == []
        (asked,) = res.interruptions
        assert asked.path == ("support", "billing")
        assert (asked.tool, asked.args) == ("refund", {"order_id": "A1", "amount": 900})

        child_id = child_run_id(res.run_id, "support", "ask_billing", 1, "t0_0")
        waiting_child = await store.load(child_id)
        assert waiting_child.status == "interrupted" and waiting_child.agent == "billing"
        assert [i["id"] for i in waiting_child.pending.interruptions] == [asked.id]

        done = await Runner.resume(
            support, res.run_id, store=store, approvals={asked.id: Approve()}
        )
        assert (done.status, done.output) == ("completed", "Refunded.")
        assert RAN == ["refund:A1:900"]
        (message,) = [m for m in done.messages if m["role"] == "tool"]
        assert message["content"] == "Done: refunded 900"
        assert (await store.load(child_id)).status == "completed"
        assert (parent.calls, child.calls) == (2, 2), "nobody asked the model twice"

    async def test_a_child_without_a_store_refuses_and_the_parent_reads_why(self, hub):
        support, _, _, _ = billing_and_support(
            hub,
            [asks(("ask_billing", {"task": "refund 900 on A1"})), says("Could not.")],
            [asks(("refund", {"order_id": "A1", "amount": 900})), says("I could not refund.")],
        )
        res = await Runner.run(support, "refund A1")
        assert (res.status, RAN) == ("completed", [])
        (message,) = [m for m in res.messages if m["role"] == "tool"]
        assert message["content"] == "I could not refund."

    async def test_isolation_a_tool_another_agent_owns_is_unknown(self, hub):
        """`refund` exists in this process — billing owns it — and support's
        model names it directly. Support's dispatch knows only its own tools."""
        support, _, _, child = billing_and_support(
            hub, [asks(("refund", {"order_id": "A1", "amount": 1})), says("ok")], [says("unused")]
        )
        res = await Runner.run(support, "refund A1", store=InMemoryStateStore())
        (message,) = [m for m in res.messages if m["role"] == "tool"]
        assert message["content"].startswith("Error: no tool named 'refund'.")
        assert "Available tools: ask_billing." in message["content"]
        assert (res.status, res.interruptions, RAN, child.calls) == ("completed", [], [], 0)

    async def test_max_turns_bounds_the_child(self, hub):
        parent_llm = ScriptedLLM(asks(("ask_billing", {"task": "loop"})), says("gave up"))
        child_llm = ScriptedLLM(asks(("balance", {"account": "1"})))
        hub(p=parent_llm, b=child_llm)
        billing = Agent(name="billing", model=Model("b"), tools=[balance])
        support = Agent(
            name="support",
            model=Model("p"),
            tools=[billing.as_tool(name="ask_billing", max_turns=2)],
        )
        res = await Runner.run(support, "go")
        (message,) = [m for m in res.messages if m["role"] == "tool"]
        assert child_llm.calls == 2
        assert "the billing agent" in message["content"]

    async def test_the_child_nests_under_the_parents_call_in_the_trace(self, hub):
        support, _, _, _ = billing_and_support(
            hub,
            [asks(("ask_billing", {"task": "balance of 7"})), says("1200")],
            [asks(("balance", {"account": "7"})), says("1200")],
        )

        @op
        async def chat(question: str) -> dict:
            return {"answer": (await Runner.run(support, question)).output}

        @graph
        def flow(question):
            c = chat(question=question)
            START >> c >> END

        handle = Operon(flow, params={"question": None}).start({"question": "hi"})
        assert (await handle.result())["answer"] == "1200"
        records = {n.op_id: n for n in handle.trace.nodes}
        tree = build_tree(handle.trace)
        parent_of = {
            records[i].op_full_name: records[t["parent"]].op_name if t["parent"] else None
            for i, t in tree.items()
            if t["kind"] == "record"
        }
        assert parent_of["flow.c.turn.ask_billing.turn"] == "ask_billing"
        assert parent_of["flow.c.turn.ask_billing.turn.balance"] == "turn"


class TestAgentAsOp:
    async def test_outputs_bind_downstream(self, hub):
        hub(m=ScriptedLLM(asks(("balance", {"account": "9"})), says("1200")))
        agent = Agent(name="teller", model=Model("m"), tools=[balance])
        teller = agent.as_op()

        @op
        def shout(output: str, status: str) -> dict:
            return {"loud": f"{status}: {output.upper()}!"}

        @graph
        def flow(question):
            t = teller(input=question)
            s = shout(output=t["output"], status=t["status"])
            START >> t >> s >> END

        out = await Operon(flow, params={"question": None}).run({"question": "balance?"})
        assert out["loud"] == "completed: 1200!"

    async def test_an_interrupted_run_is_resumed_from_its_state_id(self, hub):
        hub(m=ScriptedLLM(asks(("refund", {"order_id": "A1", "amount": 5})), says("refunded")))
        agent = Agent(name="teller", model=Model("m"), tools=[refund])
        store = InMemoryStateStore()
        teller = agent.as_op(store=store)

        @graph
        def flow(question):
            t = teller(input=question)
            START >> t >> END

        out = await Operon(flow, params={"question": None}).run({"question": "refund"})
        assert out["status"] == "interrupted" and RAN == []
        (asked,) = out["interruptions"]
        assert asked["tool"] == "refund" and asked["path"] == ["teller"]
        done = await Runner.resume(
            agent, out["state_id"], store=store, approvals={asked["id"]: Approve()}
        )
        assert (done.status, RAN) == ("completed", ["refund:A1:5"])

    async def test_streamed_events_reach_the_custom_stream(self, hub):
        hub(m=ScriptedLLM(asks(("balance", {"account": "9"})), says("It is 1200")))
        agent = Agent(name="teller", model=Model("m"), tools=[balance])
        teller = agent.as_op(stream=True)

        @graph
        def flow(question):
            t = teller(input=question)
            e = EmitOp(payload=t["event"], channel="agent", transient=True)
            START >> t >> e >> END

        got = []
        async for chunk in Operon(flow, params={"question": None}).stream(
            {"question": "balance?"}, mode="custom"
        ):
            got.append(chunk)
        assert all(isinstance(c, CustomEvent) and c.channel == "agent" for c in got)
        events = [c.payload for c in got]
        kinds = [type(e).__name__ for e in events]
        assert kinds[0] == "RunStarted" and kinds[-1] == "RunFinished"
        assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "It is 1200"
        assert any(isinstance(e, ToolCallFinished) and e.tool == "balance" for e in events)
        assert isinstance(events[-1], RunFinished) and events[-1].result.output == "It is 1200"

    async def test_streaming_adds_no_trace_record_per_event(self, hub):
        """The op and the EmitOp are transient: one record for the stream,
        none per event; the trace stays agent → turn → model / tool."""
        hub(m=ScriptedLLM(asks(("balance", {"account": "9"})), says("It is 1200 and more")))
        teller = Agent(name="teller", model=Model("m"), tools=[balance]).as_op(stream=True)

        @graph
        def flow(question):
            t = teller(input=question)
            e = EmitOp(payload=t["event"], channel="agent", transient=True)
            START >> t >> e >> END

        handle = Operon(flow, params={"question": None}).start({"question": "q"})
        await handle.result()
        assert sorted(n.op_name for n in handle.trace.nodes) == [
            "balance", "model", "model", "t", "turn", "turn",
        ]  # fmt: skip
