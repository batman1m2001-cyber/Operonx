"""The read commands (``milestones``, ``show``, ``status``) say what the store says; ``new`` makes a project."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from operonx_project.board import column_of
from operonx_project.cli import main
from operonx_project.model import Card, CardState, Milestone, MilestoneState
from operonx_project.project import Project


def run(*argv) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main([str(a) for a in argv])
    assert code == 0, buf.getvalue()
    return buf.getvalue()


def test_new_then_the_read_commands_match_the_store(tmp_path):
    repo = tmp_path / "fresh"
    out = run("new", repo, "--name", "fresh")
    assert "docs/brd/" in out and (repo / "docs" / "brd" / "README.md").exists()
    assert "no milestone yet" in run("milestones", "--repo", repo)

    store = Project.open(repo).store
    store.add_milestone(Milestone("m1", str(repo), "first", state=MilestoneState.RUNNING))
    store.add_cards([Card("C1", "m1", "add", state=CardState.DONE), Card("C2", "m1", "double", deps=["C1"])])
    cards = store.cards("m1")
    [st] = json.loads(run("milestones", "--repo", repo, "--json"))
    assert st["state"] == "running" and st["cards"] == 2
    assert st["columns"]["done"] == sum(column_of(c) == "done" for c in cards) == 1
    shown = json.loads(run("show", "m1", "--repo", repo, "--json"))
    assert [(c["id"], c["state"]) for c in shown["cards"]] == [("C1", "done"), ("C2", "pending")]
    assert "C1     done" in run("show", "m1", "--repo", repo)
    assert json.loads(run("status", "--repo", repo, "--json"))["plans"][0]["milestone"] == "m1"
