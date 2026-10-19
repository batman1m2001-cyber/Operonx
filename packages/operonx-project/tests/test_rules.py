"""The owner's standing rules and the project's AGENTS.md reach every coder (plan D16)."""

from __future__ import annotations

from operonx_project.coder.base import CoderRequest
from operonx_project.coder.claude import ClaudeCodeCoder
from operonx_project.rules import LIMIT, standing_rules


def _system(cmd):
    return cmd[cmd.index("--append-system-prompt") + 1] if "--append-system-prompt" in cmd else ""


def test_every_session_carries_the_owners_rules_then_the_projects(tmp_path, monkeypatch):
    owner = tmp_path / "owner.md"
    owner.write_text("- No Co-Authored-By trailer in a commit.\n", encoding="utf-8")
    monkeypatch.setenv("OPERONX_OWNER_RULES", str(owner))
    (tmp_path / "AGENTS.md").write_text("# calc\n\n- Never print(); use LOGGER.\n", encoding="utf-8")
    c = ClaudeCodeCoder()
    for role in ("implementer", "reviewer", "planner"):
        system = _system(c.command(CoderRequest(prompt="", cwd=tmp_path, role=role), "claude", "sid"))
        assert system.index("No Co-Authored-By") < system.index("Never print()"), role
        assert "the engine's win" in system
    # a session's own system text comes first
    system = _system(c.command(CoderRequest(prompt="", cwd=tmp_path, system="You plan."), "claude", "sid"))
    assert system.startswith("You plan.") and "No Co-Authored-By" in system


def test_no_rules_no_flag_and_a_long_file_is_cut(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERONX_OWNER_RULES", str(tmp_path / "missing.md"))
    cmd = ClaudeCodeCoder().command(CoderRequest(prompt="", cwd=tmp_path), "claude", "sid")
    assert "--append-system-prompt" not in cmd and standing_rules(tmp_path) == ""
    (tmp_path / "AGENTS.md").write_text("x" * (LIMIT + 50), encoding="utf-8")
    assert standing_rules(tmp_path).endswith("… (cut)")


def test_the_reviewer_is_told_the_rules_are_the_bar():
    from operonx_project.model import Card
    from operonx_project.prompts import reviewer_prompt

    card = Card(id="C1", milestone="m1", title="t", intent="i")
    assert "a change that breaks one is a major finding" in reviewer_prompt(card, "diff", "ok", [])
