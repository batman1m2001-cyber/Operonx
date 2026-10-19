"""Ship a delivered milestone on the owner's tap, and merge it where granted (plan D15)."""

from __future__ import annotations

import json
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from operonx_project.api import act
from operonx_project.model import MilestoneState as M
from operonx_project.model import Policy
from operonx_project.ship import remote_kind
from tests.flow_env import git
from tests.life_env import Life, base_script

GH = """#!/bin/sh
# a stand-in gh: records each call, answers like gh
printf '%s\\n' "$*" >> "{log}"
case "$1 $2" in
  "pr create") echo "https://github.com/o/r/pull/7" ;;
  "pr view") cat "{view}" ;;
  "pr merge") echo "merged" ;;
esac
"""


def _done(tmp_path: Path) -> Life:
    life = Life(tmp_path, base_script(), policy=Policy.w1(approve_plan="auto", heldout=False))
    life.command("start")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    return life


def _remote(life: Life, tmp_path: Path, url: str = "") -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(life.repo, "remote", "add", "origin", url or str(bare))
    if url:  # a GitHub or GitLab url to read, pushes going to the local bare repo
        git(life.repo, "remote", "set-url", "--push", "origin", str(bare))
    return bare


def _gh(tmp_path: Path, monkeypatch, view: dict) -> Path:
    log, page = tmp_path / "gh.log", tmp_path / "gh.view"
    page.write_text(json.dumps(view))
    gh = tmp_path / "gh"
    gh.write_text(GH.format(log=log, view=page))
    gh.chmod(0o755)
    monkeypatch.setenv("OPERONX_GH", str(gh))
    return log


def test_remote_kinds():
    assert remote_kind("git@github.com:o/r.git") == {"kind": "github", "host": "github.com", "path": "o/r"}
    assert remote_kind("https://github.com/o/r") == {"kind": "github", "host": "github.com", "path": "o/r"}
    assert remote_kind("https://gitlab.team.vn/ai/callbot.git")["path"] == "ai/callbot"
    assert remote_kind("/srv/git/r.git")["kind"] == "other"


def test_only_a_delivered_milestone_ships_and_only_with_a_remote(tmp_path):
    life = Life(tmp_path, base_script())
    assert "only a delivered milestone ships" in act(life.project, {"op": "ship", "milestone": "m1"})["message"]
    life = _done(tmp_path / "b")
    assert "no remote 'origin'" in act(life.project, {"op": "ship", "milestone": "m1"})["message"]


def test_ship_pushes_the_branch_and_opens_the_pr_with_gh(tmp_path, monkeypatch):
    life = _done(tmp_path)
    bare = _remote(life, tmp_path, "git@github.com:o/r.git")
    log = _gh(tmp_path, monkeypatch, {})
    got = act(life.project, {"op": "ship", "milestone": "m1"}, actor="owner")
    assert got["ok"] and got["pr"] == "https://github.com/o/r/pull/7", got
    branch = "milestone/m1/main"
    assert subprocess.run(["git", "branch", "--list", branch], cwd=bare, capture_output=True, text=True).stdout.strip()
    call = log.read_text()
    assert f"pr create --repo o/r --base main --head {branch} --title m1: calc" in call
    assert life.store.ms_get("m1", "shipped")["pr"] == got["pr"]
    # merge is the owner's grant: refused until settings.toml says so
    assert "not granted" in act(life.project, {"op": "merge", "milestone": "m1"})["message"]


def test_ship_to_gitlab_is_the_prefilled_merge_request_link(tmp_path):
    life = _done(tmp_path)
    _remote(life, tmp_path, "git@gitlab.team.vn:ai/calc.git")
    got = act(life.project, {"op": "ship", "milestone": "m1"})
    assert got["ok"] and got["pr"].startswith("https://gitlab.team.vn/ai/calc/-/merge_requests/new?"), got
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(got["pr"]).query)
    assert q["merge_request[source_branch]"] == ["milestone/m1/main"] and q["merge_request[target_branch]"] == ["main"]


@pytest.mark.parametrize(
    "rollup, says",
    [
        ([{"name": "tests", "status": "IN_PROGRESS", "conclusion": ""}], "checks still run: tests"),
        ([{"name": "tests", "status": "COMPLETED", "conclusion": "FAILURE"}], "checks fail: tests (failure)"),
        ([{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}], None),
    ],
)
def test_merge_reads_the_checks_first(tmp_path, monkeypatch, rollup, says):
    life = _done(tmp_path)
    _remote(life, tmp_path, "https://github.com/o/r.git")
    log = _gh(tmp_path, monkeypatch, {"state": "OPEN", "mergeable": "MERGEABLE", "statusCheckRollup": rollup})
    assert act(life.project, {"op": "ship", "milestone": "m1"})["ok"]
    (life.project.state / "settings.toml").write_text("merge = true\n")
    got = act(life.project, {"op": "merge", "milestone": "m1"})
    if says:
        assert not got["ok"] and says in got["message"]
        assert "pr merge" not in log.read_text()
    else:
        assert got["ok"] and "merged into main" in got["message"]
        assert not got["synced"] and "Pull main here" in got["message"]  # origin's fetch url is not reachable
        assert "pr merge https://github.com/o/r/pull/7 --merge" in log.read_text()
        assert "already merged" in act(life.project, {"op": "merge", "milestone": "m1"})["message"]


def test_after_a_merge_the_checked_out_base_is_fast_forwarded(tmp_path):
    from operonx_project.ship import _sync_base

    life = _done(tmp_path)
    bare = _remote(life, tmp_path)
    git(life.repo, "push", "-q", "origin", "milestone/m1/main:main")  # the PR merged on the remote
    assert _sync_base(life.project, "main")
    assert git(life.repo, "rev-parse", "main") == git(life.repo, "rev-parse", "milestone/m1/main")
    (life.repo / "calc.py").write_text("changed\n")  # a tracked change: never moved under it
    assert not _sync_base(life.project, "main")
    assert bare.exists()
