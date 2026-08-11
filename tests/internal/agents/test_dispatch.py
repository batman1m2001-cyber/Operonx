"""Tool dispatch — every path must yield exactly one tool message.

That invariant is the point of the module. Providers reject a
conversation in which an assistant ``tool_call`` has no matching result,
so a dropped message does not fail here; it fails on the *next* request,
one turn away from its cause.

The trap is that operonx records an op's exception into state and returns
a partial result rather than propagating it, so a dispatch path that
lets an error escape produces no message and no traceback either.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.agents.graphs.dispatch import build_dispatch, parse_call
from operonx.agents.tool import clear_registry, tool
from operonx.checkpoint import bind_interrupt_bus
from operonx.core import Operon

pytestmark = pytest.mark.unit

NUM = {"type": "object", "properties": {"a": {"type": "number"}}, "required": ["a"]}


@pytest.fixture(autouse=True)
def _tools():
    clear_registry()
    deleted = []

    @tool(name="echo", description="Echo a number.", schema=NUM)
    async def echo(a: float) -> dict:
        return {"a": a}

    @tool(name="boom", description="Always raises.", schema={"type": "object", "properties": {}})
    async def boom() -> dict:
        raise RuntimeError("kaboom")

    @tool(
        name="slow",
        description="Sleeps.",
        schema={"type": "object", "properties": {}},
        timeout=0.05,
    )
    async def slow() -> dict:
        await asyncio.sleep(5)
        return {"never": True}

    @tool(
        name="big",
        description="Returns a lot.",
        schema={"type": "object", "properties": {}},
        max_result_chars=40,
    )
    async def big() -> dict:
        return {"blob": "x" * 500}

    @tool(name="wipe", description="Deletes everything.", schema=NUM, destructive=True)
    async def wipe(a: float) -> dict:
        deleted.append(a)
        return {"gone": a}

    yield deleted
    clear_registry()


async def _dispatch(call, *, answer=None, timeout=5.0):
    """Run one call through dispatch; answer any approval with `answer`."""
    built = build_dispatch(approval_timeout=timeout)(call=None)
    engine = Operon(built)
    handle = engine.start(inputs={"call": call})
    prompts = []

    def sink(evt):
        prompts.append(evt.payload)
        if answer is not None:
            handle.state.resume_interrupt(evt.interrupt_id, answer)

    bind_interrupt_bus(handle.state, sink=sink)
    result = await asyncio.wait_for(handle.result(), timeout=30)
    return prompts, result.get("tool_message")


class TestParseCall:
    """`parse_call` never raises — errors become data for `execute`."""

    def test_openai_function_shape(self):
        out = parse_call.__wrapped__(
            call={"id": "1", "function": {"name": "echo", "arguments": '{"a": 2}'}}
        )
        assert (out["tool_name"], out["args"], out["error"]) == ("echo", {"a": 2}, "")

    def test_plain_shape(self):
        out = parse_call.__wrapped__(call={"id": "1", "name": "echo", "args": {"a": 2}})
        assert out["args"] == {"a": 2}

    def test_unknown_tool_becomes_an_error_not_an_exception(self):
        out = parse_call.__wrapped__(call={"id": "1", "name": "nope", "args": {}})
        assert "no tool named" in out["error"]
        assert "echo" in out["error"], "must list what the model could call instead"

    def test_malformed_json_arguments(self):
        out = parse_call.__wrapped__(call={"id": "1", "name": "echo", "args": "{oops"})
        assert "could not parse arguments" in out["error"]

    def test_non_object_json_arguments(self):
        out = parse_call.__wrapped__(call={"id": "1", "name": "echo", "args": "[1,2]"})
        assert "could not parse arguments" in out["error"]

    def test_approval_only_for_destructive(self):
        assert parse_call.__wrapped__(call={"name": "echo", "args": {}})["needs_approval"] is False
        assert parse_call.__wrapped__(call={"name": "wipe", "args": {}})["needs_approval"] is True

    def test_a_broken_call_never_reaches_the_gate(self):
        """Asking a human to approve a call that cannot run trains them to
        click through."""
        out = parse_call.__wrapped__(call={"name": "wipe", "args": "{oops"})
        assert out["error"]
        assert out["needs_approval"] is False

    def test_payload_carries_what_a_human_needs(self):
        out = parse_call.__wrapped__(call={"id": "9", "name": "wipe", "args": {"a": 1}})
        assert out["approval_payload"] == {
            "tool": "wipe",
            "args": {"a": 1},
            "call_id": "9",
            "description": "Deletes everything.",
        }

    def test_empty_call(self):
        out = parse_call.__wrapped__(call=None)
        assert out["error"]
        assert out["needs_approval"] is False


class TestEveryPathReturnsOneMessage:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "call,expect_error,fragment",
        [
            pytest.param({"id": "1", "name": "echo", "args": {"a": 2}}, False, '"a": 2', id="ok"),
            pytest.param(
                {"id": "2", "name": "echo", "args": '{"a": 3}'}, False, '"a": 3', id="json-args"
            ),
            pytest.param(
                {"id": "3", "function": {"name": "echo", "arguments": '{"a": 4}'}},
                False,
                '"a": 4',
                id="openai-shape",
            ),
            pytest.param(
                {"id": "4", "name": "nope", "args": {}}, True, "no tool named", id="unknown"
            ),
            pytest.param(
                {"id": "5", "name": "echo", "args": "{oops"}, True, "parse", id="bad-json"
            ),
            pytest.param({"id": "6", "name": "boom", "args": {}}, True, "kaboom", id="raises"),
            pytest.param({"id": "7", "name": "slow", "args": {}}, True, "timed out", id="timeout"),
        ],
    )
    async def test_path(self, call, expect_error, fragment):
        _, msg = await _dispatch(call)
        assert msg is not None, "a dropped tool message 400s the provider next turn"
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == call["id"]
        assert msg["status"] == ("error" if expect_error else "success")
        assert fragment in msg["content"]

    @pytest.mark.asyncio
    async def test_result_truncation_is_announced(self):
        """A model shown a half-file with no marker reasons about it as if
        it were whole."""
        _, msg = await _dispatch({"id": "1", "name": "big", "args": {}})
        assert "[truncated:" in msg["content"]
        assert len(msg["content"]) < 200


class TestHumanInTheLoop:
    @pytest.mark.asyncio
    async def test_approved_runs_and_shows_real_values(self, _tools):
        prompts, msg = await _dispatch(
            {"id": "1", "name": "wipe", "args": {"a": 7}}, answer={"approved": True}
        )
        assert prompts == [
            {"tool": "wipe", "args": {"a": 7}, "call_id": "1", "description": "Deletes everything."}
        ], "the human must see the tool and its arguments, not Refs"
        assert msg["status"] == "success"
        assert _tools == [7]

    @pytest.mark.asyncio
    async def test_denied_blocks_execution(self, _tools):
        _, msg = await _dispatch(
            {"id": "1", "name": "wipe", "args": {"a": 7}}, answer={"approved": False}
        )
        assert msg["status"] == "error"
        assert "declined" in msg["content"]
        assert _tools == [], "a denied call must not run"

    @pytest.mark.asyncio
    async def test_expiry_is_denial_and_says_so(self, _tools):
        """Expiry and refusal are both 'not approved', but telling the
        model a human declined when nobody answered is a lie it will act
        on."""
        _, msg = await _dispatch(
            {"id": "1", "name": "wipe", "args": {"a": 7}}, answer=None, timeout=0.2
        )
        assert msg["status"] == "error"
        assert "expired" in msg["content"]
        assert _tools == []

    @pytest.mark.asyncio
    async def test_non_destructive_never_prompts(self):
        prompts, msg = await _dispatch({"id": "1", "name": "echo", "args": {"a": 1}})
        assert prompts == []
        assert msg["status"] == "success"
