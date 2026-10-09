"""Tools that are operonx ops and graphs.

An ``@op`` or ``@graph`` in ``tools=[...]`` (or through ``tool(...)``) is a
tool: its schema comes from its function's signature and docstring, and a
call runs it as a step of the agent's run (``operonx.invoke``), so its ops
are in the trace under the tool's call. Every dispatch rule holds for it:
validation, policy, approval, a failure the model reads.
"""

from __future__ import annotations

import pytest
from operonx import END, START, Operon, graph, op
from operonx.telemetry.consumers.langfuse import build_tree

from operonx_agents import Agent, AgentOp, Approve, InMemoryStateStore, Model, Runner, tool
from operonx_agents.errors import ToolDefinitionError
from operonx_agents.run.context import RunContext
from tests.agents import RAN, asks, says
from tests.fakes import ScriptedLLM


@pytest.fixture(autouse=True)
def _ran():
    RAN.clear()
    yield


@op
def lookup(order_id: str) -> dict:
    """The shipping status of an order.

    Args:
        order_id: The 8-character order code.
    """
    RAN.append(f"lookup:{order_id}")
    return {"status": f"{order_id}: shipped"}


@op
def fetch(url: str) -> dict:
    return {"html": f"<p>page {url}</p>"}


@op
def visible(html: str) -> dict:
    return {"text": html.replace("<p>", "").replace("</p>", ""), "chars": len(html)}


@graph
def read_page(url: str):
    """Read a web page: its visible text.

    Args:
        url: The page's address.
    """
    f = fetch(url=url)
    v = visible(html=f["html"])
    START >> f >> v >> END


@op
def explode(order_id: str) -> dict:
    """Always fails."""
    raise RuntimeError(f"warehouse down for {order_id}")


@op
def refund_op(order_id: str, amount: int) -> dict:
    """Refund an order."""
    RAN.append(f"refund:{order_id}:{amount}")
    return {"done": f"refunded {amount} on {order_id}"}


def _agent(tools, model="m"):
    return Agent(name="support", model=Model(model), instructions="Help.", tools=tools)


def _tree(trace):
    nodes = build_tree(trace)
    return nodes, {nid: n["parent"] for nid, n in nodes.items()}


class TestSchema:
    def test_an_op_and_a_graph_are_tools_with_their_functions_schema(self):
        agent = _agent([lookup, tool(read_page, readonly=True)])
        by = {t.name: t for t in agent.tools}
        assert (by["lookup"].kind, by["read_page"].kind) == ("op", "graph")
        assert by["lookup"].spec.description == "The shipping status of an order."
        assert by["lookup"].spec.params_schema["properties"]["order_id"] == {
            "description": "The 8-character order code.",
            "type": "string",
        }
        assert by["lookup"].spec.params_schema["required"] == ["order_id"]
        assert by["read_page"].spec.readonly and not by["read_page"].spec.sequential
        assert by["read_page"].target is read_page

    def test_an_op_cannot_take_a_run_context(self):
        @op
        def needs_ctx(ctx: RunContext, x: int) -> dict:
            """Reads the context."""
            return {"x": x}

        with pytest.raises(ToolDefinitionError, match="cannot take a RunContext"):
            tool(needs_ctx)


class TestRun:
    async def test_the_model_reads_an_ops_one_output_and_a_graphs_outputs(self, hub):
        llm = ScriptedLLM(
            asks(("lookup", {"order_id": "A1"}), ("read_page", {"url": "x.com"})), says("done")
        )
        hub(m=llm)
        res = await Runner.run(_agent([lookup, tool(read_page, readonly=True)]), "where is A1?")
        assert (res.status, res.output) == ("completed", "done")
        got = {m["name"]: m["content"] for m in res.messages if m["role"] == "tool"}
        assert got["lookup"] == "A1: shipped"
        assert got["read_page"] == '{"text": "page x.com", "chars": 17}'
        assert RAN == ["lookup:A1"]

    async def test_bad_arguments_are_refused_before_the_op_runs(self, hub):
        hub(m=ScriptedLLM(asks(("lookup", {"order": "A1"})), says("sorry")))
        res = await Runner.run(_agent([lookup]), "where is A1?")
        (msg,) = [m for m in res.messages if m["role"] == "tool"]
        assert msg["status"] == "error" and "order_id: Field required" in msg["content"]
        assert RAN == []

    async def test_a_failing_op_is_an_error_the_model_reads(self, hub):
        hub(m=ScriptedLLM(asks(("explode", {"order_id": "A1"})), says("it failed")))
        res = await Runner.run(_agent([explode]), "where is A1?")
        assert (res.status, res.output) == ("completed", "it failed")
        (msg,) = [m for m in res.messages if m["role"] == "tool"]
        assert msg["status"] == "error"
        assert "OpFailed" in msg["content"] and "warehouse down for A1" in msg["content"]

    async def test_an_op_tool_that_needs_approval_parks_and_runs_on_resume(self, hub):
        hub(
            m=ScriptedLLM(asks(("refund_op", {"order_id": "A1", "amount": 900})), says("Refunded."))
        )
        agent = _agent([tool(refund_op, approval="always", idempotent=False)])
        store = InMemoryStateStore()
        res = await Runner.run(agent, "refund A1", store=store)
        assert res.status == "interrupted" and RAN == []
        done = await Runner.resume(
            agent, res.run_id, store=store, approvals={res.interruptions[0].id: Approve()}
        )
        assert (done.status, done.output) == ("completed", "Refunded.")
        assert RAN == ["refund:A1:900"]


@graph
def app(question):
    a = AgentOp.of(agent=AGENT, input=question)
    START >> a >> END


AGENT = _agent([lookup, tool(read_page, readonly=True)])


class TestTrace:
    async def test_an_ops_and_a_graphs_steps_hang_under_the_tool_call(self, hub):
        hub(
            m=ScriptedLLM(
                asks(("lookup", {"order_id": "A1"}), ("read_page", {"url": "x.com"})),
                says("done"),
            )
        )
        handle = Operon(app, params={"question": None}).start({"question": "where is A1?"})
        out = await handle.result()
        assert out["status"] == "completed"
        trace = handle.trace
        _, parent = _tree(trace)
        by = {n.op_full_name: n for n in trace.nodes}
        call = by["app.a.turn.read_page"]
        assert call.op_type == "tool"
        run = by["app.a.turn.read_page.read_page"]
        assert run.op_type == "graph" and parent[run.op_id] == call.op_id
        for name in ("f", "v"):
            step = by[f"app.a.turn.read_page.read_page.{name}"]
            assert parent[step.op_id] == run.op_id
        one = by["app.a.turn.lookup.lookup.lookup"]
        assert parent[parent[one.op_id]] == by["app.a.turn.lookup"].op_id
        assert trace.status == "ok"

    async def test_a_failing_op_tool_does_not_fail_the_agents_run(self, hub):
        hub(m=ScriptedLLM(asks(("explode", {"order_id": "A1"})), says("it failed")))

        @graph
        def failing(question):
            a = AgentOp.of(agent=_agent([explode]), input=question)
            START >> a >> END

        handle = Operon(failing, params={"question": None}).start({"question": "?"})
        assert (await handle.result())["status"] == "completed"
        trace = handle.trace
        bad = [n for n in trace.nodes if n.status == "error"]
        assert {n.op_type for n in bad} == {"graph", "code"}, "the failure is recorded inside"
        assert trace.status == "ok", "the model read the error and answered"


class TestDescribe:
    def test_describe_lists_the_agents_parts_and_each_tools_kind_and_policy(self):
        billing = Agent(name="billing", model=Model("b"), instructions="You bill.")
        agent = Agent(
            name="support",
            model=Model("m", fallback=["n"]),
            instructions=lambda ctx: "built per run",
            tools=[
                lookup,
                tool(read_page, readonly=True),
                tool(refund_op, destructive=True),
                billing.as_tool(),
            ],
        )
        d = agent.describe()
        assert d["name"] == "support" and d["model"] == ["m", "n"]
        assert d["instructions"]["dynamic"] and d["instructions"]["text"] is None
        assert d["limits"] == {"turns": 25}
        by = {t["name"]: t for t in d["tools"]}
        assert [by[n]["kind"] for n in ("lookup", "read_page", "refund_op", "billing")] == [
            "op",
            "graph",
            "op",
            "agent",
        ]
        assert by["read_page"]["policy"] == "allow"
        assert by["refund_op"]["policy"] == "ask", "destructive tools ask by default"
        assert by["lookup"]["target"].endswith(":lookup")
        assert by["billing"]["agent"] == "billing"
        static = Agent(name="x", model=Model("m"), instructions="Fixed.").describe()
        assert static["instructions"] == {"text": "Fixed.", "dynamic": False, "from": None}
