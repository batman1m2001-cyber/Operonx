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


def _plan(status: str, phases: str, updated: str, title: str) -> str:
    return f"---\ntitle: {title}\nstatus: {status}\nphases:\n{phases}updated: {updated}\n---\n\n# {title}\n"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "calc"
    req = repo / "docs/brd/requirements"
    req.mkdir(parents=True)
    (req / "requirements_traceability.md").write_text(MATRIX, encoding="utf-8")
    (req / "open_questions.md").write_text(QUESTIONS, encoding="utf-8")
    docs = repo / "docs"
    (docs / "EXPORT_PLAN.md").write_text(_plan("in_progress", "  - {id: E1, title: Rows, status: done}\n"
                                               "  - {id: E2, title: The service, status: in_progress}\n"
                                               "  - {id: E3, title: Docs}\n", "2026-10-20", "CSV export"))  # fmt: skip
    (docs / "OLD_PLAN.md").write_text(_plan("done", "  - {id: A, status: done}\n", "2026-09-01", "Old"))
    (docs / "NEW_PLAN.md").write_text(_plan("done", "  - {id: A, status: done}\n", "2026-10-21", "Newer"))
    (docs / "NEXT_PLAN.md").write_text(_plan("in_progress", "  - {id: N1, title: First}\n", "2026-10-22", "Next"))
    (docs / "IDEA_PLAN.md").write_text(_plan("plan", "", "2026-10-22", "An idea"))
    (docs / "LEGACY_PLAN.md").write_text("# A plan from before the block\n")
    (repo / "docs/brd/practices.toml").write_text('[tdd]\nmode = "strict"\n', encoding="utf-8")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "first: the checklist"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "E1: rows"], check=True)
    return repo


def test_the_status_reads_checklist_plans_questions_changes_and_setup(tmp_path):
    repo = _repo(tmp_path)
    doc = status(repo)
    c = doc["checklist"]
    assert (c["total"], c["verified"]) == (4, 1)
    assert [(r["id"], r["what"], r["note"]) for r in c["open"]] == [
        ("CALC-FR-02", "Divide, with a | in it", "no zero check yet"),  # an escaped | stays in its cell
        ("OWN-01", "", "needs a browser run"),  # Notes, not "Tests / evidence"
    ]
    assert [p["title"] for p in doc["plans"]] == ["CSV export", "Next", "An idea", "Newer", "Old"]
    assert doc["unmarked"] == ["docs/LEGACY_PLAN.md"]
    assert [w["sentence"] for w in doc["working_on"]] == [
        "E2, phase 2 of 3: The service",
        "next: N1, phase 1 of 1: First",
    ]
    assert doc["needs_you"] == [{"source": "brd", "id": "Q-01", "kind": "ambiguity",
                                 "text": "Rounding of a division", "state": "open — business"}]  # fmt: skip
    assert [ch["subject"] for ch in doc["changes"]] == ["E1: rows", "first: the checklist"]
    assert doc["setup"]["practices"]["line"] == "tests written first, seen failing" and doc["setup"]["engine"] is False
    assert not (repo / ".operonx").exists()  # it only reads

    lines = text(doc)
    assert lines.startswith("Checklist: 1 of 4 verified, 2 open\n")
    assert "  CSV export — E2, phase 2 of 3: The service\n" in lines
    assert "  Q-01  ambiguity  Rounding of a division\n" in lines
    assert "Plans: 2 in progress, 1 plan, 2 done\n" in lines
    assert "Plan docs with no status block (1): docs/LEGACY_PLAN.md\n" in lines
    assert "engine: not used yet" in lines


def test_a_bare_folder_has_a_status_too(tmp_path):
    doc = status(tmp_path)
    assert doc["checklist"]["total"] == 0 and doc["plans"] == [] and doc["changes"] == []
    assert text(doc).startswith("Checklist: none")
    assert doc["setup"]["practices"]["file"] == ""


def _cli(*args: str) -> tuple:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(list(args))
    return code, buf.getvalue()


def test_the_api_op_and_the_cli_read_without_making_a_store(tmp_path):
    repo = _repo(tmp_path)
    code, out = _cli("api", "--repo", str(repo), '{"op": "status", "changes": 1}')
    got = json.loads(out)
    assert code == 0 and got["ok"] and [ch["subject"] for ch in got["project"]["changes"]] == ["E1: rows"]
    assert got["text"] == text(got["project"])
    code, out = _cli("status", "--repo", str(repo), "--json")
    assert code == 0 and json.loads(out)["checklist"]["verified"] == 1
    assert _cli("status", "--repo", str(repo))[1] == text(status(repo))
    assert not (repo / ".operonx").exists()
