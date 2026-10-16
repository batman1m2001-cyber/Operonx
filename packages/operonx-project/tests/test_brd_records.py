"""The register, records, questions, the matrix; init → check; diffing a new source version."""

from __future__ import annotations

import subprocess

from operonx_project.brd.checker import check
from operonx_project.brd.diff import affected, diff_sources, mark_changed
from operonx_project.brd.extraction import check_coverage, check_ids, check_quotes
from operonx_project.brd.folder import init_brd, refresh_summary
from operonx_project.brd.readers import read_source
from operonx_project.brd.records import (
    Question,
    Requirement,
    add_matrix_rows,
    add_questions,
    load_requirements,
    matrix_status,
    write_requirements,
)
from operonx_project.brd.register import add_owner_request, load_register, private_hashes, register_source
from tests.brd_files import SAMPLE_BLOCKS, make_docx


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def test_register_rows_dedupe_and_private(tmp_path):
    repo = _repo(tmp_path)
    brd = repo / "docs" / "brd"
    init_brd(repo)
    secret = repo / "client.docx"
    make_docx(secret, SAMPLE_BLOCKS)
    a = register_source(brd, secret, private=True, repo=repo, title="Client BRD")
    assert a.id == "SRC-01" and a.private and str(secret.resolve()) in a.where
    assert register_source(brd, secret, private=True, repo=repo).id == "SRC-01"  # the same bytes: one source
    b = add_owner_request(brd, "Flag office documents in commits.", date="2026-10-17")
    assert b.id == "SRC-02" and b.role == "approved"
    rows = load_register(brd)
    assert [(r.id, r.private) for r in rows] == [("SRC-01", True), ("SRC-02", False)]
    assert private_hashes(brd) == {a.sha256[0]: "Client BRD"}
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert "/client.docx" in exclude
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert "client.docx" not in status


def _req(rid, quote, typ="functional", **kw):
    return Requirement(rid, typ, quote, kw.pop("location", "§2"), reading=kw.pop("reading", "r"), **kw)


def test_checks_catch_a_planted_fake_quote_and_a_skipped_section(tmp_path):
    src = read_source(make_docx(tmp_path / "b.docx", SAMPLE_BLOCKS), tmp_path / "o")
    secs = src.sections
    good = _req("BRD-BR-01", "Gửi lời nhắc trước buổi học 24 giờ")
    fake = _req("BRD-BR-02", "Phụ huynh được hoàn tiền trong 7 ngày")  # not in the source
    gap = _req("BRD-RULE-CALL-01", "Không gọi điện cho phụ huynh … sau 21 giờ", "rule", location="§1")
    probs = check_quotes([good, fake, gap], secs)
    assert [(p.id, p.kind) for p in probs] == [("BRD-BR-02", "fake_quote")]
    assert good.section == "§2" and gap.section == "§3"  # found where it is, not where it was said to be
    # §1 has text, no requirement and no reason: skipped
    cov = check_coverage(secs, [good, gap], descriptive={})
    assert [p.id for p in cov] == ["§1"]
    assert check_coverage(secs, [good, gap], descriptive={"§1": "an introduction"}) == []


def test_ids_stable_and_unique():
    reqs = [_req("BRD-BR-01", "x" * 10), _req("BRD-BR-01", "y" * 10), _req("bad id", "z" * 10),
            _req("BRD-OLD-01", "w" * 10)]  # fmt: skip
    kinds = {(p.id, p.kind) for p in check_ids(reqs, previous=["BRD-BR-01", "BRD-BR-07"], retired=["BRD-OLD-01"])}
    assert kinds == {("BRD-BR-01", "duplicate_id"), ("bad id", "bad_id"), ("BRD-OLD-01", "reused_id"),
                     ("BRD-BR-07", "dropped_id")}  # fmt: skip


def test_records_matrix_and_questions_pass_the_checker(tmp_path):
    repo = _repo(tmp_path)
    brd = repo / "docs" / "brd"
    init_brd(repo, project="reminders")
    assert check(brd)[0] == []  # a new folder checks clean
    reqs = [_req("BRD-BR-01", "Gửi lời nhắc trước buổi học 24 giờ", location="§2 Yêu cầu kinh doanh"),
            _req("BRD-RULE-CALL-01", "Không gọi điện cho phụ huynh sau 21 giờ", "rule",
                 status="needs_clarification", question="Q-01")]  # fmt: skip
    write_requirements(brd, "brd", "Client BRD", "SRC-01", reqs)
    (q,) = add_questions(brd, [Question("Q-00", "ambiguity", "Whose time zone is 21 giờ?", "§3 says 21 giờ")])
    assert q.id == "Q-01"
    assert add_questions(brd, [Question("Q-00", "ambiguity", "whose time zone is  21 giờ?", "")]) == []
    assert add_matrix_rows(brd, reqs, "Client BRD") == ["BRD-BR-01", "BRD-RULE-CALL-01"]
    assert add_matrix_rows(brd, reqs, "Client BRD") == []  # never twice
    refresh_summary(brd)
    errors, report = check(brd)
    assert errors == [], errors
    assert report[0].startswith("BRD: {'planned': 2}")
    assert matrix_status(brd) == {"BRD-BR-01": "planned", "BRD-RULE-CALL-01": "planned"}
    back = {r.id: r for r in load_requirements(brd)}
    assert back["BRD-RULE-CALL-01"].type == "rule" and back["BRD-RULE-CALL-01"].question == "Q-01"
    # a dropped id stays, superseded
    write_requirements(brd, "brd", "Client BRD", "SRC-01", reqs[:1])
    assert {r.id: r.status for r in load_requirements(brd)}["BRD-RULE-CALL-01"] == "superseded"


def test_a_new_source_version_marks_what_changed(tmp_path):
    old = read_source(make_docx(tmp_path / "v1.docx", SAMPLE_BLOCKS), tmp_path / "o1")
    blocks = list(SAMPLE_BLOCKS)
    blocks[6] = ("p", "Không gọi điện cho phụ huynh sau 20 giờ. Mỗi phụ huynh nhận tối đa hai cuộc gọi mỗi ngày.")
    blocks.insert(7, ("h2", "4. Báo cáo"))
    blocks.insert(8, ("p", "Mỗi tuần gửi báo cáo cho trung tâm."))
    new = read_source(make_docx(tmp_path / "v2.docx", blocks), tmp_path / "o2")
    d = diff_sources(old, new)
    assert d.changed == [("§3", "§3")] and d.added == ["§4"] and d.removed == [] and d.same >= 2
    reqs = [_req("BRD-BR-01", "q", section="§2"), _req("BRD-RULE-CALL-01", "q", "rule", section="§3")]
    assert affected(d, reqs) == ["BRD-RULE-CALL-01"]
    repo = _repo(tmp_path)
    brd = repo / "docs" / "brd"
    init_brd(repo)
    add_matrix_rows(brd, reqs, "BRD")
    assert mark_changed(brd, ["BRD-RULE-CALL-01"], "SRC-02") == ["BRD-RULE-CALL-01"]
    assert "source changed (SRC-02)" in (brd / "requirements" / "requirements_traceability.md").read_text()


def test_a_sources_own_letter_codes_are_ids():
    # E0: the BRD's review findings are coded F01…F27; BRD-F26 keeps the source's code
    assert check_ids([_req("BRD-F26", "x" * 10), _req("BRD-NFR-01", "y" * 10)]) == []
    assert [p.kind for p in check_ids([_req("BRD-FINDING", "z" * 10)])] == ["bad_id"]
