"""Linked milestones across repos (plan D17): a milestone that needs another
project's waits in ``waiting_dependency`` until that one is delivered."""

from __future__ import annotations

from pathlib import Path

import pytest

from operonx_project.api import act
from operonx_project.model import MilestoneState as M
from operonx_project.model import Policy
from operonx_project.project import Project, create_milestone
from tests.flow_env import make_repo
from tests.life_env import Life, base_script


@pytest.fixture(autouse=True)
def _index(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERONX_PROJECTS_INDEX", str(tmp_path / "projects.json"))


def _other(tmp_path: Path, name: str = "studio") -> Project:
    repo = make_repo(tmp_path / "other")
    repo = repo.rename(repo.parent / name)
    other = Project.open(repo, worktrees=tmp_path / "wt-other")  # opening it puts it in the index
    create_milestone(other, "the API", goal="an endpoint", mid="m3")
    return other


def _deliver(p: Project, mid: str) -> None:
    path = [M.DISCOVERING, M.REQUIREMENTS, M.PLANNING, M.RUNNING, M.VERIFYING, M.DONE]
    now = p.store.get_milestone(mid).state
    for s in path[path.index(now) + 1 if now in path else 0 :]:
        p.store.move_milestone(mid, s, "test")


def test_a_milestone_waits_for_another_projects_and_starts_when_it_is_delivered(tmp_path):
    other = _other(tmp_path)
    repo = make_repo(tmp_path)
    mine = Project.open(repo, worktrees=tmp_path / "worktrees")
    create_milestone(mine, "calc", goal="calc.add adds", policy=Policy.w1(approve_plan="auto", heldout=False),
                     mid="m1", needs=["studio#m3"])  # fmt: skip
    life = Life(tmp_path, base_script(), repo=repo)
    life.command("start")
    assert life.serve() == M.WAITING_DEPENDENCY
    assert life.store.get_milestone("m1").reason == "waits for studio#m3 (created)"
    other.store.move_milestone("m3", M.DISCOVERING, "test")
    assert life.life.check_dependencies() == []
    assert life.store.get_milestone("m1").reason == "waits for studio#m3 (discovering)"  # the Board says where it is
    _deliver(other, "m3")
    assert life.life.check_dependencies() == ["m1"]
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason


def test_a_need_on_this_project_and_one_not_found(tmp_path):
    repo = make_repo(tmp_path)
    mine = Project.open(repo, worktrees=tmp_path / "worktrees")
    create_milestone(mine, "first", goal="g", mid="m1")
    create_milestone(mine, "second", goal="g", mid="m2", needs=["m1", "nowhere#m9"])
    life = Life(tmp_path, base_script(), repo=repo)
    assert life.life.unmet_needs("m2") == ["m1 (created)", "nowhere#m9 (not found on this machine)"]


def test_a_malformed_need_is_refused_at_intake(tmp_path):
    p = Project.open(make_repo(tmp_path), worktrees=tmp_path / "worktrees")
    got = act(p, {"op": "start", "title": "x", "goal": "y", "needs": ["studio m3"]})
    assert not got["ok"] and "a need is <project>#m<n> or m<n>" in got["message"]
