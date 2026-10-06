"""Trajectory evaluators over agent runs, and ``dataset_from_runs``.

Each evaluator is checked on a real run's trace (the runner's records, read
through operonx's ``TraceView``), passing and failing with a reason that
says what happened. ``dataset_from_runs`` turns recorded service runs into
cases that an ``Eval`` of the same service then passes.
"""

from __future__ import annotations

import pytest
from operonx import END, START, Operon, graph, op
from operonx.app import http
from operonx.app.evals import Eval, TraceView, trajectory
from operonx.app.serve.app import build_app
from operonx.telemetry.runs import RunFilter
from operonx.telemetry.runs.files import FilesRunStore
from pydantic import BaseModel
from starlette.testclient import TestClient

from operonx_agents import (
    Agent,
    InMemoryStateStore,
    Model,
    Runner,
    UsageLimits,
    agent_service,
    tool,
)
from operonx_agents.compose import _outputs
from operonx_agents.evals import (
    cost_at_most,
    dataset_from_runs,
    no_tool_errors,
    output_valid,
    result_of,
    tool_called,
    tool_not_called,
    turns_at_most,
)
from tests.agents import RAN, asks, says
from tests.fakes import ScriptedLLM


@pytest.fixture(autouse=True)
def _ran():
    RAN.clear()
    yield


@tool(readonly=True)
async def order_status(order_id: str) -> str:
    """The shipping status of an order."""
    if order_id == "BAD":
        raise RuntimeError("no such order")
    return f"{order_id}: shipped"


@tool(idempotent=False, approval="always")
async def refund(order_id: str, amount: int) -> str:
    """Refund an order."""
    return "refunded"


@tool(readonly=True)
async def balance(account: str) -> str:
    """An account's balance."""
    return "1200"


@op(exclude={"trace": ["agent"]})
async def run_agent(agent: Agent, question: str) -> dict:
    """The agent a test built, run as one step: ``AgentOp``'s outputs."""
    out = _outputs(await Runner.run(agent, question))
    return {"output": out["output"], "status": out["status"], "error": out["error"]}


@graph
def asked(agent, question):
    a = run_agent(agent=agent, question=question)
    START >> a >> END


async def traced(agent: Agent, question: str):
    """One run of ``agent`` in a graph: its outputs and its TraceView."""
    engine = Operon(asked, params={"agent": None, "question": None})
    handle = engine.start({"agent": agent, "question": question})
    async for _ in handle:
        pass
    return await handle.result(), TraceView.from_trace(handle.trace)


def agent_on(hub, *script, cost=None, **kw):
    hub(m=ScriptedLLM(*script, cost=cost))
    return Agent(name="support", model=Model("m"), tools=[order_status, balance], **kw)


class TestToolCalled:
    async def test_by_name_and_by_a_subset_of_its_arguments(self, hub):
        agent = agent_on(hub, asks(("order_status", {"order_id": "A1"})), says("Shipped."))
        _, trace = await traced(agent, "where is A1?")
        assert tool_called("order_status")(trace=trace)["passed"]
        assert tool_called("order_status", {"order_id": "A1"}, times=1)(trace=trace)["passed"]
        wrong = tool_called("order_status", {"order_id": "B2"})(trace=trace)
        assert not wrong["passed"]
        assert "matched 0" in wrong["reason"] and "'order_id': 'A1'" in wrong["reason"]
        twice = tool_called("order_status", times=2)(trace=trace)
        assert not twice["passed"] and "2 time(s)" in twice["reason"]

    async def test_not_called(self, hub):
        agent = agent_on(hub, asks(("order_status", {"order_id": "A1"})), says("Shipped."))
        _, trace = await traced(agent, "where is A1?")
        assert tool_not_called("balance")(trace=trace) == {"passed": True}
        got = tool_not_called("order_status")(trace=trace)
        assert not got["passed"] and "called 1 time(s)" in got["reason"]

    async def test_names_read_well_in_a_report(self, hub):
        assert (
            tool_called("a", {"x": 1}, times=2).eval_name
            == "tool_called(a, args={'x': 1}, times=2)"
        )
        assert tool_not_called("a", agent="b").eval_name == "tool_not_called(a, agent='b')"
        assert no_tool_errors().eval_name == "no_tool_errors()"


class TestNoToolErrors:
    async def test_an_exception_the_model_reads_is_an_error(self, hub):
        agent = agent_on(hub, asks(("order_status", {"order_id": "BAD"})), says("Sorry."))
        _, trace = await traced(agent, "where is BAD?")
        got = no_tool_errors()(trace=trace)
        assert not got["passed"] and "order_status" in got["reason"]
        assert "no such order" in got["reason"]

    async def test_unknown_tool_and_bad_arguments_too(self, hub):
        agent = agent_on(hub, asks(("nope", {}), ("order_status", {"order": "A1"})), says("Sorry."))
        _, trace = await traced(agent, "x")
        reason = no_tool_errors()(trace=trace)["reason"]
        assert "nope" in reason and "order_status" in reason

    async def test_clean_calls_pass(self, hub):
        agent = agent_on(hub, asks(("order_status", {"order_id": "A1"})), says("Shipped."))
        _, trace = await traced(agent, "where is A1?")
        assert no_tool_errors()(trace=trace) == {"passed": True, "reason": None}


class TestTurns:
    async def test_counts_model_turns(self, hub):
        agent = agent_on(
            hub,
            asks(("order_status", {"order_id": "A1"})),
            asks(("balance", {"account": "7"})),
            says("Done."),
        )
        _, trace = await traced(agent, "x")
        assert turns_at_most(3)(trace=trace)["passed"]
        over = turns_at_most(2)(trace=trace)
        assert not over["passed"] and over["reason"] == "3 turns > 2" and over["score"] == 0.6667

    def test_one_turn_at_least(self):
        with pytest.raises(ValueError, match="one turn at least"):
            turns_at_most(0)


class TestPerAgent:
    async def test_a_sub_agents_steps_are_its_own(self, hub):
        child_llm = ScriptedLLM(asks(("balance", {"account": "7"})), says("1200"))
        parent_llm = ScriptedLLM(asks(("ask_billing", {"task": "balance of 7"})), says("1200."))
        hub(p=parent_llm, b=child_llm)
        billing = Agent(name="billing", model=Model("b"), tools=[balance])
        support = Agent(
            name="support", model=Model("p"), tools=[billing.as_tool(name="ask_billing")]
        )
        _, trace = await traced(support, "balance?")
        assert tool_called("balance", agent="billing")(trace=trace)["passed"]
        assert not tool_called("balance", agent="support")(trace=trace)["passed"]
        assert tool_called("ask_billing", agent="support")(trace=trace)["passed"]
        assert turns_at_most(2, agent="support")(trace=trace)["passed"]
        assert not turns_at_most(3)(trace=trace)["passed"]  # 2 + 2 turns in all


class Ticket(BaseModel):
    order_id: str
    priority: int


class TestOutput:
    async def test_completed_and_valid(self, hub):
        agent = agent_on(hub, says('{"order_id": "A1", "priority": 2}'), output_type=Ticket)
        out, _ = await traced(agent, "file a ticket")
        assert output_valid()(output=out) == {"passed": True}
        assert output_valid(Ticket)(output=out) == {"passed": True}

    async def test_a_run_that_did_not_complete_fails_with_why(self, hub):
        agent = agent_on(
            hub, asks(("order_status", {"order_id": "A1"})), limits=UsageLimits(turns=1)
        )
        out, _ = await traced(agent, "x")
        got = output_valid()(output=out)
        assert not got["passed"] and "the run ended" in got["reason"]

    def test_an_output_that_does_not_validate(self):
        reply = {"status": "completed", "output": {"order_id": "A1"}}
        got = output_valid(Ticket)(output=reply)
        assert not got["passed"] and "priority" in got["reason"]

    def test_result_of_every_shape_an_agent_graph_sends(self):
        reply = {"status": "completed", "output": "x"}
        assert result_of(reply) == reply
        events = [{"type": "RunStarted"}, {"type": "RunFinished", "result": reply}]
        assert result_of(events) == reply
        assert result_of("plain text") is None
        assert not output_valid()(output="plain text")["passed"]


class TestCost:
    async def test_priced_calls_within_and_over(self, hub):
        # 10 prompt + 3 completion tokens a call, at 1e-3 / 2e-3 per token
        agent = agent_on(
            hub, asks(("order_status", {"order_id": "A1"})), says("ok"), cost=(1e-3, 2e-3)
        )
        _, trace = await traced(agent, "x")
        assert cost_at_most(0.04)(trace=trace)["passed"]  # 2 × 0.016
        over = cost_at_most(0.03)(trace=trace)
        assert not over["passed"] and "cost_usd 0.032 > 0.03" in over["reason"]

    async def test_an_unpriced_call_is_an_unknown_cost(self, hub):
        agent = agent_on(hub, says("ok"))
        _, trace = await traced(agent, "x")
        got = cost_at_most(1.0)(trace=trace)
        assert not got["passed"] and "unpriced" in got["reason"]


def desk(messages, params):
    """A model by rule, so cases may run in any order: a tool's answer is
    the reply; else A1 asks for its status and anything else the balance."""
    if messages[-1]["role"] == "tool":
        return says(messages[-1]["content"])
    if "A1" in str(messages[-1]["content"]):
        return asks(("order_status", {"order_id": "A1"}))
    if "balance" in str(messages[-1]["content"]):
        return asks(("balance", {"account": "7"}))
    return says("hi")


class TestDatasetFromRuns:
    def _serve_and_record(self, hub, tmp_path, script, questions):
        hub(m=ScriptedLLM(*script))
        agent = Agent(name="support", model=Model("m"), tools=[order_status, balance])
        store = FilesRunStore(tmp_path / "runs")
        spec = agent_service(agent, http("POST", "/s"), store=InMemoryStateStore(), trace=[store])
        with TestClient(build_app((spec,))) as client:
            replies = [client.post("/s", json=q).json() for q in questions]
        return agent, spec, store, replies

    def test_recorded_runs_become_cases_an_eval_of_the_service_passes(self, hub, tmp_path):
        agent, spec, store, replies = self._serve_and_record(
            hub, tmp_path, [desk], [{"input": "where is A1?"}, "balance of 7?"]
        )
        assert [r["status"] for r in replies] == ["completed", "completed"]
        cases = dataset_from_runs(store, RunFilter(name="support"))
        by_input = {str(c["input"]): c for c in cases}
        assert set(by_input) == {"where is A1?", "balance of 7?"}
        first = by_input["where is A1?"]
        assert first["trajectory"] == {
            "tool_calls": [{"name": "order_status", "args": {"order_id": "A1"}}]
        }
        assert first["expected"] == {"output": "A1: shipped"}
        assert first["from"]["run"] and first["tags"] == ["from_runs", "support"]

        # replay them through an Eval of the same service graph
        from operonx.app.evals import Dataset

        dataset = tmp_path / "support.jsonl"
        Dataset(dataset).add(cases)
        ev = Eval(
            "support",
            graph=spec.graph,
            dataset=str(dataset),
            evaluators=[
                trajectory.tool_calls(mode="strict", args="subset"),
                no_tool_errors(),
                output_valid(),
                lambda output, expected: result_of(output)["output"] == expected["output"],
            ],
            record_dir=tmp_path / "evals",
        )
        summary = ev.run_sync().meta["eval"]
        assert (summary["cases"], summary["passed"], summary["pass_rate"]) == (2, 2, 1.0)

    def test_a_resumed_run_is_not_a_case(self, hub, tmp_path):
        script = [asks(("refund", {"order_id": "A1", "amount": 900})), says("Refunded.")]
        hub(m=ScriptedLLM(*script))
        agent = Agent(name="support", model=Model("m"), tools=[refund])
        store = FilesRunStore(tmp_path / "runs")
        spec = agent_service(agent, http("POST", "/s"), store=InMemoryStateStore(), trace=[store])
        with TestClient(build_app((spec,))) as client:
            parked = client.post("/s", json="refund 900 on A1").json()
            (asked,) = parked["interruptions"]
            done = client.post(
                "/s/resume",
                json={"run_id": parked["run_id"], "approvals": {asked["id"]: "approve"}},
            ).json()
        assert done["status"] == "completed"
        (case,) = dataset_from_runs(store)
        assert case["input"] == "refund 900 on A1" and "expected" not in case
        assert case["trajectory"]["tool_calls"] == [
            {"name": "refund", "args": {"order_id": "A1", "amount": 900}}
        ]

    def test_only_one_agents_runs(self, hub, tmp_path):
        _, _, store, _ = self._serve_and_record(hub, tmp_path, [says("hi")], ["hello"])
        assert dataset_from_runs(store, agent="billing") == []
        assert len(dataset_from_runs(store, agent="support")) == 1
