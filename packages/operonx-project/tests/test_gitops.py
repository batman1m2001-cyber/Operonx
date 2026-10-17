"""gitops on real temporary repositories."""

import subprocess
from pathlib import Path

import pytest

from operonx_project.gitops import GitError, GitOps, card_branch, guard, integration_branch, sha256_file


def sh(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "product"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    sh(r, "config", "user.name", "Owner")
    sh(r, "config", "user.email", "owner@example.com")
    (r / "app.py").write_text("def f():\n    return 1\n")
    (r / "README.md").write_text("product\n")
    sh(r, "add", "-A")
    sh(r, "commit", "-q", "-m", "init")
    return r


@pytest.fixture
def git(repo, tmp_path):
    return GitOps(repo, root=tmp_path / "worktrees")


def test_branch_names_do_not_collide_as_refs():
    assert integration_branch("m1") == "milestone/m1/main"
    assert card_branch("m1", "c1") == "milestone/m1/card/c1"
    with pytest.raises(ValueError):
        card_branch("m1", "../evil")
    with pytest.raises(ValueError):
        integration_branch("a b")


def test_worktrees_live_outside_the_repo_and_are_idempotent(git, repo, tmp_path):
    integ = git.ensure_integration("m1", base="main")
    wt = git.ensure_card("m1", "c1")
    assert wt == (tmp_path / "worktrees" / "product" / "m1" / "c1").resolve()
    assert not str(wt).startswith(str(repo))
    assert git.rev("HEAD", cwd=wt) == git.rev("HEAD", cwd=integ) == git.rev("main")
    assert sh(wt, "branch", "--show-current").strip() == "milestone/m1/card/c1"
    # after a crash: the same call finds the same worktree, files kept
    (wt / "new.py").write_text("x = 1\n")
    assert git.ensure_card("m1", "c1") == wt and (wt / "new.py").exists()


def test_a_worktree_whose_directory_vanished_is_recreated_on_its_branch(git):
    git.ensure_integration("m1", base="main")
    wt = git.ensure_card("m1", "c1")
    (wt / "a.txt").write_text("a\n")
    sha = git.commit(wt, "card c1: a")
    subprocess.run(["rm", "-rf", str(wt)], check=True)
    wt2 = git.ensure_card("m1", "c1")
    assert git.rev("HEAD", cwd=wt2) == sha


def test_commit_returns_none_when_nothing_changed_and_uses_the_repo_identity(git):
    git.ensure_integration("m1", base="main")
    wt = git.ensure_card("m1", "c1")
    assert git.commit(wt, "nothing") is None
    (wt / "app.py").write_text("def f():\n    return 2\n")
    (wt / "new.py").write_text("y = 2\n")
    sha = git.commit(wt, "card c1: change f")
    assert sha and git.dirty(wt) == []
    assert sh(wt, "log", "-1", "--format=%an <%ae>").strip() == "Owner <owner@example.com>"
    assert sorted(git.changed(wt, integration_branch("m1"))) == ["app.py", "new.py"]
    diff = git.diff(wt, integration_branch("m1"))
    assert "return 2" in diff and "new.py" in diff


def test_commit_falls_back_to_the_engine_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # no global identity
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    r = tmp_path / "bare"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    g = GitOps(r, root=tmp_path / "wts")
    (r / "a").write_text("a\n")
    assert g.commit(r, "first")
    assert sh(r, "log", "-1", "--format=%an").strip() == "operonx-project"


def test_merges_are_no_ff_one_at_a_time(git):
    integ = git.ensure_integration("m1", base="main")
    for cid, name in (("c1", "one.py"), ("c2", "two.py")):
        wt = git.ensure_card("m1", cid)
        (wt / name).write_text(f"{cid}\n")
        git.commit(wt, f"card {cid}")
    m1 = git.merge_card("m1", "c1")
    m2 = git.merge_card("m1", "c2")
    assert m1.status == m2.status == "merged"
    assert (integ / "one.py").exists() and (integ / "two.py").exists()
    parents = sh(integ, "log", "-1", "--format=%P").split()
    assert len(parents) == 2  # a merge commit, not a fast-forward
    assert git.merge_card("m1", "c1").status == "empty"
    # main itself is untouched: only the integration branch moved
    assert git.rev("main") != git.rev(integration_branch("m1"))


def test_a_conflict_is_aborted_and_named_then_a_rebase_round_resolves_it(git):
    integ = git.ensure_integration("m1", base="main")
    a = git.ensure_card("m1", "a")
    b = git.ensure_card("m1", "b")
    (a / "app.py").write_text("def f():\n    return 'a'\n")
    (b / "app.py").write_text("def f():\n    return 'b'\n")
    git.commit(a, "a")
    git.commit(b, "b")
    assert git.merge_card("m1", "a").status == "merged"
    tip = git.rev("HEAD", cwd=integ)
    m = git.merge_card("m1", "b")
    assert m.status == "conflict" and m.files == ["app.py"]
    # the integration branch is clean and where it was
    assert git.rev("HEAD", cwd=integ) == tip and git.dirty(integ) == []
    assert not (Path(sh(integ, "rev-parse", "--absolute-git-dir").strip()) / "MERGE_HEAD").exists()
    # the rebase round: the tip comes into the card, markers left for the coder
    u = git.update_card("m1", "b")
    assert u.status == "conflict" and git.conflicted(b) == ["app.py"]
    assert "<<<<<<<" in (b / "app.py").read_text()
    (b / "app.py").write_text("def f():\n    return 'a and b'\n")
    git.commit(b, "b: resolve with a")
    assert git.merge_card("m1", "b").status == "merged"
    assert "a and b" in (integ / "app.py").read_text()


def test_a_half_written_merge_is_aborted_before_the_next(git):
    integ = git.ensure_integration("m1", base="main")
    a = git.ensure_card("m1", "a")
    (a / "app.py").write_text("def f():\n    return 'a'\n")
    git.commit(a, "a")
    (integ / "app.py").write_text("def f():\n    return 'i'\n")
    sh(integ, "commit", "-qam", "integration edit")
    # a crash in the middle of a merge leaves MERGE_HEAD and markers
    subprocess.run(["git", "merge", card_branch("m1", "a")], cwd=integ, capture_output=True)
    assert git.conflicted(integ) == ["app.py"]
    m = git.merge_card("m1", "a")
    assert m.status == "conflict"  # redone cleanly, not stuck on the old one
    assert git.dirty(integ) == []


def test_merge_refuses_a_dirty_integration_worktree(git):
    integ = git.ensure_integration("m1", base="main")
    a = git.ensure_card("m1", "a")
    (a / "x").write_text("x\n")
    git.commit(a, "a")
    (integ / "stray").write_text("?\n")
    with pytest.raises(GitError, match="uncommitted"):
        git.merge_card("m1", "a")


def test_remove_card_drops_the_worktree_and_optionally_the_branch(git):
    git.ensure_integration("m1", base="main")
    wt = git.ensure_card("m1", "c1")
    git.remove_card("m1", "c1")
    assert not wt.exists() and git.has_branch(card_branch("m1", "c1"))
    git.remove_card("m1", "c1", delete_branch=True)
    assert not git.has_branch(card_branch("m1", "c1"))


# ── guards ──


def test_guard_refuses_protected_and_outside_paths_and_warns_on_shared(tmp_path):
    found = guard(
        tmp_path,
        ["src/a/x.py", "tests/acceptance/test_c1.py", "src/b/y.py", "pyproject.toml"],
        paths=["src/a/**"],
        shared=["pyproject.toml"],
        protected=["tests/acceptance/**"],
    )
    by = {(f.path, f.rule, f.severity) for f in found}
    assert by == {
        ("tests/acceptance/test_c1.py", "protected", "blocker"),
        ("src/b/y.py", "outside_paths", "blocker"),
        ("pyproject.toml", "outside_paths", "warning"),
    }
    assert guard(tmp_path, ["anything.py"]) == []  # no paths declared: anywhere


def test_guard_catches_a_private_source_under_any_name(tmp_path):
    src = tmp_path / "brd.docx"
    src.write_bytes(b"PK\x03\x04 private bytes")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "renamed.bin").write_bytes(src.read_bytes())
    (tmp_path / "ok.txt").write_text("fine\n")
    found = guard(tmp_path, ["docs/renamed.bin", "ok.txt"], private={sha256_file(src): "brd.docx"})
    assert [(f.path, f.rule) for f in found] == [("docs/renamed.bin", "private_source")]
