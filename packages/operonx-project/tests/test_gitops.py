"""gitops on real temporary repositories: the project's folder, its name, its commit identity."""

import subprocess

import pytest

from operonx_project.gitops import GitError, GitOps


def sh(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_a_bad_project_name_and_a_missing_ref_are_refused(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    with pytest.raises(ValueError):
        GitOps(tmp_path, project="../evil")
    with pytest.raises(GitError):
        GitOps(tmp_path).rev("nope")


def test_the_repo_identity_else_the_engines(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # no global identity
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    r = tmp_path / "bare"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    g = GitOps(r)
    assert g._commit_env(r)["GIT_AUTHOR_NAME"] == "operonx-project"
    sh(r, "config", "user.name", "Owner")
    sh(r, "config", "user.email", "owner@example.com")
    assert g._commit_env(r) == {}
