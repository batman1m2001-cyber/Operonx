"""Tool permission policy (ported with ``ToolPolicy`` from
``operonx/tests/internal/agents/test_policy.py``; the dispatch-level cases
are in ``test_dispatch.py``).

A policy's failure mode is permissiveness — a rule that does not match,
or a value that is not understood, must never quietly widen what the
agent may do. These tests are mostly about that direction.
"""

from __future__ import annotations

import pytest

from operonx_agents.tools.policy import DEFAULT_POLICY, ToolPolicy

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
