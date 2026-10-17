"""Findings must point at the diff; held-out failures show names and messages only."""

from __future__ import annotations

from operonx_project.review import check_findings, fingerprint, heldout_failures


def test_a_blocking_finding_must_cite_a_changed_file_and_a_real_line(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    (tmp_path / "b.py").write_text("z = 3\n")
    fs = [
        {"severity": "major", "file": "a.py", "line": 2, "title": "ok"},
        {"severity": "blocker", "file": "b.py", "line": 1, "title": "not in the diff"},
        {"severity": "major", "file": "a.py", "line": 9, "title": "no such line"},
        {"severity": "minor", "file": "a.py", "line": 1, "title": "minor never blocks"},
    ]
    blocking, other = check_findings(fs, tmp_path, ["a.py"])
    assert [f["title"] for f in blocking] == ["ok"]
    assert {f["title"]: f.get("unverified") for f in other} == {
        "not in the diff": "b.py is not in the diff",
        "no such line": "a.py has no line 9",
        "minor never blocks": None,
    }


def test_same_as_keeps_the_previous_fingerprint(tmp_path):
    (tmp_path / "a.py").write_text("x\n")
    prev = fingerprint("a.py", "missing check")
    (f,), _ = check_findings(
        [{"severity": "major", "file": "a.py", "line": 1, "title": "reworded", "same_as": prev}],
        tmp_path,
        ["a.py"],
        [prev],
    )
    assert f["fingerprint"] == prev
    (g,), _ = check_findings([{"severity": "major", "file": "a.py", "line": 1, "title": "Missing  check!"}],
                             tmp_path, ["a.py"])  # fmt: skip
    assert g["fingerprint"] == prev  # normalized title


def test_heldout_failures_hide_where_and_how(tmp_path):
    hdir = tmp_path / "secret" / "held"
    out = (
        f"FAILED {hdir}/test_h.py::test_negative - AssertionError: add handles negatives\n"
        f"ERROR {hdir}/test_i.py - ImportError: no module named x in {hdir}/test_i.py\n"
        "FAILED test_k.py::test_k\n"
    )
    got = heldout_failures(out, hdir)
    assert got == [
        "test_negative: AssertionError: add handles negatives",
        "test_i.py: ImportError: no module named x in <held-out>/test_i.py",
        "test_k: failed",
    ]
    assert all(str(hdir) not in g for g in got)
