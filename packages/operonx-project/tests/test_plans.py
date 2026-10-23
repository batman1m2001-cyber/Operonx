"""Plan docs carry their status (PROJECT_MODE_PLAN D1): the block, what is wrong in one, and brd check."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from operonx_project.plans import PlanError, find, front_matter, parse

BLOCK = """---
title: CSV export
status: in_progress
phases:
  - {id: E1, title: The rows, status: done}
  - {id: E2, title: The service, status: in_progress}
  - {id: E3, title: Docs}
rows: [R-EX-01]
updated: 2026-10-23
---

# CSV export
"""


def _doc(repo: Path, rel: str, text: str) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, encoding="utf-8")


def test_a_plan_says_where_it_stands():
    plan = parse("docs/EXPORT_PLAN.md", front_matter(BLOCK), BLOCK)
    assert (plan.title, plan.status, plan.done, len(plan.phases)) == ("CSV export", "in_progress", 1, 3)
    assert plan.current.id == "E2" and plan.phases[2].status == "open"  # a phase with no status is open
    assert plan.rows == ["R-EX-01"] and plan.updated == "2026-10-23"  # YAML reads the date: kept as text
    assert plan.as_dict()["current"] == "E2"


@pytest.mark.parametrize(
    "block, says",
    [
        ("status: started\n", "status 'started'"),
        ("status: plan\nphases: [{title: x}]\n", "phase 1 has no id"),
        ("status: plan\nphases: [{id: A, status: finished}]\n", "phase A: status 'finished'"),
        ("status: plan\nphases: [{id: A}, {id: A}]\n", "listed twice"),
        ("status: done\nphases: [{id: A, status: done}, {id: B}]\n", "phase B is still open"),
        ("status: plan\nrows: R-1\n", "rows is not a list"),
    ],
)
def test_a_wrong_block_says_what_is_wrong(block, says):
    with pytest.raises(PlanError, match=says):
        parse("docs/X_PLAN.md", front_matter(f"---\n{block}---\n"))


def test_find_reads_plans_skips_the_rest_and_warns_about_plans_without_a_block(tmp_path):
    _doc(tmp_path, "docs/EXPORT_PLAN.md", BLOCK)
    _doc(tmp_path, "docs/design/OLD_PLAN.md", "# An old plan, no block\n")
    _doc(tmp_path, "docs/notes.md", "---\nlayout: page\n---\n# Another tool's front matter\n")
    _doc(tmp_path, "docs/BROKEN_PLAN.md", "---\nstatus: plan\nphases: [\n")
    _doc(tmp_path, "docs/brd/board/m1.md", "---\nstatus: whatever the engine writes\n---\n")
    long = BLOCK.replace("phases:\n", "phases:\n" + "".join(f"  - {{id: X{i}, status: done}}\n" for i in range(400)))
    _doc(tmp_path, "docs/LONG_PLAN.md", long)  # a block longer than the head read first
    plans, errors, warnings = find(tmp_path)
    assert [p.path for p in plans] == ["docs/EXPORT_PLAN.md", "docs/LONG_PLAN.md"]
    assert errors == ["docs/BROKEN_PLAN.md: a block opened with --- is never closed"]
    assert warnings == ["docs/design/OLD_PLAN.md: a plan with no status block (PROJECT_MODE_PLAN D1)"]
    assert find(tmp_path / "nothing") == ([], [], [])


def test_brd_check_checks_the_plans_but_a_milestones_final_pass_does_not(tmp_path):
    from operonx_project.brd.checker import check
    from operonx_project.brd.folder import init_brd

    init_brd(tmp_path, project="calc")
    brd = tmp_path / "docs/brd"
    before, _ = check(brd)
    _doc(tmp_path, "docs/EXPORT_PLAN.md", BLOCK)  # R-EX-01 is in no checklist
    _doc(tmp_path, "docs/OLD_PLAN.md", "# no block\n")
    errors, report = check(brd)
    assert errors == before + ["plan docs/EXPORT_PLAN.md: row R-EX-01 is not in the checklist"]
    assert "plans: 1 with a status block, 1 without" in report
    assert "warning: docs/OLD_PLAN.md: a plan with no status block (PROJECT_MODE_PLAN D1)" in report
    assert check(brd, plans=False)[0] == before


def test_the_cli_lists_the_plans_and_fails_on_a_wrong_block(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}

    def cli(*args):
        return subprocess.run([sys.executable, "-m", "operonx_project.cli", "plans", "--repo", str(tmp_path), *args],
                              capture_output=True, text=True, env=env)  # fmt: skip

    _doc(tmp_path, "docs/EXPORT_PLAN.md", BLOCK)
    r = cli()
    assert r.returncode == 0, r.stderr
    assert "in_progress  1/3  CSV export · now E2 The service  (docs/EXPORT_PLAN.md)" in r.stdout
    assert json.loads(cli("--json").stdout)["plans"][0]["current"] == "E2"
    _doc(tmp_path, "docs/BAD_PLAN.md", "---\nstatus: started\n---\n")
    r = cli()
    assert r.returncode == 1 and "ERROR docs/BAD_PLAN.md: status 'started'" in r.stdout
