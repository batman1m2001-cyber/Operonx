"""`Eval(repeats=N)`: every case N times, and what that says about it.

Gates: each case becomes N job items keyed ``<id>#<r>``, yielded
repeat-major; each verdict names its case and repeat; cases are classed
stable-pass / stable-fail / flaky; pass^k is the unbiased estimate (checked
here by enumerating the trials' subsets); the counts and the pass rate
follow EVALS_PLAN D8; repeat keys resume; a ``cluster`` field gives the
clustered SE; ``repeats=1`` keeps the 1.14.0 keys and numbers.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from operonx.app.evals import Eval, exact
from operonx.core import END, START, graph, op

CALLS: dict = {}


@op(bound="sync")
def answer(text: str = "") -> dict:
    """Deterministic per call: `flaky` passes on its 1st and 3rd call."""
    n = CALLS.get(text, 0)
    CALLS[text] = n + 1
    if text.startswith("flaky"):
        ok = n % 2 == 0
    else:
        ok = not text.startswith("bad")
    return {"label": "yes" if ok else "no"}


@graph
def flow(text: str = ""):
    a = answer(text=text)
    START >> a >> END


@pytest.fixture(autouse=True)
def _fresh_calls():
    CALLS.clear()


CASES = [
    {"id": "good1", "input": "good one", "expected": {"label": "yes"}},
    {"id": "good2", "input": "good two", "expected": {"label": "yes"}},
    {"id": "bad", "input": "bad", "expected": {"label": "yes"}},
    {"id": "flaky", "input": "flaky", "expected": {"label": "yes"}},
]


def _eval(tmp_path: Path, rows=CASES, **kw) -> Eval:
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return Eval(
        "answers",
        graph=flow,
        input="text",
        dataset=path,
        evaluators=[exact("label")],
        record_dir=tmp_path / "evals",
        trace=[],
        concurrency=1,  # one at a time, so the flaky case's calls are in order
        **kw,
    )


def _unbiased_pass_hat(trials, k):
    """pass^k by brute force: the share of k-subsets of the trials that all pass."""
    subsets = list(itertools.combinations(trials, k))
    return sum(1 for s in subsets if all(s)) / len(subsets)


def test_each_case_runs_n_times_and_the_record_says_which(tmp_path):
    run = _eval(tmp_path, repeats=3).run_sync()
    assert [i.key for i in run.items] == [
        f"{c['id']}#{r}" for r in range(3) for c in CASES
    ]  # repeat-major
    for item in run.items:
        case, _, r = item.key.partition("#")
        assert (item.verdict["case"], item.verdict["repeat"]) == (case, int(r))
    flaky = [i.verdict["passed"] for i in run.items if i.verdict["case"] == "flaky"]
    assert flaky == [True, False, True]


def test_flaky_cases_and_pass_hat_k(tmp_path):
    run = _eval(tmp_path, repeats=3).run_sync()
    ev = run.meta["eval"]
    trials = {"good1": [1, 1, 1], "good2": [1, 1, 1], "bad": [0, 0, 0], "flaky": [1, 0, 1]}
    rel = ev["reliability"]
    assert (rel["stable_pass"], rel["stable_fail"], rel["flaky"]) == (2, 1, 1)
    assert rel["flaky_cases"] == ["flaky"]
    for k in (1, 2, 3):
        want = sum(_unbiased_pass_hat(t, k) for t in trials.values()) / len(trials)
        assert rel["pass_hat_k"][str(k)] == pytest.approx(want, abs=1e-6)
    assert rel["pass_hat_k"] == pytest.approx({"1": 2 / 3, "2": 7 / 12, "3": 0.5}, abs=1e-6)

    # the counts: cases are distinct, trials are items, passed/failed count trials
    assert (ev["cases"], ev["trials"], ev["passed"], ev["failed"]) == (4, 12, 8, 4)
    shares = [sum(t) / 3 for t in trials.values()]
    assert ev["pass_rate"] == pytest.approx(sum(shares) / 4, abs=1e-4)
    assert ev["metrics"]["pass"]["method"] == "clt"
    assert ev["metrics"]["pass"]["mean"] == pytest.approx(2 / 3, abs=1e-6)
    assert run.status == "failed"  # no gate, no threshold: a failed trial fails the run
    assert "passed=8/12 trials of 4 cases x3" in run.summary()


def test_repeat_keys_resume(tmp_path):
    ev = _eval(tmp_path, rows=CASES[:2], repeats=2)
    first = ev.run_sync()
    assert first.counts["ok"] == 4
    again = ev.run_sync(resume=True)
    assert again.counts["skipped"] == 4 and again.counts["ok"] == 0


def test_repeats_one_keeps_the_1_14_record(tmp_path):
    run = _eval(tmp_path).run_sync()
    assert [i.key for i in run.items] == ["good1", "good2", "bad", "flaky"]
    ev = run.meta["eval"]
    assert (ev["cases"], ev["passed"], ev["failed"], ev["errored"]) == (4, 3, 1, 0)
    assert ev["pass_rate"] == 0.75 and ev["checks"] == {"exact(label)": {"passed": 3, "cases": 4}}
    assert ev["metrics"]["pass"]["method"] == "wilson" and "reliability" not in ev
    assert run.items[0].verdict["repeat"] == 0 and run.items[0].verdict["case"] == "good1"
    assert "passed=3/4 (75.0%)" in run.summary()


def test_a_cluster_field_gives_the_clustered_se(tmp_path):
    rows = [dict(r, scenario="s1" if i < 2 else "s2") for i, r in enumerate(CASES)]
    run = _eval(tmp_path, rows=rows, cluster="scenario").run_sync()
    m = run.meta["eval"]["metrics"]["pass"]
    # cases 1,1 | 0,1 (flaky passes its first call): s̄ = 3/4; cluster sums
    # of deviations s1 = ¼ + ¼ = +½ and s2 = −¾ + ¼ = −½ → √(¼ + ¼) / 4
    assert m["method"] == "clustered"
    assert m["se"] == pytest.approx((0.25 + 0.25) ** 0.5 / 4, abs=1e-6)
    assert run.items[0].verdict["cluster"] == "s1"


def test_repeats_must_be_a_positive_whole_number(tmp_path):
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="repeats"):
            _eval(tmp_path, repeats=bad)
