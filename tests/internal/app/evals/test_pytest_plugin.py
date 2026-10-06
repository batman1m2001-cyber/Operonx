"""The opt-in pytest plugin: a session is one experiment (EVALS_PLAN D46).

Driven with ``pytester``: each test writes a small test file and runs
pytest on it in-process, with and without ``-p operonx.app.evals.pytest_plugin``.
"""

from __future__ import annotations

import json
from importlib.metadata import entry_points
from pathlib import Path

import pytest
import xmlschema

# imported here, so the in-process runs share one copy (pytester drops the
# modules a run imports, and operonx's registries are process-wide)
import operonx.app.evals.pytest_plugin  # noqa: F401

pytest_plugins = ["pytester"]

PLUGIN = "operonx.app.evals.pytest_plugin"
SCHEMA = xmlschema.XMLSchema(str(Path(__file__).with_name("junit-10.xsd")))

FLOW = """
import pytest

from operonx.app.evals import exact
from operonx.app.evals.pytest_plugin import cases
from operonx.core import END, START, graph, op


@op(bound="sync")
def classify(text: str = "") -> dict:
    return {"label": "refund" if "money back" in text else "other"}


@graph
def flow(text: str = ""):
    c = classify(text=text, name="classify")
    START >> c >> END
"""

CASES = [
    {
        "id": "refund",
        "input": "my money back",
        "expected": {"label": "refund"},
        "tags": ["critical"],
    },
    {"id": "hello", "input": "hello", "expected": {"label": "other"}},
    {"id": "wrong", "input": "hello again", "expected": {"label": "refund"}},
]


@pytest.fixture
def project(pytester):
    pytester.makeini(
        "[pytest]\nasyncio_mode = auto\nasyncio_default_fixture_loop_scope = function\n"
        "filterwarnings = ignore::pytest.PytestAssertRewriteWarning\n"
    )
    (pytester.path / "datasets").mkdir()
    (pytester.path / "datasets" / "labels.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in CASES)
    )
    return pytester


def _record(root: Path, name: str = "pytest"):
    runs = sorted((root / "evals" / name).iterdir())
    run = runs[-1]
    meta = json.loads((run / "run.json").read_text())
    items = [json.loads(x) for x in (run / "items.jsonl").read_text().splitlines()]
    return run, meta, items, len(runs)


def test_it_is_never_auto_loaded(project):
    assert not [ep for ep in entry_points(group="pytest11") if ep.value.startswith("operonx")], (
        "operonx must not register a pytest11 entry point"
    )
    project.makepyfile(
        test_x=FLOW
        + """
async def test_one(run_case):
    await run_case(flow, {"input": "hi"}, input="text")
"""
    )
    got = project.runpytest()
    got.assert_outcomes(errors=1)
    assert "fixture 'run_case' not found" in got.stdout.str()
    assert not (project.path / "evals").exists()


def test_a_session_is_one_experiment_and_verdicts_are_outcomes(project):
    project.makepyfile(
        test_labels=FLOW
        + """
@pytest.mark.parametrize("case", cases("dataset:labels"))
async def test_label(case, run_case):
    got = await run_case(flow, case, evaluators=[exact("label")], input="text")
    assert got.trace.path() == ["classify"]


def test_plain_tests_are_not_items():
    assert True
"""
    )
    got = project.runpytest("-p", PLUGIN)
    got.assert_outcomes(passed=3, failed=1)
    out = got.stdout.str()
    # the failing check is the failing test, with why
    assert "test_label[wrong]" in out and "operonx eval: exact(label)" in out
    run, meta, items, n = _record(project.path)
    assert n == 1  # one session, one experiment
    assert [i["key"] for i in items] == [
        "test_labels.py::test_label[refund]",
        "test_labels.py::test_label[hello]",
        "test_labels.py::test_label[wrong]",
    ]
    ev = meta["eval"]
    assert (ev["cases"], ev["passed"], ev["failed"]) == (3, 2, 1)
    assert ev["gate"]["verdict"] == "failed" and meta["variant"] == "pytest"
    assert meta["kind"] == "eval" and meta["graph"] == "flow"
    assert items[0]["verdict"]["tags"] == ["critical"] and items[0]["verdict"]["case_hash"]
    assert ev["fingerprint"]["graph_hash"] and ev["fingerprint"]["dataset_version"]
    assert "operonx eval pytest" in out and f"{run.name}" in out and "gate=failed" in out


def test_a_failing_assert_is_recorded(project):
    project.makepyfile(
        test_a=FLOW
        + """
async def test_path(run_case):
    got = await run_case(flow, {"id": "x", "input": "hi"}, input="text")
    assert got.trace.path() == ["classify", "answer"], "the answer step never ran"
"""
    )
    got = project.runpytest("-p", PLUGIN)
    got.assert_outcomes(failed=1)
    _, meta, (item,), _ = _record(project.path)
    check = item["verdict"]["checks"]["assert"]
    assert check["passed"] is False and "the answer step never ran" in check["reason"]
    assert item["verdict"]["passed"] is False and meta["eval"]["failed"] == 1


def test_sync_tests_check_and_why(project):
    project.makepyfile(
        test_s=FLOW
        + """
def test_sync(run_case):
    got = run_case.sync(flow, {"id": "s", "input": "hello", "expected": {"label": "other"}},
                        input="text")
    assert got.output == {"label": "other"} and got.status == "ok"
    assert got.check(exact("label"))
    assert not got.check(lambda output=None: {"passed": False, "reason": "nope"})
    assert got.why == "<lambda>: nope"
    got.checks.pop("<lambda>")


def test_twice(run_case):
    run_case.sync(flow, {"input": "a"}, input="text")
    run_case.sync(flow, {"input": "b"}, input="text")
"""
    )
    got = project.runpytest("-p", PLUGIN)
    got.assert_outcomes(passed=1, failed=1)
    assert "one test is one case" in got.stdout.str()


def test_reports_and_the_store(project):
    project.makepyfile(
        test_r=FLOW
        + """
@pytest.mark.parametrize("case", cases("dataset:labels", ids=["refund", "hello"]))
async def test_label(case, run_case):
    await run_case(flow, case, evaluators=[exact("label")], input="text")
"""
    )
    got = project.runpytest(
        "-p", PLUGIN,
        "--operonx-eval-name", "smoke",
        "--operonx-eval-report", "md,junit",
        "--operonx-eval-out", "out",
        "--operonx-eval-store",
    )  # fmt: skip
    got.assert_outcomes(passed=2)
    run, meta, items, _ = _record(project.path, "smoke")
    md = (project.path / "out" / "report.md").read_text()
    assert md.startswith("## Eval `smoke`: PASS (exit 0)")
    SCHEMA.validate((project.path / "out" / "junit.xml").read_text())
    from operonx.telemetry.scores import open_score_store

    store = open_score_store(
        {"backend": "files", "root": str(project.path / ".operonx" / "runs" / "scores")}
    )
    stored = store.get_experiment(run.name)
    assert stored is not None and len(stored.items) == 2 and stored.experiment.variant == "pytest"


def test_the_gate_can_fail_a_passing_session(project):
    project.makepyfile(
        test_g=FLOW
        + """
@pytest.mark.parametrize("case", cases("dataset:labels", ids=["refund", "hello"]))
async def test_label(case, run_case):
    await run_case(flow, case, evaluators=[exact("label")], input="text")
"""
    )
    project.runpytest("-p", PLUGIN).assert_outcomes(passed=2)
    gated = ("--operonx-eval-baseline", "latest", "--operonx-eval-tolerance", "0.01")
    loose = project.runpytest("-p", PLUGIN, *gated)
    loose.assert_outcomes(passed=2)
    assert loose.ret == 0 and "gate=inconclusive" in loose.stdout.str()
    strict = project.runpytest("-p", PLUGIN, *gated, "--operonx-eval-strict")
    strict.assert_outcomes(passed=2)
    assert strict.ret == 1  # two cases cannot rule out a 1-point drop: 2 under strict
    _, meta, _, n = _record(project.path)
    assert n == 3 and meta["eval"]["gate"]["exit_code"] == 2
