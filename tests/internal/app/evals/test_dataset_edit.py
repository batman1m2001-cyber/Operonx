"""Editing a case in place, and archiving one (EVALS_PLAN D63, D64).

The JSONL file in git is the dataset's truth: an edit rewrites the one
line it changes and leaves every other line byte for byte, so the merge
request shows exactly what was edited. An archived case stays in the file
— its history across experiments stays readable — and is out of every run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from operonx.app.evals import Dataset, Eval, exact
from operonx.app.evals.fingerprint import dataset_version
from operonx.core import END, START, graph, op

LINES = [
    '{"id": "a", "input": "x", "expected": {"label": "refund"}, "tags": ["refund"]}\n',
    "\n",
    '"bare input"\n',
    '{"input":   "spaced",  "expected": "other"}\n',
    '{"id": "d", "input": "w", "split": "dev", "note": "kept"}\n',
]


def _file(tmp_path: Path) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(LINES), encoding="utf-8")
    return path


def _lines(path: Path):
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def test_an_edit_changes_one_line_and_keeps_the_rest(tmp_path):
    path = _file(tmp_path)
    ds = Dataset(path)
    row = ds.update("a", {"expected": {"label": "other"}, "tags": ["refund", "critical"]})
    assert row == {
        "id": "a",
        "input": "x",
        "expected": {"label": "other"},
        "tags": ["refund", "critical"],
    }
    lines = _lines(path)
    assert lines[1:] == LINES[1:]  # every other line, byte for byte
    assert json.loads(lines[0]) == row
    assert list(json.loads(lines[0])) == ["id", "input", "expected", "tags"]  # key order kept
    assert [r["id"] for r in ds.rows()][0] == "a" and len(ds.rows()) == 4


def test_none_removes_a_key_and_new_keys_follow(tmp_path):
    path = _file(tmp_path)
    row = Dataset(path).update("d", {"split": None, "note": None, "cluster": "s1"})
    assert row == {"id": "d", "input": "w", "cluster": "s1"}
    assert _lines(path)[4] == '{"id": "d", "input": "w", "cluster": "s1"}\n'
    assert _lines(path)[:4] == LINES[:4]


def test_a_line_without_an_id_gains_its_own(tmp_path):
    path = _file(tmp_path)
    ds = Dataset(path)
    bare, spaced = (r["id"] for r in ds.rows()[1:3])
    got = ds.update(bare, {"expected": "x"})
    assert got == {"id": bare, "input": "bare input", "expected": "x"}
    got = ds.update(spaced, {"tags": ["t"]})
    assert got["id"] == spaced and got["input"] == "spaced"
    # the ids did not move: the same cases, now saying their id
    assert [r["id"] for r in ds.rows()] == ["a", bare, spaced, "d"]


@pytest.mark.parametrize(
    "changes, match",
    [
        ({"input": "y"}, r"cannot change 'input'.*a different input is a different case"),
        ({"id": "zz"}, r"cannot change 'id'"),
        ({"colour": "red"}, r"cannot change 'colour'.*expected, tags, split"),
        ({"tags": "refund"}, r"tags is a list of strings"),
        ({"split": 3}, r"split is a string"),
        ({"status": "deleted"}, r"status is 'active' or 'archived'"),
        ({"trajectory": ["a"]}, r"trajectory is \{ops"),
        ({}, r"nothing to change"),
    ],
)
def test_what_an_edit_refuses_leaves_the_file_alone(tmp_path, changes, match):
    path = _file(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match=match):
        Dataset(path).update("a", changes)
    assert path.read_bytes() == before


def test_an_unknown_or_duplicate_case_is_refused(tmp_path):
    path = _file(tmp_path)
    with pytest.raises(ValueError, match=r"no case 'zz' in .*cases\.jsonl"):
        Dataset(path).update("zz", {"note": "n"})
    path.write_text(LINES[0] + LINES[0], encoding="utf-8")
    with pytest.raises(ValueError, match=r"case 'a' is on lines 1 and 2"):
        Dataset(path).update("a", {"note": "n"})
    with pytest.raises(ValueError, match="no such file"):
        Dataset(tmp_path / "none.jsonl").update("a", {"note": "n"})


def test_a_file_changed_while_editing_is_not_overwritten(tmp_path, monkeypatch):
    """A writer that is not a Dataset (an editor, a script) appends after
    the edit read the file: the edit is refused, the line survives."""
    import tempfile

    path = _file(tmp_path)
    ds = Dataset(path)
    real_mkstemp = tempfile.mkstemp

    def append_first(*a, **kw):  # the edit has read the file; someone appends
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"id": "late", "input": "z"}\n')
        return real_mkstemp(*a, **kw)

    monkeypatch.setattr("operonx.app.evals.dataset.tempfile.mkstemp", append_first)
    with pytest.raises(ValueError, match="changed while it was being edited"):
        ds.update("a", {"note": "n"})
    monkeypatch.undo()
    assert [r["id"] for r in ds.rows()][-1] == "late"  # the other writer's line survived
    assert "note" not in ds.rows()[0]
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]  # no temporary left


def test_edits_and_adds_from_many_threads_lose_nothing(tmp_path):
    """add() and update() take the folder's lock, so an append never lands
    in the file an edit is replacing."""
    import threading

    path = tmp_path / "cases.jsonl"
    path.write_text('{"id": "a", "input": "x"}\n', encoding="utf-8")
    errors = []

    def adder(k):
        try:
            for i in range(25):
                Dataset(path).add([{"id": f"t{k}-{i}", "input": i}])
        except Exception as exc:  # noqa: BLE001 — reported below
            errors.append(exc)

    def editor():
        try:
            for i in range(50):
                Dataset(path).update("a", {"note": str(i)})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=adder, args=(k,)) for k in range(4)]
    threads.append(threading.Thread(target=editor))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    rows = Dataset(path).rows()
    assert len(rows) == 1 + 4 * 25 and rows[0] == {"id": "a", "input": "x", "note": "49"}


def test_archived_cases_are_out_of_runs_and_versions(tmp_path):
    path = _file(tmp_path)
    ds = Dataset(path)
    active_before = dataset_version(ds.rows())
    ds.update("d", {"status": "archived"})
    assert [r["id"] for r in ds.rows()] == [r["id"] for r in ds.all_rows()][:3]
    assert len(ds.all_rows()) == 4 and ds.all_rows()[3]["status"] == "archived"
    assert ds.select(split="dev").rows() == []
    with pytest.raises(ValueError, match=r"not selected"):
        ds.select(ids=["d"]).rows()
    assert dataset_version(ds.rows()) != active_before
    # the version is of the active cases: the file without the archived one
    assert dataset_version(ds.rows()) == dataset_version(ds.all_rows()[:3])
    # active is the default, so making it active again writes no status
    ds.update("d", {"status": "active"})
    assert _lines(path)[4] == LINES[4] and dataset_version(ds.rows()) == active_before


@op(bound="sync")
def echo(text: str = "") -> dict:
    return {"label": text}


@graph
def flow(text: str = ""):
    e = echo(text=text)
    START >> e >> END


def test_an_eval_does_not_run_an_archived_case(tmp_path):
    path = tmp_path / "cases.jsonl"
    rows = [{"id": i, "input": i, "expected": {"label": i}} for i in ("a", "b", "c")]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    Dataset(path).update("b", {"status": "archived"})
    run = Eval(
        "echo",
        graph=flow,
        item_input="text",
        dataset=path,
        evaluators=[exact("label")],
        record_dir=tmp_path / "evals",
        trace=[],
    ).run_sync()
    assert sorted(i.key for i in run.items) == ["a", "c"]
    assert run.meta["eval"]["cases"] == 2


def test_problems_flag_a_bad_status(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text('{"id": "a", "input": 1, "status": "gone"}\n', encoding="utf-8")
    assert Dataset(path).problems() == [(1, "status is 'active' or 'archived', not 'gone'")]
