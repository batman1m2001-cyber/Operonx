"""Two projects in one repo (plan D11): each folder with its own BRD, store
and branches; a card of one cannot touch the other's files."""

from __future__ import annotations

import subprocess

from operonx_project.gitops import guard
from operonx_project.project import Project


def _git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _mono(tmp_path):
    repo = tmp_path / "mono"
    for sub in ("alpha", "beta"):
        (repo / sub).mkdir(parents=True)
        (repo / sub / "operonx.toml").write_text(f'[project]\nname = "{sub}"\n')
        (repo / sub / "calc.py").write_text("x = 1\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return repo


def test_each_subfolder_is_its_own_project(tmp_path):
    repo = _mono(tmp_path)
    a = Project.open(repo / "alpha", worktrees=tmp_path / "wt")
    b = Project.open(repo / "beta", worktrees=tmp_path / "wt")
    assert a.repo == repo / "alpha" and a.state == repo / "alpha" / ".operonx" / "project"
    assert (a.git.sub, a.git.ns, b.git.ns) == ("alpha", "alpha", "beta")
    assert a.git.integration_branch("m1") == "milestone/alpha/m1/main" != b.git.integration_branch("m1")
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert "/alpha/.operonx/project/" in exclude and "/beta/.operonx/runs/" in exclude

    # a card's worktree is the whole repo; the engine works in the project's folder inside it
    integ_a, integ_b = a.git.ensure_integration("m1"), b.git.ensure_integration("m1")
    assert integ_a.name == "alpha" and (integ_a / "calc.py").is_file() and integ_a != integ_b
    wt = a.git.ensure_card("m1", "C1")
    assert wt.name == "alpha" and _git(wt, "branch", "--show-current").strip() == "milestone/alpha/m1/card/C1"

    # paths are the project's; the other project's files are outside it, and refused
    (wt / "calc.py").write_text("x = 2\n")
    (wt.parent / "beta" / "calc.py").write_text("x = 3\n")
    (wt / "new.py").write_text("")
    dirty = sorted(a.git.dirty(wt))
    assert dirty == ["../beta/calc.py", "calc.py", "new.py"]
    found = guard(wt, dirty, paths=[])  # no paths declared: anywhere inside the project
    assert [(f.path, f.rule) for f in found] == [("../beta/calc.py", "outside_project")]
    a.git.revert(wt, ["../beta/calc.py"])
    assert (wt.parent / "beta" / "calc.py").read_text() == "x = 1\n"

    sha = a.git.commit(wt, "C1")
    assert sha and a.git.changed(wt, a.git.integration_branch("m1")) == ["calc.py", "new.py"]
    assert a.git.merge_card("m1", "C1").status == "merged"
    assert (integ_a / "calc.py").read_text() == "x = 2\n" and (integ_b / "calc.py").read_text() == "x = 1\n"


def test_a_top_level_project_keeps_its_names(tmp_path):
    repo = _mono(tmp_path)
    p = Project.open(repo, worktrees=tmp_path / "wt")
    assert (p.git.sub, p.git.ns, p.repo) == ("", "", repo)
    assert p.git.integration_branch("m1") == "milestone/m1/main"
    assert p.git.ensure_integration("m1") == p.git.integration_path("m1")
