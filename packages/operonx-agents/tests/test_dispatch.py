"""Dispatch: one tool message per call, whatever happens to the call.

Ported invariants from ``operonx/tests/internal/agents/test_dispatch*.py``
(a timeout, an unknown tool, a denied tool each produce exactly one
message) plus the A2 list: an argument error names the field,
``ModelRetry`` round-trips, sequential tools never overlap, and a tool
another agent owns answers "no tool named".
"""

from __future__ import annotations

import asyncio
import time

import pytest
from operonx import END, START, Operon, graph, op

from operonx_agents import ModelRetry, RunContext, ToolPolicy, Toolset, dispatch, tool

SPANS: list = []


@tool(readonly=True)
async def lookup(order_id: str) -> dict:
    """Look up an order."""
    start = time.perf_counter()
    await asyncio.sleep(0.03)
    SPANS.append(("lookup", start, time.perf_counter()))
    return {"order_id": order_id, "status": "shipped"}


@tool
async def write_note(order_id: str, note: str) -> str:
    """Write a note on an order (not readonly: runs alone)."""
    start = time.perf_counter()
    await asyncio.sleep(0.03)
    SPANS.append(("write_note", start, time.perf_counter()))
    return "saved"


@tool
async def refund(ctx: RunContext, order_id: str, amount: float) -> str:
    """Refund an order."""
    if amount > ctx.deps["limit"]:
        raise ModelRetry(f"amount exceeds the {ctx.deps['limit']} limit; ask a human")
    return f"refunded {amount} on {order_id} ({ctx.tool_call_id})"


@tool(timeout=0.05)
async def hang() -> str:
    """Never answers."""
    await asyncio.sleep(5)


@tool
def boom() -> str:
    """Raises."""
    raise RuntimeError("disk on fire")


@tool(destructive=True)
async def wipe(path: str) -> str:
    """Delete a path."""
    return f"wiped {path}"


@tool(approval=lambda ctx, args: args["amount"] > 100)
async def pay(amount: float) -> str:
    """Pay."""
    return f"paid {amount}"


TOOLS = Toolset([lookup, write_note, refund, hang, boom, wipe, pay])


def call(name, args, id="c1"):
    return {"id": id, "name": name, "args": args}


async def one(c, **kw):
    (msg,) = await dispatch([c], TOOLS, ctx=RunContext(deps={"limit": 50}), **kw)
    return msg


class TestOneMessagePerCall:
    async def test_success(self):
        msg = await one(call("lookup", {"order_id": "A1"}))
        assert msg == {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "lookup",
            "content": '{"order_id": "A1", "status": "shipped"}',
            "status": "success",
        }

    async def test_argument_error_names_the_field(self):
        msg = await one(call("refund", {"order_id": "A1", "amount": "lots"}))
        assert msg["status"] == "error"
        assert "invalid arguments for 'refund'" in msg["content"]
        assert "amount:" in msg["content"] and "order_id" not in msg["content"].split(":", 1)[1]

    async def test_missing_argument_names_the_field(self):
        msg = await one(call("lookup", {}))
        assert "order_id: Field required" in msg["content"]

    async def test_arguments_that_were_not_json(self):
        msg = await one(call("lookup", '{"order_id": "A'))
        assert "could not parse arguments for 'lookup'" in msg["content"]

    async def test_model_retry_round_trips(self):
        msg = await one(call("refund", {"order_id": "A1", "amount": 80}))
        assert msg["status"] == "error"
        assert msg["content"] == "amount exceeds the 50 limit; ask a human"
        ok = await one(call("refund", {"order_id": "A1", "amount": 20}, id="c2"))
        assert ok["status"] == "success" and ok["content"] == "refunded 20.0 on A1 (c2)"

    async def test_timeout(self):
        start = time.perf_counter()
        msg = await one(call("hang", {}))
        assert time.perf_counter() - start < 1
        assert msg["content"] == "Error: tool 'hang' timed out after 0.05s."

    async def test_exception(self):
        msg = await one(call("boom", {}))
        assert msg["content"] == "Error: tool 'boom' failed: RuntimeError: disk on fire"

    async def test_unknown_tool(self):
        msg = await one(call("shell", {"cmd": "ls"}))
        assert msg["status"] == "error"
        assert msg["content"].startswith("Error: no tool named 'shell'. Available tools: lookup,")

    async def test_denied_tool_never_runs(self):
        msg = await one(
            call("lookup", {"order_id": "A1"}), policy=ToolPolicy(default="deny", readonly="deny")
        )
        assert "policy forbids" in msg["content"]

    async def test_deny_beats_unknown(self):
        msg = await one(call("shell", {}), policy=ToolPolicy(rules={"shell": "deny"}))
        assert "policy forbids" in msg["content"] and "no tool named" not in msg["content"]

    async def test_every_outcome_in_one_turn_answers_its_own_id(self):
        calls = [
            call("lookup", {"order_id": "A1"}, "a"),
            call("refund", {"order_id": "A1", "amount": 99}, "b"),
            call("hang", {}, "c"),
            call("nope", {}, "d"),
            call("lookup", {}, "e"),
            call("wipe", {"path": "/"}, "f"),
        ]
        out = await dispatch(calls, TOOLS, ctx=RunContext(deps={"limit": 50}))
        assert [m["tool_call_id"] for m in out] == list("abcdef")
        assert [m["status"] for m in out] == ["success"] + ["error"] * 5


class TestApproval:
    async def test_destructive_without_an_approver_fails_closed(self):
        msg = await one(call("wipe", {"path": "/tmp/x"}))
        assert msg["status"] == "error" and "no way to ask" in msg["content"]

    async def test_approver_yes_and_no(self):
        asked = []

        async def yes(c, spec):
            asked.append((c["name"], c["args"]))
            return True

        async def no(c, spec):
            return False

        assert (await one(call("wipe", {"path": "/x"}), approve=yes))["content"] == "wiped /x"
        assert asked == [("wipe", {"path": "/x"})]
        assert "declined" in (await one(call("wipe", {"path": "/x"}), approve=no))["content"]

    async def test_argument_dependent_approval(self):
        assert (await one(call("pay", {"amount": 10})))["content"] == "paid 10.0"
        assert "no way to ask" in (await one(call("pay", {"amount": 500})))["content"]


class TestConcurrency:
    async def test_sequential_tools_never_overlap(self):
        """Recorded timestamps: no sequential call overlaps any other call,
        and they run in emitted order after the concurrent ones."""
        SPANS.clear()
        calls = [
            call("write_note", {"order_id": "A", "note": "1"}, "1"),
            call("lookup", {"order_id": "A"}, "2"),
            call("write_note", {"order_id": "B", "note": "2"}, "3"),
            call("lookup", {"order_id": "B"}, "4"),
            call("write_note", {"order_id": "C", "note": "3"}, "5"),
        ]
        out = await dispatch(calls, TOOLS)
        assert [m["tool_call_id"] for m in out] == ["1", "2", "3", "4", "5"]
        notes = [s for s in SPANS if s[0] == "write_note"]
        lookups = [s for s in SPANS if s[0] == "lookup"]
        assert len(notes) == 3 and len(lookups) == 2
        for i, (_, start, end) in enumerate(SPANS):
            for j, (name, s2, e2) in enumerate(SPANS):
                if i != j and (SPANS[i][0] == "write_note" or name == "write_note"):
                    assert end <= s2 or e2 <= start, f"{SPANS[i]} overlaps {SPANS[j]}"
        # The two readonly lookups did run together.
        (_, a0, a1), (_, b0, b1) = lookups
        assert a0 < b1 and b0 < a1

    async def test_each_call_gets_its_own_context(self):
        out = await dispatch(
            [call("refund", {"order_id": "A", "amount": 1}, f"id{i}") for i in range(3)],
            TOOLS,
            ctx=RunContext(deps={"limit": 50}),
        )
        assert [m["content"][-5:] for m in out] == ["(id0)", "(id1)", "(id2)"]


class TestIsolation:
    async def test_a_tool_another_agent_owns_is_unknown(self):
        """The A4 gate's regression, checked from A2: no global registry."""
        billing = Toolset([refund])
        support = Toolset([lookup])
        assert "refund" in billing
        (msg,) = await dispatch([call("refund", {"order_id": "A", "amount": 1})], support)
        assert msg["content"] == (
            "Error: no tool named 'refund'. Available tools: lookup. "
            "Call one of those, or answer without a tool."
        )

    def test_two_tools_one_name(self):
        other = tool(lambda order_id: order_id, name="lookup", description="Another.")
        with pytest.raises(ValueError, match="two tools named 'lookup'"):
            Toolset([lookup, other])


@op
async def agent_turn(order_id: str) -> dict:
    msgs = await dispatch(
        [call("lookup", {"order_id": order_id}, "t1"), call("nope", {}, "t2")], TOOLS
    )
    return {"messages": msgs}


@graph
def two_calls(order_id):
    t = agent_turn(order_id=order_id)
    START >> t >> END


@op
async def odd_turn() -> dict:
    return {"m": await dispatch([call("a.b[0]", {})], TOOLS)}


@graph
def odd_name():
    t = odd_turn()
    START >> t >> END


class TestTracing:
    async def test_each_call_is_a_child_execution(self):
        handle = Operon(two_calls, params={"order_id": None}).start({"order_id": "A1"})
        out = await handle.result()
        assert len(out["messages"]) == 2
        # Recorded as each finishes; the two ran together.
        tools = sorted(
            (n for n in handle.trace.nodes if n.op_type == "tool"), key=lambda n: n.op_name
        )
        assert [n.op_name for n in tools] == ["lookup", "nope"]
        assert tools[0].attrs == {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "lookup",
            "gen_ai.tool.call.id": "t1",
        }
        assert tools[0].inputs == {"args": {"order_id": "A1"}}
        assert tools[0].outputs["tool_message"]["status"] == "success"
        assert tools[0].op_full_name.endswith(".t.lookup")

    async def test_a_name_no_trace_segment_can_hold(self):
        handle = Operon(odd_name).start({})
        out = await handle.result()
        assert "no tool named 'a.b[0]'" in out["m"][0]["content"]
