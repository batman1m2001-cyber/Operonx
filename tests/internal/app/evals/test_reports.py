"""Reports: Markdown, JSON and JUnit XML of an experiment (EVALS_PLAN D42).

The JUnit XML is validated against the Jenkins xunit ``junit-10.xsd``
(vendored beside this file, MIT) — the shape GitLab's widget reads — and
its counts are checked by hand. A record and its rows in the store give
the same reports.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import xmlschema

from operonx.app.evals import Eval, Gate, exact
from operonx.app.evals.experiments import ExperimentData, load_experiment
from operonx.app.evals.report import (
    as_json,
    compare_markdown,
    junit,
    markdown,
    parse_formats,
    write_reports,
)
from operonx.app.evals.stats import wilson
from operonx.core import END, START, graph, op
from operonx.telemetry.scores import open_score_store

SCHEMA = xmlschema.XMLSchema(str(Path(__file__).with_name("junit-10.xsd")))


@op(bound="sync")
def classify(text: str = "", broken: bool = False) -> dict:
    if text == "boom":
        raise ValueError("cannot classify")
    label = "refund" if "money back" in text else "other"
    if broken and text.endswith("!"):
        label = "other" if label == "refund" else "refund"
    return {"label": label, "note": "a | pipe\nand a newline \x01"}


@graph
def flow(text: str = "", broken: bool = False):
    c = classify(text=text, broken=broken)
    START >> c >> END


def _data(path: Path, n=40, boom=0) -> Path:
    rows = []
    for i in range(n):
        text = (
            "boom"
            if i < boom
            else ("I want my money back" if i % 3 else "hello") + ("!" if i % 2 == 0 else "")
        )
        rows.append(
            {"id": f"c{i}", "input": text, "expected": {"label": "refund" if i % 3 else "other"}}
        )
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _run(tmp_path, gate=None, broken=False, evaluators=None, scores=None, **kw):
    return Eval(
        "labels",
        graph=flow,
        input="text",
        inputs={"broken": broken},
        dataset=kw.pop("dataset", None) or _data(tmp_path / "cases.jsonl"),
        evaluators=[exact("label")] if evaluators is None else evaluators,
        record_dir=tmp_path / "evals",
        trace=[],
        gate=gate,
        scores=scores,
        **kw,
    ).run_sync()


def _suite(xml: str):
    SCHEMA.validate(xml)  # raises with the line and the rule on any violation
    root = ET.fromstring(xml)
    (suite,) = root.findall("testsuite")
    return root, suite


def _outcome(tc) -> str:
    for kind in ("failure", "error", "skipped"):
        if tc.find(kind) is not None:
            return kind
    return "passed"


def test_a_regressed_run_reports_why_first(tmp_path):
    gate = Gate(baseline="latest", tolerance=0.05)
    first = _run(tmp_path, gate)
    run = _run(tmp_path, gate, broken=True, variant="prompt v2")
    md = markdown(load_experiment(run))
    lines = md.splitlines()
    assert lines[0] == "## Eval `labels`: REGRESSED (exit 1)"
    assert f"experiment `{run.run_id}`" in lines[2] and "40 cases" in lines[2]
    assert "variant “prompt v2”" in lines[2]
    assert lines[4] == "**Why**" and lines[6].startswith("- pass: -50.0 pts")
    lo, hi = wilson(20, 40)  # 35.2% – 64.8%
    assert "### Metrics" in md and f"| pass | 50.0% | {lo:.1%} – {hi:.1%} | 40 | wilson |" in md
    assert f"### Against `{first.run_id}` — 40 shared cases" in md
    assert "| pass (gated) | 100.0% | 50.0% | -50.0 pts |" in md
    assert "| exact(label) | 100.0% | 50.0% | -50.0 pts |" in md and "exploratory" in md
    assert "Flips: 20 regressed (`c0`, `c2`," in md and "changed, not verified" in md
    assert "### Failing cases (20 of 40) — the first 10" in md
    # an output with a pipe and a newline stays in its cell
    row = next(line for line in lines if line.startswith("| `c0` |"))
    assert row.count(" | ") == 3 and "a \\| pipe" in row
    assert "Cost and latency: p50" in md


def test_junit_of_a_regressed_run_validates_and_counts(tmp_path):
    gate = Gate(baseline="latest", tolerance=0.05)
    _run(tmp_path, gate)
    run = _run(tmp_path, gate, broken=True)
    root, suite = _suite(junit(load_experiment(run)))
    cases = suite.findall("testcase")
    # the gate, then one per case × check: 1 + 40
    assert len(cases) == 41 and suite.get("tests") == "41"
    assert (cases[0].get("name"), _outcome(cases[0])) == ("gate", "failure")
    assert cases[0].find("failure").get("type") == "regressed"
    assert suite.get("failures") == "21" and suite.get("errors") == "0"  # gate + 20 cases
    assert root.get("failures") == "21"
    c0 = next(c for c in cases if c.get("classname") == "labels.c0")
    assert c0.get("name") == "exact(label)" and _outcome(c0) == "failure"
    assert c0.find("failure").get("message") == "0/1 repeats passed"
    props = {p.get("name"): p.get("value") for p in suite.find("properties")}
    assert props["verdict"] == "regressed" and props["exit_code"] == "1"
    assert props["experiment"] == run.run_id


def test_junit_of_errors_flaky_repeats_and_inconclusive(tmp_path):
    data = _data(tmp_path / "boom.jsonl", n=10, boom=1)
    run = _run(tmp_path, Gate(max_error_rate=0.5), dataset=data, repeats=2)
    _, suite = _suite(junit(load_experiment(run)))
    by = {(c.get("classname"), c.get("name")): c for c in suite.findall("testcase")}
    assert _outcome(by[("labels", "gate")]) == "passed"  # 10% errored, under 50%
    assert _outcome(by[("labels.c0", "run")]) == "error"
    assert by[("labels.c0", "run")].find("error").get("message") == "2/2 runs errored"
    assert ("labels.c0", "exact(label)") not in by  # no check ran on it
    assert _outcome(by[("labels.c1", "exact(label)")]) == "passed"
    assert suite.get("errors") == "1"

    # inconclusive: skipped, unless strict
    base = _run(tmp_path, None)
    loose = _run(tmp_path, Gate(baseline=base.run_id, tolerance=0.01))
    _, suite = _suite(junit(load_experiment(loose)))
    gate_case = suite.findall("testcase")[0]
    assert loose.meta["eval"]["gate"]["verdict"] == "inconclusive"
    assert _outcome(gate_case) == "skipped" and suite.get("skipped") == "1"
    strict = _run(tmp_path, Gate(baseline=base.run_id, tolerance=0.01, strict=True))
    _, suite = _suite(junit(load_experiment(strict)))
    assert _outcome(suite.findall("testcase")[0]) == "failure"


def test_junit_of_a_flaky_case_says_flaky(tmp_path):
    items = [
        {
            "key": f"a#{r}",
            "case": "a",
            "repeat": r,
            "status": "ok",
            "passed": ok,
            "ms": 12.5,
            "checks": {"exact": {"passed": ok, "reason": None if ok else "said x"}},
        }
        for r, ok in enumerate([True, False, True])
    ]
    exp = ExperimentData(
        "e1",
        "flaky",
        "ok",
        "2026-10-04T10:00:00+00:00",
        None,
        {"repeats": 3, "gate": {"verdict": "pass", "exit_code": 0}},
        items,
    )
    _, suite = _suite(junit(exp))
    tc = suite.findall("testcase")[1]
    assert tc.find("failure").get("message") == "2/3 repeats passed (flaky)"
    assert tc.find("failure").text == "repeat 1: said x" and tc.get("time") == "0.013"


def test_an_eval_without_checks_has_one_pass_testcase_per_case(tmp_path):
    run = _run(tmp_path, None, evaluators=[], dataset=_data(tmp_path / "few.jsonl", n=3))
    _, suite = _suite(junit(load_experiment(run)))
    names = [(c.get("classname"), c.get("name")) for c in suite.findall("testcase")]
    assert names == [
        ("labels", "gate"),
        ("labels.c0", "pass"),
        ("labels.c1", "pass"),
        ("labels.c2", "pass"),
    ]


def test_json_is_the_experiment_as_data(tmp_path):
    run = _run(tmp_path, Gate(threshold=0.9), broken=True)
    exp = load_experiment(run)
    got = json.loads(as_json(exp))
    assert got == json.loads(json.dumps(exp.as_dict(), default=str))
    assert got["summary"]["gate"]["verdict"] == "failed" and len(got["items"]) == 40


def test_a_record_and_the_store_report_the_same(tmp_path):
    store = open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    gate = Gate(baseline="latest", tolerance=0.05)
    _run(tmp_path, gate, scores=store)
    run = _run(tmp_path, gate, broken=True, scores=store)
    record = load_experiment(run.run_id, record_dirs=[tmp_path / "evals"])
    stored = load_experiment(run.run_id, store=store)
    assert markdown(record) == markdown(stored)
    assert junit(record) == junit(stored)


def test_write_reports_and_formats(tmp_path):
    run = _run(tmp_path, None)
    paths = write_reports(load_experiment(run), "md,json,junit", tmp_path / "out")
    assert sorted(p.name for p in paths.values()) == ["experiment.json", "junit.xml", "report.md"]
    _suite((tmp_path / "out" / "junit.xml").read_text())
    with pytest.raises(ValueError, match="no report format \\['html'\\]"):
        parse_formats("md,html")


def test_a_comparison_as_markdown(tmp_path):
    from operonx.app.evals.compare import compare

    a = load_experiment(_run(tmp_path, None))
    b = load_experiment(_run(tmp_path, None, broken=True))
    md = compare_markdown(compare(a, b, tolerance=0.05))
    assert (
        md.splitlines()[0]
        == f"## Compare `{a.experiment_id}` → `{b.experiment_id}`: REGRESSED (exit 1)"
    )
    assert "| pass (gated) | 100.0% | 50.0% | -50.0 pts |" in md
    plain = compare_markdown(compare(a, b))
    assert "REGRESSED" not in plain and "not judged" in plain
