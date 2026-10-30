"""P3 gate: integration — merge under the lock, a conflict re-opens the card
with a rebase round, a regression is undone; and the scheduler end to end."""

from __future__ import annotations

import pytest

from operonx_project.checks import Check
from operonx_project.gitops import integration_branch
from operonx_project.model import CardState, MilestoneState
from operonx_project.runner import GitIntegrator
from operonx_project.scheduler import Outcome, Scheduler
from tests.flow_env import PY, RIGHT, Env, card, git, impl

RESOLVED = "def add(a, b):\n    # integration note\n    return a + b\n"


async def test_merge_conflict_leads_to_a_rebase_round(tmp_path):
    env = Env(tmp_path, {"implementer": [impl(RIGHT), impl(RESOLVED)]}, heldout=False)
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "verified"
    # meanwhile the integration branch changed the same lines
    integ = env.git.integration_path("m1")
    (integ / "calc.py").write_text("def add(a, b):\n    # integration note\n    return a - b\n")
    git(integ, "commit", "-qam", "another card")
    res = await GitIntegrator(env.rt).integrate(env.card())
    assert res.status == "conflict" and "calc.py" in res.reason
    assert not env.git.dirty(integ)  # the merge was aborted cleanly

    # what the scheduler does on a conflict: re-open with the reason
    env.store.update_card(
        "m1", "C1", reopens=1, detail={**env.card().detail, "reopen": {"why": "conflict", "reason": res.reason}}
    )
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "verified", out
    rebase = env.coder.of("implementer")[1]
    assert "in conflict" in rebase["prompt"] and "calc.py" in rebase["prompt"]
    assert rebase["session_in"] == env.coder.of("implementer")[0]["session"]
    assert len(env.coder.of("test_author")) == 1  # prepare's work is not redone on a re-open
    res = await GitIntegrator(env.rt).integrate(env.card())
    assert res.status == "merged", res
    assert (integ / "calc.py").read_text() == RESOLVED


async def test_unresolved_markers_are_never_committed(tmp_path):
    env = Env(tmp_path, {"implementer": [impl(RIGHT), {"write": {}}, impl(RESOLVED)]}, heldout=False)
    await env.runner.run(env.card(), env.stage)
    integ = env.git.integration_path("m1")
    (integ / "calc.py").write_text("def add(a, b):\n    # integration note\n    return a - b\n")
    git(integ, "commit", "-qam", "another card")
    res = await GitIntegrator(env.rt).integrate(env.card())
    env.store.update_card("m1", "C1", detail={**env.card().detail, "reopen": {"why": "conflict", "reason": res.reason}})
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "verified"
    assert "still has conflict markers" in env.coder.of("implementer")[2]["prompt"]
    assert "<<<<<<<" not in env.git.git("log", "-p", "-3", cwd=env.wt())


async def test_a_regression_is_undone_and_reported(tmp_path):
    env = Env(tmp_path, {"implementer": [impl(RIGHT)]}, heldout=False)
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "verified"
    integ = env.git.integration_path("m1")
    before = env.git.head(integ)
    # the full checks on the integration branch see something the card's checks did not
    env.rt.checks = env.rt.checks + [
        Check("no-plus", f"{PY} -c \"import sys; sys.exit('+' in open('calc.py').read())\"")
    ]
    res = await GitIntegrator(env.rt).integrate(env.card())
    assert res.status == "regression" and "no-plus" in res.reason
    assert env.git.head(integ) == before


def _marked(env, marker):
    """The card's checks, each also leaving *marker* when it runs."""
    env.rt.checks = [Check(c.name, f"touch {marker} && {c.cmd}", timeout_s=c.timeout_s) for c in env.rt.checks]


async def test_a_card_checked_on_an_unmoved_branch_merges_without_running_the_checks_again(tmp_path):
    env = Env(tmp_path, {"implementer": [impl(RIGHT)]}, heldout=False)
    assert (await env.runner.run(env.card(), env.stage)).status == "verified"
    integ = env.git.integration_path("m1")
    (integ / "docs/brd/board").mkdir(parents=True)
    (integ / "docs/brd/board/m1.md").write_text("# m1\n")  # the engine's board commit moves the branch, not the code
    git(integ, "add", "-A")
    git(integ, "commit", "-qm", "m1: board (C1 verified)")
    _marked(env, tmp_path / "ran")
    res = await GitIntegrator(env.rt).integrate(env.card())
    assert res.status == "merged" and "the card's QA" in res.reason, res
    assert not (tmp_path / "ran").exists()
    assert (integ / "calc.py").read_text() == RIGHT


async def test_a_card_merged_onto_new_code_is_checked_again(tmp_path):
    env = Env(tmp_path, {"implementer": [impl(RIGHT)]}, heldout=False)
    assert (await env.runner.run(env.card(), env.stage)).status == "verified"
    integ = env.git.integration_path("m1")
    (integ / "other.py").write_text("X = 1\n")
    git(integ, "add", "-A")
    git(integ, "commit", "-qm", "another card")
    _marked(env, tmp_path / "ran")
    res = await GitIntegrator(env.rt).integrate(env.card())
    assert res.status == "merged" and "the card's QA" not in res.reason, res
    assert (tmp_path / "ran").exists()


async def test_a_final_check_waits_for_the_milestone_end(tmp_path):
    env = Env(tmp_path, {"implementer": [impl(RIGHT)]}, heldout=False)
    env.rt.checks = env.rt.checks + [Check("long", f"touch {tmp_path / 'long'}", when="final"),
                                     Check("fast", "true", when="card")]  # fmt: skip
    assert (await env.runner.run(env.card(), env.stage)).status == "verified"
    assert not (tmp_path / "long").exists()
    assert [c.name for c in env.rt.card_checks] == ["tests", "fast"]
    assert [c.name for c in env.rt.final_checks] == ["tests", "long"]
    with pytest.raises(ValueError, match="when"):
        Check("x", "true", when="sometimes")


async def test_scheduler_runs_cards_to_merged(tmp_path):
    cards = [
        card("C1"),
        card("C2", title="sub numbers", paths=["calc_sub.py"], deps=["C1"],
             criteria=["WHEN sub(a, b) is called THE SYSTEM SHALL return a - b"]),
    ]  # fmt: skip

    def author(req, i):
        cid = "C1" if i == 0 else "C2"
        body = ("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n" if cid == "C1" else
                "from calc_sub import sub\n\n\ndef test_sub():\n    assert sub(5, 3) == 2\n")  # fmt: skip
        return {"write": {f"tests/acceptance/{cid}/test_{cid.lower()}.py": body}}

    env = Env(tmp_path, {
        "test_author": [author],
        "implementer": [impl(RIGHT), {"write": {"calc_sub.py": "def sub(a, b):\n    return a - b\n"}}],
    }, heldout=False, cards=cards)  # fmt: skip
    env.store.move_milestone("m1", MilestoneState.PLANNING)
    env.store.move_milestone("m1", MilestoneState.AWAITING_APPROVAL)
    env.store.move_milestone("m1", MilestoneState.RUNNING)
    sched = Scheduler(env.store, "m1", env.runner, GitIntegrator(env.rt))
    assert await sched.run() == Outcome.COMPLETE
    assert [c.state for c in env.store.cards("m1")] == [CardState.DONE, CardState.DONE]
    files = env.git.git("ls-tree", "-r", "--name-only", integration_branch("m1"))
    assert "calc_sub.py" in files and "tests/acceptance/C2/test_c2.py" in files
