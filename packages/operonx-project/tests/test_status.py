"""One status for the whole project (PROJECT_MODE_PLAN D2): what it reads, in what order, and that it only reads."""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

from operonx_project.cli import main
from operonx_project.status import status, text

MATRIX = """# Requirements traceability matrix

## 1. Numbered

| ID | BRD § | Requirement (short) | Tests | Status | Evidence, gaps |
|---|---|---|---|---|---|
| CALC-FR-01 | §1 | Add two numbers | `tests/test_calc.py::test_add` | implemented_verified | done |
| CALC-FR-02 | §1 | Divide, with a \\| in it | — | partially_implemented | no zero check yet |
| CALC-FR-03 | §2 | A slide rule | — | out_of_scope_demo | — |

## 2. Owner requests

| ID | Features | Status | Tests / evidence | Notes |
|---|---|---|---|---|
| OWN-01 | FEAT-01 | implemented_unverified | `tests/test_calc.py::test_csv` | needs a browser run |
"""

QUESTIONS = """# Open questions

| ID | Type | Topic | State |
|---|---|---|---|
| Q-01 | ambiguity | Rounding of a division | open — business |
| Q-02 | gap | Locale of numbers | resolved (ADR-0001) |
"""


BOARD = """# Board

### C1 · a stray fix · needs you
files: src/calc.py

Round half up or half even?

## EX · CSV export · open
plan: docs/EXPORT_PLAN.md · opened: 2026-11-01

### C2 · Rows · done
done: tests/test_calc.py::test_add

### C3 · The service · doing
after: C2
files: src/export/routes.py

### C4 · Docs · todo

## OL · Old · closed
plan: docs/OLD_PLAN.md · opened: 2026-09-01 · closed: 2026-09-20

### C5 · An old step that was doing when it closed · done
"""


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "calc"
    req = repo / "docs/brd/requirements"
    req.mkdir(parents=True)
    (req / "requirements_traceability.md").write_text(MATRIX, encoding="utf-8")
    (req / "open_questions.md").write_text(QUESTIONS, encoding="utf-8")
    (repo / "docs/brd/board.md").write_text(BOARD, encoding="utf-8")
    (repo / "docs/EXPORT_PLAN.md").write_text("# CSV export\n\nWhy and how; where it stands is its cycle.\n")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "first: the checklist"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "E1: rows"], check=True)
    return repo


def test_the_status_reads_checklist_board_cycles_and_changes(tmp_path):
    repo = _repo(tmp_path)
    doc = status(repo)
    c = doc["checklist"]
    assert (c["total"], c["verified"]) == (4, 1)
    assert [(r["id"], r["what"], r["note"]) for r in c["open"]] == [
        ("CALC-FR-02", "Divide, with a | in it", "no zero check yet"),  # an escaped | stays in its cell
        ("OWN-01", "", "needs a browser run"),  # Notes, not "Tests / evidence"
    ]
    assert [(y["id"], y["state"], y["done"], len(y["cards"])) for y in doc["board"]["cycles"]] == [
        ("EX", "open", 1, 3), ("OL", "closed", 1, 1)]
    assert [(w["card"], w["sentence"]) for w in doc["working_on"]] == [("C3", "doing · web · EX 1/3")]
    # a BRD question reaches "needs you" only through a card
    assert doc["needs_you"] == [{"source": "board", "id": "C1", "kind": "card", "text": "Round half up or half even?",
                                 "state": "open", "cycle": ""}]  # fmt: skip
    assert [ch["subject"] for ch in doc["changes"]] == ["E1: rows", "first: the checklist"]
    assert not (repo / ".operonx").exists()  # it only reads

    lines = text(doc)
    assert lines.startswith("Checklist: 1 of 4 verified, 2 open\n")
    assert "  cycle EX · CSV export · 1/3 done (docs/EXPORT_PLAN.md)\n" in lines
    assert "  closed cycles: OL\n" in lines and "C5" not in lines and "Plan" not in lines


def test_a_bare_folder_has_a_status_too(tmp_path):
    doc = status(tmp_path)
    assert doc["checklist"]["total"] == 0 and doc["board"] is None and doc["changes"] == []
    assert text(doc).startswith("Checklist: none")


def _cli(*args: str) -> tuple:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(list(args))
    return code, buf.getvalue()


def test_the_api_op_and_the_cli_only_read(tmp_path):
    repo = _repo(tmp_path)
    code, out = _cli("api", "--repo", str(repo), '{"op": "status", "changes": 1}')
    got = json.loads(out)
    assert code == 0 and got["ok"] and [ch["subject"] for ch in got["project"]["changes"]] == ["E1: rows"]
    assert got["text"] == text(got["project"])
    code, out = _cli("status", "--repo", str(repo), "--json")
    assert code == 0 and json.loads(out)["checklist"]["verified"] == 1
    assert _cli("status", "--repo", str(repo))[1] == text(status(repo))
    assert not (repo / ".operonx").exists()


# ── the standard (PROJECT_STANDARD_PLAN D1) ─────────────────────────────────

GOOD_MATRIX = """# Requirements traceability matrix

## 0. Summary

| Set | Rows | implemented_verified | implemented_unverified | partially_implemented | planned | not_implemented | blocked | out_of_scope_demo | not_applicable |
|---|---|---|---|---|---|---|---|---|---|
| {name} | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## 1. Rows

| ID | Requirement | Tests | Status |
|---|---|---|---|
| CALC-01 | Add two numbers | `tests/test_calc.py::test_add` | implemented_verified |
"""


def _standard_repo(tmp_path: Path, summary_name: str = "CALC") -> Path:
    repo = tmp_path / "std"
    req = repo / "docs/brd/requirements"
    req.mkdir(parents=True)
    (req / "requirements.md").write_text("# Requirements\n\n### CALC-01 Add two numbers\n", encoding="utf-8")
    (req / "requirements_traceability.md").write_text(GOOD_MATRIX.format(name=summary_name), encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests/test_calc.py").write_text("def test_add():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    return repo


def _std(doc):
    return {x["key"]: x["ok"] for x in doc["standard"]}


def test_a_project_up_to_the_standard_meets_both(tmp_path):
    from operonx_project.brd.checker import check

    repo = _standard_repo(tmp_path)
    assert check(repo / "docs/brd")[0] == []
    doc = status(repo)
    assert _std(doc) == {"brd": True, "check": True}
    assert all(x["fix"] == "" for x in doc["standard"])
    assert "Standard: 2 of 2, all met\n" in text(doc)


def test_each_missing_item_says_where_it_stands_and_what_fixes_it(tmp_path):
    doc = status(_repo(tmp_path))
    assert _std(doc) == {"brd": True, "check": False}
    lines = text(doc)
    assert "Standard: 1 of 2\n" in lines and "  missing check: " in lines

    bare = status(tmp_path / "nothing")
    assert _std(bare) == {"brd": False, "check": False}
    assert "operonx-project adopt" in bare["standard"][0]["fix"]


def test_a_summary_with_sets_of_its_own_is_named_in_brd_toml(tmp_path):
    # OneWealth's case: the summary names its sets in words, the checker groups by prefix
    repo = _standard_repo(tmp_path, summary_name="The calculator (CALC)")
    check_item = next(x for x in status(repo)["standard"] if x["key"] == "check")
    assert not check_item["ok"] and "summary row" in check_item["line"] and "brd.toml [[groups]]" in check_item["fix"]
    (repo / "docs/brd/brd.toml").write_text('[[groups]]\nname = "The calculator (CALC)"\npattern = "CALC-"\n',
                                            encoding="utf-8")  # fmt: skip
    # the check re-runs because a file it reads changed
    assert _std(status(repo))["check"] is True
