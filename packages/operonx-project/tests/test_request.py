"""The owner's words become rows (plan D14): ``request`` makes the words a BRD
source, extracts them into requirements that continue the BRD's numbering,
and scopes a milestone to those rows, planned and waiting for approval."""

from __future__ import annotations

from pathlib import Path

from operonx_project.api import act
from operonx_project.brd.folder import init_brd
from operonx_project.brd.records import (
    Requirement,
    add_matrix_rows,
    load_requirements,
    matrix_status,
    write_requirements,
)
from operonx_project.brd.register import register_source
from operonx_project.model import MilestoneState as M
from operonx_project.project import Project
from tests.flow_env import git, make_repo
from tests.life_env import ADD_ROW, Life, base_script, card_json, plan, write_source

HALVE = "CALC-REQ-MATH-02"
WORDS = "The calculator must halve a number."


def _repo_with_a_brd(root: Path) -> Path:
    """A repo whose main already has a BRD with one requirement, ADD_ROW."""
    repo = make_repo(root)
    brd = repo / "docs" / "brd"
    init_brd(repo, "docs/brd", project="calc")
    src = register_source(brd, write_source(root), role="authoritative", title="calc", repo=repo)
    req = Requirement(id=ADD_ROW, type="functional", quote="The calculator must add two numbers.", location="§1",
                      source=src.id)  # fmt: skip
    write_requirements(brd, "calc", "calc", src.id, [req])
    add_matrix_rows(brd, [req], "Arithmetic")
    git(repo, "add", "docs/brd")
    git(repo, "commit", "-q", "-m", "the BRD")
    return repo


def _extraction(rid: str):
    return {"output": {"requirements": [{"id": rid, "type": "functional", "section": "S01", "quote": WORDS,
                                         "reading": "halve"}],
                       "descriptive": []}}  # fmt: skip


def _request(root: Path, rid: str):
    repo = _repo_with_a_brd(root)
    project = Project.open(repo, worktrees=root / "worktrees")
    got = act(project, {"op": "request", "text": WORDS, "title": "Halve"}, actor="owner")
    assert got["ok"] and got["milestone"] == "m1", got
    script = base_script(**{
        "planner:extractor": [_extraction(rid)],
        "reviewer:hunter": [{"output": {"requirements": []}}],
        "planner:plan": [plan([card_json("C1", rid, title="halve", criteria=[
            {"row": rid, "text": "WHEN half(x) THE SYSTEM SHALL return x / 2"}])])],
    })  # fmt: skip
    return got, Life(root, script, repo=repo)


def test_a_request_becomes_new_rows_that_scope_a_milestone_waiting_for_approval(tmp_path):
    got, life = _request(tmp_path, HALVE)
    assert life.serve() == M.AWAITING_APPROVAL, life.store.get_milestone("m1").reason
    ms = life.store.get_milestone("m1")
    assert ms.rows == [HALVE]  # the request's row only, not ADD_ROW
    assert [r["id"] for r in life.store.ms_get("m1", "context")["rows"]] == [HALVE]
    brd = life.integ() / "docs" / "brd"
    assert (brd / got["source"]).read_text().count(WORDS) == 1  # the words are a source in the BRD
    assert set(matrix_status(brd)) == {ADD_ROW, HALVE}
    # each source keeps its own file: the request never rewrote (or superseded) the first source's rows
    assert {(r.id, r.source, r.status) for r in load_requirements(brd)} == {
        (ADD_ROW, "SRC-01", "documented"), (HALVE, "SRC-02", "documented")}  # fmt: skip
    prompt = life.coder.of("planner:extractor")[0]["prompt"]
    assert "never reuse" in prompt and ADD_ROW in prompt  # the extractor was told what is taken


def test_a_request_that_reuses_an_id_fails_loudly(tmp_path):
    _, life = _request(tmp_path, ADD_ROW)
    assert life.serve() == M.FAILED
    assert "reuse ids the BRD already has: " + ADD_ROW in life.store.get_milestone("m1").reason


def test_a_request_needs_words(tmp_path):
    project = Project.open(make_repo(tmp_path), worktrees=tmp_path / "worktrees")
    got = act(project, {"op": "request", "text": "  "})
    assert not got["ok"] and got["status"] == "invalid"
