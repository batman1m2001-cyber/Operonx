"""`compare`, `calibrate` and the power functions (EVALS_PLAN D43–D45).

Every number is checked against a second computation: a hand formula, an
independent simulation through the gate, or the exact interval.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List

import pytest

from operonx.app.evals.calibrate import calibrate, discordance
from operonx.app.evals.compare import compare
from operonx.app.evals.experiments import ExperimentData
from operonx.app.evals.gate import CaseOutcome, Gate, compare_runs
from operonx.app.evals.stats import (
    detectable_drop,
    mcnemar,
    newcombe_paired,
    paired_sample_size,
)


def exp(eid: str, results: Dict[str, List[bool]], *, name="labels", sha="abc", dv="v1"):
    """An experiment from per-case trial outcomes (one check, ``ok``)."""
    repeats = max(len(v) for v in results.values())
    items = [
        {
            "key": c if repeats == 1 else f"{c}#{r}",
            "case": c,
            "repeat": r,
            "status": "ok",
            "passed": ok,
            "checks": {"ok": {"passed": ok}},
            "case_hash": f"h-{c}",
        }
        for r in range(repeats)
        for c, outs in results.items()
        if r < len(outs)
        for ok in [outs[r]]
    ]
    passes = [sum(v) / len(v) for v in results.values()]
    return ExperimentData(
        experiment_id=eid,
        eval=name,
        status="ok",
        started=f"2026-10-04T10:00:0{eid[-1]}+00:00",
        ended="2026-10-04T10:01:00+00:00",
        summary={
            "repeats": repeats,
            "metrics": {"pass": {"mean": sum(passes) / len(passes)}},
            "fingerprint": {"code_version": sha, "dataset_version": dv, "evaluators_hash": "e"},
        },
        items=items,
    )


# ── power ─────────────────────────────────────────────────────────────────


def test_the_t4_example_needs_312_cases():
    # by hand: (1.959964·√0.10 + 0.841621·√0.0975)² / 0.05²
    hand = (1.959964 * math.sqrt(0.10) + 0.841621 * math.sqrt(0.0975)) ** 2 / 0.0025
    got = paired_sample_size(0.10, 0.05)
    assert got == pytest.approx(hand, rel=1e-5) and math.ceil(got) == 312


def test_mcnemar_at_that_n_detects_the_drop_about_80_percent_of_the_time():
    """The formula is the normal approximation; the exact test is a little
    conservative (measured 77%)."""
    n, rng, hits, sims = 312, random.Random(7), 0, 3000
    for _ in range(sims):
        b = c = 0
        for _ in range(n):
            u = rng.random()
            if u < 0.075:  # pass → fail: (p_d + δ) / 2
                b += 1
            elif u < 0.10:  # fail → pass: (p_d − δ) / 2
                c += 1
        hits += mcnemar(b, c) < 0.05 and b > c
    assert 0.74 <= hits / sims <= 0.84


def test_detectable_drop_inverts_the_sample_size():
    assert detectable_drop(312, 0.10) == pytest.approx(0.05, abs=2e-4)
    for n in (100, 500, 2000):
        d = detectable_drop(n, 0.2)
        assert paired_sample_size(0.2, d) == pytest.approx(n, rel=1e-6)
    assert detectable_drop(50, 0.10) is None  # not even a 10-point drop
    with pytest.raises(ValueError, match="0 < delta ≤ p_d"):
        paired_sample_size(0.05, 0.10)


def test_discordance_is_the_share_of_cases_that_differ():
    a = exp("a1", {"c1": [True], "c2": [True], "c3": [False], "c4": [True]})
    b = exp("b2", {"c1": [False], "c2": [True], "c3": [True], "c4": [True]})
    assert discordance(a, b) == (0.5, 4)
    shares = exp("r3", {"c1": [True, False], "c2": [True, True]})
    other = exp("r4", {"c1": [True, True], "c2": [False, False]})
    assert discordance(shares, other) == ((0.5 + 1.0) / 2, 2)


# ── compare ───────────────────────────────────────────────────────────────


def _results(n: int, failing=()) -> Dict[str, List[bool]]:
    return {f"c{i}": [i not in failing] for i in range(n)}


def test_compare_without_a_tolerance_reports_and_does_not_judge():
    a = exp("a1", _results(40))
    b = exp("b2", _results(40, failing=range(10)))
    got = compare(a, b)
    assert (got["verdict"], got["exit_code"]) == (None, None)
    test = got["comparison"]["tests"][0]
    assert test["metric"] == "pass" and "verdict" not in test
    assert (test["regressed"], test["fixed"], test["diff"]) == (10, 0, -0.25)
    assert test["p"] == pytest.approx(mcnemar(10, 0), abs=1e-6)
    lo, hi = newcombe_paired(30, 10, 0, 0)
    assert (test["ci_lo"], test["ci_hi"]) == (
        pytest.approx(lo, abs=1e-6),
        pytest.approx(hi, abs=1e-6),
    )


def test_compare_with_a_tolerance_judges_like_the_gate():
    a = exp("a1", _results(40))
    b = exp("b2", _results(40, failing=range(10)))
    got = compare(a, b, tolerance=0.05)
    assert (got["verdict"], got["exit_code"]) == ("regressed", 1)
    assert "pass: -25.0 pts" in got["reasons"][0]
    same = compare(a, exp("b3", _results(40)), tolerance=0.05, strict=True)
    assert (same["verdict"], same["exit_code"]) == ("inconclusive", 2)  # 40 cases: ±8.8 pts
    assert compare(a, exp("b4", _results(40)), tolerance=0.1)["verdict"] == "pass"


def test_compare_says_what_is_not_directly_comparable():
    a = exp("a1", _results(10))
    b = exp("b2", _results(12), name="other", dv="v2")
    got = compare(a, b)
    assert got["warnings"][0] == "two different evals: 'labels' and 'other'"
    assert any("dataset_version differs" in w for w in got["warnings"])
    assert got["comparison"]["cases"] == 10 and got["comparison"]["only_in_this_run"] == 2


# ── calibrate ─────────────────────────────────────────────────────────────


def test_a_deterministic_eval_needs_the_intervals_own_width():
    """No case flips: every simulated A/A pair is identical, b = c = 0, and
    the tolerance is exactly the Newcombe interval's lower end."""
    runs = [exp(f"r{k}", _results(100, failing=range(20))) for k in range(3)]
    got = calibrate(runs, simulations=20)
    assert got["flaky_share"] == 0 and got["flip_rate"] == 0
    lo, _ = newcombe_paired(80, 0, 0, 20)
    r1 = next(r for r in got["rows"] if r["repeats"] == 1)
    assert r1["tolerance"] == math.ceil(-lo * 1000 - 1e-9) / 1000
    assert [o["diff"] for o in got["observed"]] == [0, 0, 0] and len(got["observed"]) == 3
    assert got["metrics"]["pass"] == {"means": [0.8, 0.8, 0.8], "sd": 0.0}


def _flaky_runs(k=3, n=300, flaky=30, p=0.6, seed=3):
    rng = random.Random(seed)
    out = []
    for run in range(k):
        res = {}
        for i in range(n):
            if i < flaky:
                res[f"c{i}"] = [rng.random() < p]
            else:
                res[f"c{i}"] = [i % 10 != 0]  # 10% always fail
        out.append(exp(f"r{run}", res))
    return out


def test_the_suggested_tolerance_passes_95_percent_of_a_a_runs_through_the_gate():
    runs = _flaky_runs()
    got = calibrate(runs, simulations=200, repeats=(1,))
    # flip rate by hand: 2·c(m−c)/(m(m−1)) per case, over all 300 cases
    sets = [r.outcomes() for r in runs]
    hand = 0.0
    for c in sets[0]:
        cc, m = sum(sum(s[c].passed) for s in sets), 3
        hand += 2 * cc * (m - cc) / (m * (m - 1))
    assert got["flip_rate"] == pytest.approx(hand / 300, abs=1e-6)
    tol = got["rows"][0]["tolerance"]
    assert 0 < tol < 0.2

    # an independent check: fresh A/A pairs (another seed) from the same p_i,
    # judged by the gate itself at that tolerance and at half of it
    passes = {c: sum(sum(s[c].passed) for s in sets) / 3 for c in sets[0]}
    rng = random.Random(99)

    def draw():
        return {c: CaseOutcome(c, passed=[rng.random() < q]) for c, q in passes.items()}

    def pass_share(t):
        gate = Gate(tolerance=t)
        verdicts = []
        for s in range(300):
            cmp = compare_runs(gate, draw(), draw(), baseline_id=f"x{s}")
            verdicts.append(cmp["tests"][0]["verdict"])
        return verdicts.count("pass") / len(verdicts)

    assert pass_share(tol) >= 0.92
    assert pass_share(tol / 2) < 0.80


def test_the_recommendation_is_the_fewest_repeats_that_reach_the_target():
    runs = _flaky_runs()
    got = calibrate(runs, simulations=60, repeats=(1, 3), target=0.5)
    assert got["recommended_repeats"] == 1 and got["suggested_tolerance"] <= 0.5
    rows = {r["repeats"]: r for r in got["rows"]}
    assert rows[1]["aa_pass_at_target"] == 1.0
    tight = calibrate(runs, simulations=60, repeats=(1, 3), target=0.001)
    assert tight["recommended_repeats"] is None and tight["suggested_tolerance"] is None
    assert "too noisy to gate at tolerance 0.1 pts with 300 cases" in tight["notes"][0]
    sets = [r.outcomes() for r in runs]
    flipped = sum(1 for c in sets[0] if len({s[c].passed[0] for s in sets}) > 1)
    assert 0 < flipped < 30 and got["flaky_share"] == pytest.approx(flipped / 300, abs=1e-6)
    assert any(f"{flipped} of 300 cases flipped" in n for n in got["notes"])


def test_calibrate_says_what_it_cannot_do():
    with pytest.raises(ValueError, match="at least two runs"):
        calibrate([exp("r1", _results(5))])
    with pytest.raises(ValueError, match="one eval"):
        calibrate([exp("r1", _results(5)), exp("r2", _results(5), name="other")])
    mixed = calibrate([exp("r1", _results(5)), exp("r2", _results(5), sha="def")], simulations=5)
    assert "different commits" in mixed["notes"][0]
