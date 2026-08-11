"""Tool permission policy.

A policy's failure mode is permissiveness — a rule that does not match,
or a value that is not understood, must never quietly widen what the
agent may do. These tests are mostly about that direction.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.agents.graphs.dispatch import build_dispatch, parse_call
from operonx.agents.policy import DEFAULT_POLICY, ToolPolicy
from operonx.agents.tool import clear_registry, tool
from operonx.core import Operon

pytestmark = pytest.mark.unit

NUM = {"type": "object", "properties": {"a": {"type": "number"}}, "required": ["a"]}

DESTRUCTIVE = {"destructive": True}
READONLY = {"readonly": True}
PLAIN: dict = {}


class TestResolutionOrder:
    def test_per_tool_rule_beats_everything(self):
        p = ToolPolicy(default="deny", destructive="ask", rules={"wipe": "allow"})
        assert p.decide("wipe", DESTRUCTIVE) == "allow"

    def test_destructive_beats_default(self):
        assert ToolPolicy(default="allow", destructive="ask").decide("wipe", DESTRUCTIVE) == "ask"

    def test_readonly_beats_default(self):
        assert ToolPolicy(default="deny", readonly="allow").decide("read", READONLY) == "allow"

    def test_default_applies_when_nothing_matches(self):
        assert ToolPolicy(default="deny").decide("misc", PLAIN) == "deny"

    def test_destructive_wins_over_readonly_if_both_declared(self):
        """A tool claiming both is contradictory; the stricter side must
        win, or a mislabelled tool escapes review."""
        p = ToolPolicy(default="allow", destructive="deny", readonly="allow")
        assert p.decide("odd", {"destructive": True, "readonly": True}) == "deny"

    def test_none_falls_through_to_default(self):
        p = ToolPolicy(default="deny", destructive=None, readonly=None)
        assert p.decide("wipe", DESTRUCTIVE) == "deny"
        assert p.decide("read", READONLY) == "deny"

    def test_rule_applies_to_an_unregistered_tool(self):
        """`rules={"shell": "deny"}` must hold even when the tool is not
        loaded — otherwise the model gets a 'no such tool' hint that
        invites it to look for another way in."""
        assert ToolPolicy(rules={"shell": "deny"}).decide("shell", None) == "deny"


class TestValidation:
    @pytest.mark.parametrize("field", ["default", "destructive", "readonly"])
    def test_unknown_outcome_rejected(self, field):
        with pytest.raises(ValueError, match=r"not one of"):
            ToolPolicy(**{field: "allowed"})

    def test_unknown_outcome_in_rules_rejected(self):
        with pytest.raises(ValueError, match=r"rules\['x'\]"):
            ToolPolicy(rules={"x": "yes"})

    def test_error_names_the_valid_values(self):
        with pytest.raises(ValueError) as exc:
            ToolPolicy(default="nope")
        assert "'allow'" in str(exc.value) and "'deny'" in str(exc.value)


class TestDefaultPolicy:
    """The default must reproduce pre-policy behaviour exactly, so adding
    the layer changed no existing agent."""

    def test_destructive_asks(self):
        assert DEFAULT_POLICY.decide("wipe", DESTRUCTIVE) == "ask"

    def test_everything_else_runs(self):
        assert DEFAULT_POLICY.decide("echo", PLAIN) == "allow"
        assert DEFAULT_POLICY.decide("read", READONLY) == "allow"


class TestParseCallIntegration:
    @pytest.fixture(autouse=True)
    def _registered(self):
        clear_registry()

        @tool(name="echo", description="Echo.", schema=NUM)
        async def echo(a: float) -> dict:
            return {"a": a}

        @tool(name="wipe", description="Wipe.", schema=NUM, destructive=True)
        async def wipe(a: float) -> dict:
            return {"gone": a}

        yield
        clear_registry()

    def test_deny_beats_unknown_tool(self):
        """A denied rule must refuse even when the tool is not loaded.
        "No such tool" tells the model the capability is merely absent
        and invites it to look for another route."""
        out = parse_call.__wrapped__(
            call={"id": "1", "name": "shell", "args": {}},
            policy=ToolPolicy(rules={"shell": "deny"}),
        )
        assert "policy forbids" in out["error"]
        assert "no tool named" not in out["error"]

    def test_unknown_tool_still_reported_when_not_denied(self):
        out = parse_call.__wrapped__(
            call={"id": "1", "name": "nope", "args": {}},
            policy=ToolPolicy(default="allow"),
        )
        assert "no tool named" in out["error"]

    def test_deny_short_circuits_before_the_gate(self):
        """Asking a human to approve what policy already forbids trains
        them to click through, and the answer would be ignored."""
        # `destructive` must be denied explicitly — `default="deny"` alone
        # loses to the more specific destructive rule, which is the
        # resolution order working as intended.
        out = parse_call.__wrapped__(
            call={"id": "1", "name": "wipe", "args": {"a": 1}},
            policy=ToolPolicy(default="deny", destructive="deny"),
        )
        assert out["needs_approval"] is False
        assert "policy forbids" in out["error"]
        assert "do not retry" in out["error"].lower()

    def test_ask_sets_the_gate(self):
        out = parse_call.__wrapped__(
            call={"id": "1", "name": "echo", "args": {"a": 1}},
            policy=ToolPolicy(default="ask"),
        )
        assert out["needs_approval"] is True

    def test_allow_skips_the_gate(self):
        out = parse_call.__wrapped__(
            call={"id": "1", "name": "echo", "args": {"a": 1}},
            policy=ToolPolicy(default="allow", destructive="allow"),
        )
        assert out["needs_approval"] is False
        assert out["error"] == ""

    def test_an_earlier_error_is_not_overwritten(self):
        """A malformed call should report *why* it is malformed, not a
        policy verdict on a call that could never run."""
        out = parse_call.__wrapped__(
            call={"id": "1", "name": "echo", "args": "{oops"},
            policy=ToolPolicy(default="deny"),
        )
        assert "could not parse arguments" in out["error"]


class TestEndToEnd:
    @pytest.fixture(autouse=True)
    def _tools(self):
        clear_registry()
        ran = []

        @tool(name="echo", description="Echo.", schema=NUM, readonly=True)
        async def echo(a: float) -> dict:
            ran.append(a)
            return {"a": a}

        yield ran
        clear_registry()

    async def _run(self, policy, call):
        built = build_dispatch(approval_timeout=1.0, policy=policy)(call=None)
        result = await asyncio.wait_for(Operon(built).run(inputs={"call": call}), timeout=30)
        return result.get("tool_message")

    @pytest.mark.asyncio
    async def test_denied_tool_never_executes(self, _tools):
        msg = await self._run(
            ToolPolicy(default="deny", readonly="deny"),
            {"id": "1", "name": "echo", "args": {"a": 3}},
        )
        assert msg["status"] == "error"
        assert "policy forbids" in msg["content"]
        assert _tools == [], "a denied call must not reach the tool"

    @pytest.mark.asyncio
    async def test_allowed_tool_runs(self, _tools):
        msg = await self._run(
            ToolPolicy(default="allow"), {"id": "1", "name": "echo", "args": {"a": 3}}
        )
        assert msg["status"] == "success"
        assert _tools == [3]

    @pytest.mark.asyncio
    async def test_denial_still_produces_one_tool_message(self, _tools):
        """Policy refusal is a dispatch path like any other, so it owes
        the model a matching result."""
        msg = await self._run(
            ToolPolicy(default="deny"), {"id": "42", "name": "echo", "args": {"a": 3}}
        )
        assert msg["tool_call_id"] == "42"
        assert msg["role"] == "tool"
