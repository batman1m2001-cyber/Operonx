"""Choosing cases, checking a dataset, comparing it with git (EVALS_PLAN D47).

A selection is the experiment's dataset: its version is of the selected
cases, and the run records what was selected.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from operonx.app.evals import Dataset, Eval, exact
from operonx.app.evals.dataset import diff_rows
from operonx.app.evals.fingerprint import dataset_version
from operonx.core import END, START, graph, op

ROWS = [
    {"id": "a", "input": "x", "split": "dev", "tags": ["refund"]},
    {"id": "b", "input": "y", "split": "test", "tags": ["refund", "critical"]},
    {"id": "c", "input": "z", "split": "test"},
    {"id": "d", "input": "w", "split": "dev", "tags": ["smalltalk"]},
    {"id": "e", "input": "v"},
]


def _write(path: Path, rows) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _ids(ds: Dataset):
    return [r["id"] for r in ds.rows()]


def test_split_tags_ids_and_sample(tmp_path):
    ds = Dataset(_write(tmp_path / "d.jsonl", ROWS))
    assert _ids(ds.select(split="test")) == ["b", "c"]
    assert _ids(ds.select(tags=["critical", "smalltalk"])) == ["b", "d"]  # any of them
    assert _ids(ds.select(split="dev", tags=["refund"])) == ["a"]  # all criteria
    assert _ids(ds.select(ids=["d", "a"])) == ["a", "d"]  # file order
    # sample: the N ids with the smallest sha256, whoever asks and wherever
    by_hash = sorted("abcde", key=lambda i: hashlib.sha256(i.encode()).hexdigest())
    got = _ids(ds.select(sample=2))
    assert sorted(got) == sorted(by_hash[:2]) and got == [i for i in "abcde" if i in got]
    assert _ids(ds.select(sample=10)) == list("abcde")
    # the view is the same file; the original is untouched
    view = ds.select(split="dev")
    assert view.path == ds.path and _ids(ds) == list("abcde")
    assert view.selection == {"split": "dev"} and ds.selection == {}
    assert "split='dev'" in repr(view)


def test_an_unknown_id_is_an_error(tmp_path):
    ds = Dataset(_write(tmp_path / "d.jsonl", ROWS))
    with pytest.raises(ValueError, match=r"cases not in .*d\.jsonl \(or not selected\): \['zz'\]"):
        ds.select(ids=["a", "zz"]).rows()
    with pytest.raises(ValueError, match="sample"):
        ds.select(sample=0)


@op(bound="sync")
def echo(text: str = "") -> dict:
    return {"text": text}


@graph
def flow(text: str = ""):
    e = echo(text=text)
    START >> e >> END


def test_a_selection_is_the_experiments_dataset(tmp_path):
    ds = Dataset(_write(tmp_path / "d.jsonl", ROWS)).select(split="test")
    run = Eval(
        "sel",
        graph=flow,
        item_input="text",
        dataset=ds,
        evaluators=[lambda output=None: True],
        record_dir=tmp_path / "evals",
        trace=[],
        variant="prompt v2",
    ).run_sync()
    s = run.meta["eval"]
    assert [i.key for i in run.items] == ["b", "c"]
    assert s["fingerprint"]["dataset_version"] == dataset_version(
        [r for r in Dataset(tmp_path / "d.jsonl").rows() if r.get("split") == "test"]
    )
    assert s["selection"] == {"split": "test"} and s["variant"] == "prompt v2"
    assert run.meta["variant"] == "prompt v2"

    from operonx.app.evals.publish import experiment_of

    exp = experiment_of(run)
    assert (exp.split, exp.variant) == ("test", "prompt v2")


def test_problems_name_their_line(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"id": "a", "input": 1}),
                "{not json",
                json.dumps({"id": "a", "input": 2}),
                json.dumps({"id": "t", "input": 3, "tags": "refund"}),
                json.dumps({"id": "s", "input": 4, "split": 7, "cluster": ["x"]}),
                json.dumps({"id": "j", "input": 5, "trajectory": {"ops": "classify"}}),
                json.dumps({"id": "k", "input": 6, "trajectory": ["classify"]}),
                "",
                json.dumps({"id": "ok", "input": 7, "tags": ["x"], "trajectory": {"ops": ["a"]}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    got = Dataset(path).problems()
    assert got == [
        (2, "not JSON: Expecting property name enclosed in double quotes"),
        (3, "duplicate id 'a' (first on line 1)"),
        (4, "tags is a list of strings, not 'refund'"),
        (5, "split is a string, not 7"),
        (5, "cluster is a string, not ['x']"),
        (6, "trajectory.ops is a list, not 'classify'"),
        (7, "trajectory is {ops: [...], tool_calls: [...]}, not ['classify']"),
    ]
    assert Dataset(_write(tmp_path / "good.jsonl", ROWS)).problems() == []
    assert Dataset(tmp_path / "missing.jsonl").problems() == [(0, "no such file")]


def test_diff_by_case():
    old = [
        {"id": "a", "input": 1, "expected": "x"},
        {"id": "b", "input": 2},
        {"id": "c", "input": 3},
    ]
    new = [
        {"id": "a", "input": 1, "expected": "y"},  # expected edited
        {"id": "c", "input": 3, "tags": ["t"]},  # tags are not what is asked: unchanged
        {"id": "d", "input": 4},
    ]
    assert diff_rows(old, new) == {"added": ["d"], "removed": ["b"], "changed": ["a"]}
