"""``adopt`` (plan D12): an existing project's BRD and baseline board.

The session's answer is recorded here; what is tested is the engine's part:
every cited test is run, and only a passing one verifies a row.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

from operonx_project.adopt import adopt, run_tests
from operonx_project.brd.board import read_board
from operonx_project.brd.checker import check
from operonx_project.brd.records import matrix_status
from operonx_project.project import Project

CALC = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a + b  # wrong\n"
TESTS = (
    "from calc import add, mul\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
    "class TestMul:\n    def test_mul(self):\n        assert mul(2, 3) == 6\n"
)


def _repo(tmp_path):
    repo = tmp_path / "calc"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(CALC)
    (repo / "tests" / "test_calc.py").write_text(TESTS)
    (repo / "README.md").write_text("# calc\n\nAdds and multiplies. Division is on the backlog.\n")
    (repo / "BACKLOG.md").write_text("- division\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"], cwd=repo,
                   check=True)  # fmt: skip
    return repo


ANSWER = {
    "summary": "A calculator.",
    "requirements": [
        {"id": "CALC-REQ-MATH-01", "type": "functional", "quote": "Adds", "location": "README.md", "state": "built",
         "code": ["calc.py"], "tests": ["tests/test_calc.py::test_add"]},
        {"id": "CALC-REQ-MATH-02", "type": "functional", "quote": "multiplies", "location": "README.md",
         "state": "built", "code": ["calc.py"], "tests": ["tests/test_calc.py::TestMul::test_mul"]},
        {"id": "CALC-REQ-MATH-03", "type": "functional", "quote": "Division", "location": "BACKLOG.md",
         "state": "open", "planned": True, "gap": "no divide()"},
        {"id": "CALC-REQ-MATH-04", "type": "functional", "quote": "Adds", "location": "README.md", "state": "built",
         "tests": ["tests/test_calc.py::test_invented"]},
    ],
    "features": [{"title": "arithmetic", "rows": ["CALC-REQ-MATH-01", "CALC-REQ-MATH-02", "NOT-A-ROW-01"],
                  "paths": ["calc.py"]}],
}  # fmt: skip


def test_run_tests_reads_each_node(tmp_path):
    repo = _repo(tmp_path)
    got = run_tests(repo, ["tests/test_calc.py::test_add", "tests/test_calc.py::TestMul::test_mul",
                           "tests/test_calc.py::test_invented", "not a node"], python=[sys.executable])  # fmt: skip
    assert got == {"tests/test_calc.py::test_add": True, "tests/test_calc.py::TestMul::test_mul": False,
                   "tests/test_calc.py::test_invented": False}  # fmt: skip


def test_adopt_verifies_only_what_passed_and_draws_the_baseline(tmp_path):
    repo = _repo(tmp_path)
    project = Project.open(repo, worktrees=tmp_path / "wt")
    report = asyncio.run(adopt(project, coder=None, prefix="CALC", answer=ANSWER, python=[sys.executable]))
    brd = repo / "docs" / "brd"
    assert matrix_status(brd) == {
        "CALC-REQ-MATH-01": "implemented_verified",
        "CALC-REQ-MATH-02": "implemented_unverified",  # its test fails
        "CALC-REQ-MATH-03": "planned",
        "CALC-REQ-MATH-04": "implemented_unverified",
    }  # its test does not exist
    assert report["status"]["implemented_verified"] == 1
    (bf,) = read_board(brd)
    assert (bf.milestone.id, bf.milestone.state.value) == ("m0", "done")
    assert [(c.id, c.state.value, c.rows) for c in bf.cards] == [
        ("C1", "done", ["CALC-REQ-MATH-01", "CALC-REQ-MATH-02"])
    ]  # the unknown row is dropped
    errors, _ = check(brd)
    assert errors == [], errors
    assert "adopted (`operonx-project adopt`): 4 requirements" in (brd / "CHANGELOG.md").read_text()
    assert project.store.get_milestone("m0").state.value == "done"

    # adopted again: the baseline is kept, no second m0
    asyncio.run(adopt(project, coder=None, prefix="CALC", answer=ANSWER, python=[sys.executable]))
    assert [m.id for m in project.store.milestones()] == ["m0"]
