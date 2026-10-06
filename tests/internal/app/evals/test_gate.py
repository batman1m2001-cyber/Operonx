"""The gate (`operonx.app.evals.gate`): three states, a must-pass tier, an
error budget, and the exit code they make.

The two numbers the roadmap gates E1 on are simulations of the decision
itself: with no true effect (A/A) a run is called ``regressed`` at most 5%
of the time, and a true 10-point drop on 300 cases is caught at least 80%
of the time. Each simulated rate is also checked against the exact rate
under the same model, computed by summing the trinomial distribution of
the discordant counts — so the test verifies the gate, not just itself.
"""

from __future__ import annotations

import json
import math
import random
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from operonx.app import Application
from operonx.app.evals import Eval, Gate, exact
from operonx.app.evals.gate import CaseOutcome, decide
from operonx.app.evals.stats import estimate, mcnemar, newcombe_paired, wilson
from operonx.core import END, START, graph, op

# ── synthetic paired runs ─────────────────────────────────────────────────

#: Case difficulty: each case passes with its own probability q ~ Beta(4, 1)
#: (mean 0.8) — easy cases mostly pass, a few are hard. B's probability is
#: r·q, so B's mean pass rate is 0.8·r: r = 1 is A/A, r = 7/8 a 10-pt drop.
BETA_A, BETA_B = 4, 1  # b = 1, so q is drawn as U^(1/a) below
EQ = BETA_A / (BETA_A + BETA_B)  # E[q] = 0.8
EQ2 = BETA_A * (BETA_A + 1) / ((BETA_A + BETA_B) * (BETA_A + BETA_B + 1))  # E[q²] = 2/3


def _paired_run(rng, n, r, repeats=1):
    base, cur = {}, {}
    for i in range(n):
        q = rng.random() ** (1 / BETA_A)  # Beta(a, 1) is U^(1/a)
        a = [rng.random() < q for _ in range(repeats)]
        b = [rng.random() < r * q for _ in range(repeats)]
        base[f"c{i}"] = CaseOutcome(f"c{i}", passed=a)
        cur[f"c{i}"] = CaseOutcome(f"c{i}", passed=b)
    return base, cur


def _gate_verdict(gate, base, cur):
    values = [o.share("pass") for o in cur.values()]
    got = decide(
        gate,
        current=cur,
        metrics={"pass": estimate(values, bounds=(0, 1))},
        complete=True,
        baseline=("A", base, None),
    )
    return got["verdict"]


def _exact_regressed_rate(n, r, tol, alpha=0.05):
    """P(regressed) for one binary metric, exactly — without the code's
    McNemar. Per case, A pass/B fail has probability pb = E[q(1 − rq)] and
    A fail/B pass pc = E[(1−q)rq]. The m discordant cases are Bin(n, pb+pc);
    given m, b is Bin(m, pb/(pb+pc)) and McNemar's p for (b, m − b) is
    2·P(X ≤ min) under Bin(m, ½). Sum every (m, b) the gate calls regressed:
    p < alpha and (c − b)/n < −tol."""
    pb = EQ - r * EQ2
    pc = r * (EQ - EQ2)
    pd, theta = pb + pc, pb / (pb + pc)
    lf = [math.lgamma(k + 1) for k in range(n + 2)]

    def pmf(k, m, p):
        return math.exp(lf[m] - lf[k] - lf[m - k] + k * math.log(p) + (m - k) * math.log(1 - p))

    total = 0.0
    for m in range(1, n + 1):
        p_m = pmf(m, n, pd)
        if p_m < 1e-16:
            continue
        half_cdf, acc = [], 0.0
        for i in range(m + 1):
            acc += math.exp(lf[m] - lf[i] - lf[m - i] - m * math.log(2))
            half_cdf.append(acc)
        for b in range(m + 1):
            c = m - b
            if (c - b) / n < -tol and min(1.0, 2 * half_cdf[min(b, c)]) < alpha:
                total += p_m * pmf(b, m, theta)
    return total


#: Binary cases take the McNemar/Newcombe path, which never bootstraps; the
#: replicate count only matters for the repeats simulation below.
SIM_GATE = Gate(baseline="latest", tolerance=0.02, bootstrap=100)


def test_a_a_runs_are_rarely_called_regressed():
    rng = random.Random(20261004)
    runs = 1000
    regressed = sum(
        _gate_verdict(SIM_GATE, *_paired_run(rng, 300, 1.0)) == "regressed" for _ in range(runs)
    )
    rate = regressed / runs
    exact_rate = _exact_regressed_rate(300, 1.0, 0.02)
    assert rate <= 0.05, f"A/A: {rate:.1%} of identical runs called regressed"
    assert abs(rate - exact_rate) < 4 * math.sqrt(max(exact_rate, 1e-3) * (1 - exact_rate) / runs)


def test_a_true_ten_point_drop_on_300_cases_is_caught():
    rng = random.Random(41)
    runs = 1000
    regressed = sum(
        _gate_verdict(SIM_GATE, *_paired_run(rng, 300, 7 / 8)) == "regressed" for _ in range(runs)
    )
    power = regressed / runs
    exact_power = _exact_regressed_rate(300, 7 / 8, 0.02)
    assert power >= 0.80, f"a −10 pt drop on 300 cases caught {power:.1%} of the time"
    assert abs(power - exact_power) < 4 * math.sqrt(exact_power * (1 - exact_power) / runs)
    # and T4 §8.5's normal approximation, from the discordance rate, agrees
    pb, pc = EQ - (7 / 8) * EQ2, (7 / 8) * (EQ - EQ2)
    p_d, delta, z = pb + pc, pb - pc, 1.959964
    approx = 0.5 * (
        1
        + math.erf(
            ((delta * math.sqrt(300) - z * math.sqrt(p_d)) / math.sqrt(p_d - delta**2)) / 2**0.5
        )
    )
    assert power == pytest.approx(approx, abs=0.05)


def test_a_a_with_repeats_uses_the_bootstrap_and_holds_the_bound():
    rng = random.Random(7)
    gate = Gate(baseline="latest", tolerance=0.02, bootstrap=400)
    runs = 400
    verdicts = [_gate_verdict(gate, *_paired_run(rng, 100, 1.0, repeats=3)) for _ in range(runs)]
    assert verdicts.count("regressed") / runs <= 0.05


# ── verdicts and exit codes ───────────────────────────────────────────────


def _cases(pattern, tags=None, errored=()):
    """{case: [pass, pass, …]} → outcomes."""
    out = {}
    for case, passes in pattern.items():
        out[case] = CaseOutcome(
            case,
            passed=list(passes),
            tags=tuple((tags or {}).get(case, ())),
            errored=int(case in errored),
        )
    return out


def _decide(gate, cur, base=None, complete=True):
    values = [o.share("pass") for o in cur.values()]
    return decide(
        gate,
        current=cur,
        metrics={"pass": estimate(values, bounds=(0, 1))},
        complete=complete,
        baseline=("A", base, None) if base is not None else None,
    )


def test_each_verdict_and_its_exit_code():
    good = _cases({f"c{i}": [True] for i in range(40)})
    got = _decide(Gate(threshold=0.9), good)
    assert (got["verdict"], got["exit_code"], got["reasons"]) == ("pass", 0, [])

    low = _cases({f"c{i}": [i < 30] for i in range(40)})
    got = _decide(Gate(threshold=0.9), low)
    assert (got["verdict"], got["exit_code"]) == ("failed", 1)
    assert got["reasons"] == ["pass: 75.0% < threshold 90.0%"]

    # vs a baseline: 10 of 40 cases newly fail — b = 10, c = 0, p = 2/1024
    got = _decide(Gate(baseline="latest", tolerance=0.05), low, base=good)
    test = got["comparison"]["tests"][0]
    assert test["method"] == "mcnemar" and test["regressed"] == 10
    assert test["p"] == pytest.approx(mcnemar(10, 0), abs=1e-6)
    assert (got["verdict"], got["exit_code"]) == ("regressed", 1)

    # two flips in 40: not significant, and a 5-pt drop cannot be ruled out
    two = _cases({f"c{i}": [i >= 2] for i in range(40)})
    got = _decide(Gate(baseline="latest", tolerance=0.05), two, base=good)
    assert (got["verdict"], got["exit_code"]) == ("inconclusive", 0)
    strict = _decide(Gate(baseline="latest", tolerance=0.05, strict=True), two, base=good)
    assert (strict["verdict"], strict["exit_code"]) == ("inconclusive", 2)

    # the same, with a 20-pt tolerance: the CI rules a 20-pt drop out
    got = _decide(Gate(baseline="latest", tolerance=0.2), two, base=good)
    assert got["verdict"] == "pass"

    # an error budget: 3 of 40 trials errored > 5%
    err = _cases({f"c{i}": [i >= 3] for i in range(40)}, errored={"c0", "c1", "c2"})
    got = _decide(Gate(), err)
    assert (got["verdict"], got["exit_code"]) == ("error", 3)
    assert "3 of 40 trials errored" in got["reasons"][0]
    assert _decide(Gate(max_error_rate=0.1), err)["verdict"] == "pass"

    # a run that did not reach every case is infrastructure, whatever its numbers
    got = _decide(Gate(), good, complete=False)
    assert (got["verdict"], got["exit_code"]) == ("error", 3)


def test_the_must_pass_tier():
    # tolerance=1.0: two cases say nothing statistically; this is about the tier
    tags = {"k": ["critical"]}
    failing = _cases({"k": [False, False], "x": [True, True]}, tags)
    got = _decide(Gate(), failing)
    assert got["verdict"] == "failed" and got["must_pass"]["broken"] == ["k"]

    passed_before = _cases({"k": [True, True], "x": [True, True]}, tags)
    got = _decide(Gate(baseline="latest", tolerance=1.0), failing, base=passed_before)
    assert got["verdict"] == "regressed"
    assert "passed in the baseline and fails" in " ".join(got["reasons"])

    # already failing in the baseline: not a regression of this run
    failed_before = _cases({"k": [False, False], "x": [True, True]}, tags)
    got = _decide(Gate(baseline="latest", tolerance=1.0), failing, base=failed_before)
    assert got["verdict"] == "pass" and got["must_pass"]["broken"] == []

    # flaky is a warning, not a verdict
    flaky = _cases({"k": [True, False], "x": [True, True]}, tags)
    got = _decide(Gate(), flaky)
    assert got["verdict"] == "pass" and "flaky" in got["warnings"][0]
    assert _decide(Gate(must_pass_tag=None), failing)["verdict"] == "pass"


def test_flips_are_classed_by_stability():
    base = _cases({"a": [1, 1, 1], "b": [0, 0, 0], "c": [1, 1, 1], "d": [1, 0, 1], "e": [1, 0, 0]})
    cur = _cases({"a": [0, 0, 0], "b": [1, 1, 1], "c": [1, 0, 1], "d": [1, 1, 1], "e": [0, 1, 0]})
    flips = _decide(Gate(baseline="latest", tolerance=1.0), cur, base=base)["comparison"]["flips"]
    assert flips["cases"] == {
        "regressed": ["a"],
        "fixed": ["b"],
        "destabilised": ["c"],
        "stabilised": ["d"],
    }
    assert flips["verified"] is True  # e: flaky → flaky is noise, not listed


def test_a_gate_says_what_is_wrong_with_it():
    with pytest.raises(ValueError, match="needs a tolerance"):
        Gate(baseline="latest")
    with pytest.raises(ValueError, match="names no baseline"):
        Gate(baseline="git:", tolerance=0.03)
    with pytest.raises(ValueError, match="needs a tolerance"):
        Gate(baseline="main")
    with pytest.raises(ValueError, match="pass rate in"):
        Gate(threshold=1.5)
    with pytest.raises(ValueError, match="not metrics of this eval"):
        Eval(
            "e",
            graph=flow,
            dataset="x.jsonl",
            evaluators=[exact("label")],
            gate=Gate(threshold={"exact(lable)": 0.9}),
        )
    with pytest.raises(ValueError, match="Gate\\(threshold"):
        Eval("e", graph=flow, dataset="x.jsonl", threshold=0.5, gate=Gate())


# ── end to end: an eval against its own last run ──────────────────────────


@op(bound="sync")
def classify(text: str = "", broken: bool = False) -> dict:
    if text == "boom":
        raise ValueError("cannot classify")
    label = "refund" if "money back" in text else "other"
    if broken and text.endswith("!"):
        label = "other" if label == "refund" else "refund"
    return {"label": label}


@graph
def flow(text: str = "", broken: bool = False):
    c = classify(text=text, broken=broken)
    START >> c >> END


def _dataset(path: Path, n=40, flip_every=2, tag_first=False) -> Path:
    rows = []
    for i in range(n):
        text = ("I want my money back" if i % 3 else "hello") + ("!" if i % flip_every == 0 else "")
        row = {"id": f"c{i}", "input": text, "expected": {"label": "refund" if i % 3 else "other"}}
        if tag_first and i == 0:
            row["tags"] = ["critical"]
        rows.append(row)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _eval(tmp_path, gate, broken=False, **kw):
    return Eval(
        "labels",
        graph=flow,
        input="text",
        inputs={"broken": broken},
        dataset=kw.pop("dataset", None) or _dataset(tmp_path / "cases.jsonl"),
        evaluators=[exact("label")],
        record_dir=tmp_path / "evals",
        trace=[],
        gate=gate,
        **kw,
    )


def test_an_eval_regresses_against_its_latest_run(tmp_path):
    gate = Gate(threshold=0.5, baseline="latest", tolerance=0.05)
    first = _eval(tmp_path, gate).run_sync()
    g = first.meta["eval"]["gate"]
    assert first.status == "ok" and g["verdict"] == "pass" and g["exit_code"] == 0
    assert "no earlier finished run" in g["warnings"][0]

    second = _eval(tmp_path, gate, broken=True).run_sync()
    g = second.meta["eval"]["gate"]
    assert second.status == "failed" and (g["verdict"], g["exit_code"]) == ("regressed", 1)
    cmp = g["comparison"]
    assert cmp["baseline"] == first.run_id and cmp["cases"] == 40
    by_metric = {t["metric"]: t for t in cmp["tests"]}
    assert by_metric["pass"]["regressed"] == 20 and by_metric["pass"]["gated"]
    assert by_metric["exact(label)"]["gated"] is False and "q_bh" in by_metric["exact(label)"]
    assert "gate=regressed" in second.summary()

    # a regressed run is still a finished run, so it is the next "latest":
    # 20 cases pass and 20 fail in both, and that rules out a 5-point drop
    third = _eval(tmp_path, gate, broken=True).run_sync()
    g = third.meta["eval"]["gate"]
    assert g["comparison"]["baseline"] == second.run_id
    assert (g["verdict"], g["exit_code"], third.status) == ("pass", 0, "ok")
    test = g["comparison"]["tests"][0]
    assert test["ci_lo"] == pytest.approx(newcombe_paired(20, 0, 0, 20)[0], abs=1e-6)

    # 40 cases that all pass in both cannot: Wilson(40/40) leaves ±8.8 points
    fixed = _eval(tmp_path, gate).run_sync()
    assert fixed.meta["eval"]["gate"]["verdict"] == "pass"  # 20 fixed: an improvement
    again = _eval(tmp_path, gate).run_sync().meta["eval"]["gate"]
    assert again["verdict"] == "inconclusive"
    assert again["comparison"]["tests"][0]["ci_lo"] == pytest.approx(
        -(1 - wilson(40, 40)[0]), abs=1e-6
    )


def test_a_named_baseline_and_the_exit_codes_through_the_cli(tmp_path):
    first = _eval(tmp_path, Gate()).run_sync()
    gate = Gate(baseline=first.run_id, tolerance=0.05)
    assert _eval(tmp_path, gate, broken=True).main([]) == 1
    # 4 of 40 flips: p ≈ 0.125, a 5-pt drop not ruled out → inconclusive
    few = _dataset(tmp_path / "few.jsonl", flip_every=10)
    base = _eval(tmp_path, Gate(), dataset=few).run_sync()
    strict = Gate(baseline=base.run_id, tolerance=0.05, strict=True)
    assert _eval(tmp_path, strict, broken=True, dataset=few).main([]) == 2
    loose = Gate(baseline=base.run_id, tolerance=0.05)
    assert _eval(tmp_path, loose, broken=True, dataset=few).main([]) == 0
    missing = Gate(baseline="19990101T000000-000000", tolerance=0.05)
    run = _eval(tmp_path, missing).run_sync()
    assert run.meta["eval"]["gate"]["verdict"] == "error"
    assert "is not under" in run.meta["error"]


def test_infra_errors_exit_3_with_a_gate_and_1_without(tmp_path):
    rows = [
        {"id": f"c{i}", "input": "boom" if i < 3 else "hello", "expected": {"label": "other"}}
        for i in range(20)
    ]
    data = tmp_path / "boom.jsonl"
    data.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    run = _eval(tmp_path, Gate(), dataset=data).run_sync()
    g = run.meta["eval"]["gate"]
    assert (run.status, g["verdict"], g["exit_code"]) == ("failed", "error", 3)
    legacy = Eval(
        "labels",
        graph=flow,
        input="text",
        dataset=data,
        evaluators=[exact("label")],
        record_dir=tmp_path / "evals",
        trace=[],
    )
    assert legacy.main([]) == 1  # 1.9.0: a failed case fails the run, exit 1


def test_without_a_gate_the_1_9_verdict_is_reported_unchanged(tmp_path):
    ev = Eval(
        "labels",
        graph=flow,
        input="text",
        dataset=_dataset(tmp_path / "c.jsonl"),
        evaluators=[exact("label")],
        record_dir=tmp_path / "evals",
        trace=[],
        threshold=0.6,
        inputs={"broken": True},
    )
    run = ev.run_sync()
    s = run.meta["eval"]
    assert (s["cases"], s["passed"], s["failed"], s["pass_rate"]) == (40, 20, 20, 0.5)
    assert run.status == "failed"
    assert s["gate"] == {
        "verdict": "failed",
        "exit_code": 1,
        "reasons": ["pass rate 50.0% < threshold 60.0%"],
    }
    assert "gate=" not in run.summary()


# ── declared in operonx.toml ──────────────────────────────────────────────

MOD = """
from operonx.core import END, START, graph, op

@op(bound="sync")
def classify(text: str = "") -> dict:
    return {"label": "refund" if "money back" in text else "other"}

@graph
def flow(text: str = ""):
    c = classify(text=text)
    START >> c >> END

def label_ok(output=None, expected=None):
    return output["label"] == expected["label"]
"""


def test_a_declared_eval_reads_repeats_cluster_and_its_gate(tmp_path, monkeypatch):
    import warnings

    name = f"ev_{uuid.uuid4().hex[:6]}"
    app = (
        "\nfrom operonx.app import Application, Eval\nfrom operonx.app.evals import Gate\n\n"
        'APP = Application("evdemo", jobs=[Eval("labels", graph=flow, input="text", '
        'dataset="dataset:labels", evaluators=[label_ok], repeats=2, cluster="scenario", '
        'gate=Gate(threshold=0.5, must_pass_tag="smoke"))])\n'
    )
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(MOD) + app, encoding="utf-8")
    (tmp_path / "datasets").mkdir()
    _dataset(tmp_path / "datasets" / "labels.jsonl", n=6)
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname = "evdemo"\napp = "{name}:APP"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            app = Application.find(tmp_path)
        ev = app.job("labels")
        assert ev.repeats == 2 and ev.cluster == "scenario"
        assert ev.gate.thresholds() == {"pass": 0.5} and ev.gate.must_pass_tag == "smoke"
        run = app.run_sync("labels")
        assert run.meta["eval"]["trials"] == 12 and run.meta["eval"]["gate"]["verdict"] == "pass"
        fp = run.meta["eval"]["fingerprint"]
        assert fp["version_dirty"] is None or isinstance(fp["version_dirty"], bool)
    finally:
        sys.modules.pop(name, None)
