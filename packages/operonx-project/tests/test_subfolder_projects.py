"""Two projects in one repo (plan D11): each folder with its own BRD and store."""

from __future__ import annotations

import subprocess

from operonx_project.project import Project


def _git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _mono(tmp_path):
    repo = tmp_path / "mono"
    for sub in ("alpha", "beta"):
        (repo / sub).mkdir(parents=True)
        (repo / sub / "operonx.toml").write_text(f'[project]\nname = "{sub}"\n')
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return repo


def test_each_subfolder_is_its_own_project(tmp_path):
    repo = _mono(tmp_path)
    a, b = Project.open(repo / "alpha"), Project.open(repo / "beta")
    assert a.repo == repo / "alpha" and a.state == repo / "alpha" / ".operonx" / "project"
    assert (a.git.sub, b.git.sub, a.git.project) == ("alpha", "beta", "alpha")
    assert a.state != b.state and (a.state / "project.db").is_file()
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert "/alpha/.operonx/project/" in exclude and "/beta/.operonx/runs/" in exclude


def test_a_top_level_project_keeps_its_names(tmp_path):
    p = Project.open(_mono(tmp_path))
    assert (p.git.sub, p.repo) == ("", tmp_path / "mono")
    assert "/.operonx/project/" in (tmp_path / "mono" / ".git" / "info" / "exclude").read_text().splitlines()
