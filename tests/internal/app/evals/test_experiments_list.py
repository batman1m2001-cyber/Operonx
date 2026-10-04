"""Listing experiments without reading every item (EVALS_PLAN D62).

A list shows summaries: from a store that is one ``list_experiments``
call, not a ``get_experiment`` and a ``scores`` query per experiment; from
a record, ``run.json`` without its items.
"""

from __future__ import annotations

import json
from pathlib import Path

from operonx.app.evals import Eval, Gate, exact
from operonx.app.evals.experiments import ExperimentData, experiments_of
from operonx.core import END, START, graph, op
from operonx.telemetry.scores import open_score_store


@op(bound="sync")
def classify(text: str = "", broken: bool = False) -> dict:
    label = "refund" if "money back" in text else "other"
    if broken and text.endswith("!"):
        label = "other"
    return {"label": label}


@graph
def flow(text: str = "", broken: bool = False):
    c = classify(text=text, broken=broken)
    START >> c >> END


def _data(path: Path) -> Path:
    rows = [
        {
            "id": f"c{i}",
            "input": ("I want my money back" if i % 2 else "hello") + ("!" if i % 3 == 0 else ""),
            "expected": {"label": "refund" if i % 2 else "other"},
        }
        for i in range(12)
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _run(tmp_path, store, broken=False):
    return Eval(
        "labels",
        graph=flow,
        item_input="text",
        inputs={"broken": broken},
        dataset=tmp_path / "cases.jsonl",
        evaluators=[exact("label")],
        record_dir=tmp_path / "evals",
        trace=[],
        repeats=2,
        gate=Gate(baseline="latest", tolerance=0.05),
        scores=store,
        variant="broken" if broken else "base",
    ).run_sync()


class Counting:
    """A store that counts what it is asked."""

    def __init__(self, store):
        self.store = store
        self.calls = []

    def __getattr__(self, name):
        attr = getattr(self.store, name)
        if not callable(attr):
            return attr

        def call(*a, **kw):
            self.calls.append(name)
            return attr(*a, **kw)

        return call


def _without_items(d: ExperimentData) -> dict:
    out = d.as_dict()
    out.pop("items")
    out.pop("source")
    return out


def test_from_experiment_is_from_stores_summary(tmp_path):
    _data(tmp_path / "cases.jsonl")
    store = open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    run = _run(tmp_path, store)
    full = ExperimentData.from_store(store, run.run_id)
    row = store.list_experiments().items[0]
    light = ExperimentData.from_experiment(row)
    assert light.items == [] and light.source == "store"
    assert _without_items(light) == _without_items(full)
    assert light.summary["metrics"]["pass"]["n"] == 12 and light.summary["variant"] == "base"


def test_a_list_without_items_reads_summaries_only(tmp_path):
    _data(tmp_path / "cases.jsonl")
    store = open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    runs = [_run(tmp_path, store), _run(tmp_path, store, broken=True)]
    full = experiments_of("labels", store=store, record_dirs=[tmp_path / "evals"])
    light = experiments_of("labels", store=store, record_dirs=[tmp_path / "evals"], items=False)
    assert [d.experiment_id for d in light] == [r.run_id for r in reversed(runs)]
    assert all(d.items == [] for d in light) and all(d.items for d in full)
    assert [_without_items(d) for d in light] == [_without_items(d) for d in full]
    assert (
        light[0].summary["variant"] == "broken"
        and light[0].gate["comparison"]["flips"]["regressed"] == 2
    )

    # from the store alone (the records are on another machine): one list call
    counting = Counting(store)
    only = experiments_of("labels", store=counting, items=False)
    assert counting.calls == ["list_experiments"]
    assert [d.experiment_id for d in only] == [d.experiment_id for d in light]
    assert [d.summary["metrics"] for d in only] == [d.summary["metrics"] for d in light]
    assert {d.source for d in only} == {"store"}


def test_flip_class_is_the_comparisons_rule():
    from operonx.app.evals.gate import CaseOutcome, flip_class

    def case(*passed):
        return CaseOutcome("c", passed=list(passed))

    table = [
        ((True, True), (False, False), "regressed"),
        ((False, False), (True, True), "fixed"),
        ((True, True), (True, False), "destabilised"),
        ((True, False), (True, True), "stabilised"),
        ((False, False), (True, False), None),  # stable fail → flaky: not "fixed"
        ((True, False), (False, True), None),  # flaky on both sides: noise
        ((True,), (True,), None),
    ]
    for a, b, want in table:
        assert flip_class(case(*a), case(*b)) == want, (a, b)
