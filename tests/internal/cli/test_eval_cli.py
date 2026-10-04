"""`operonx eval` — the exit-code matrix and every subcommand (EVALS_PLAN D36–D47).

Each test is a scratch project with a declared eval whose behaviour the
environment switches (``BROKEN``: half the answers wrong; ``BOOM``: every
case raises), driven through ``operonx.cli.main.main`` in-process.
"""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from operonx.app.evals import job as job_module
from operonx.cli.main import main
from operonx.telemetry.scores import ExperimentFilter, open_score_store

pytestmark = pytest.mark.unit

LABELS = """
import os

from operonx.core import END, START, graph, op


@op(bound="sync")
def classify(text: str = "") -> dict:
    if os.environ.get("BOOM"):
        raise ValueError("the endpoint is down")
    label = "refund" if "money back" in text else "other"
    if os.environ.get("BROKEN") and text.endswith("!"):
        label = "other" if label == "refund" else "refund"
    return {"label": label}


@graph
def flow(text: str = ""):
    c = classify(text=text)
    START >> c >> END
"""

CHECKS = """
from operonx.app.evals import exact

label = exact("label")


def strict_label(output=None, expected=None):
    return output == expected
"""


def _toml(gate: str = "threshold = 0.5") -> str:
    return f"""[project]
name = "demo"

[[job]]
name       = "labels"
graph      = "labels:flow"
item_input = "text"
dataset    = "dataset:labels"
evaluators = ["checks:label"]

[job.gate]
{gate}
"""


def _cases(n: int = 40):
    rows = []
    for i in range(n):
        text = ("I want my money back" if i % 3 else "hello") + ("!" if i % 2 == 0 else "")
        row = {"id": f"c{i}", "input": text, "expected": {"label": "refund" if i % 3 else "other"}}
        row["split"] = "smoke" if i < 10 else "full"
        rows.append(row)
    return rows


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    (root / "datasets").mkdir(parents=True)
    (root / "labels.py").write_text(LABELS)
    (root / "checks.py").write_text(CHECKS)
    (root / "operonx.toml").write_text(_toml())
    (root / "datasets" / "labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in _cases()))
    monkeypatch.chdir(root)
    monkeypatch.delenv("OPERONX_RUNS_DIR", raising=False)
    monkeypatch.delenv("BROKEN", raising=False)
    monkeypatch.delenv("BOOM", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in ("labels", "checks"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(job_module, "_versions", {})
    yield root
    for name in ("labels", "checks"):
        sys.modules.pop(name, None)


def _eval(*argv: str) -> int:
    return main(["eval", *argv])


def _store(root: Path):
    return open_score_store(
        {"backend": "files", "root": str(root / ".operonx" / "runs" / "scores")}
    )


def _runs(root: Path):
    base = root / "evals" / "labels"
    return sorted(p.name for p in base.iterdir()) if base.is_dir() else []


# ── the exit-code matrix ──────────────────────────────────────────────────


def test_pass_exits_0_and_the_experiment_is_stored(project, capsys):
    assert _eval("run", "labels") == 0
    out = capsys.readouterr().out
    (run_id,) = _runs(project)
    assert f"labels {run_id} ok" in out and "gate=pass" in out
    store = _store(project)
    (exp,) = store.list_experiments(ExperimentFilter(eval="labels")).items
    assert exp.experiment_id == run_id and exp.cases == 40
    assert f"experiment stored in files at {project / '.operonx' / 'runs' / 'scores'}" in out


def test_failed_exits_1(project, monkeypatch, capsys):
    (project / "operonx.toml").write_text(_toml("threshold = 0.9"))
    monkeypatch.setenv("BROKEN", "1")
    assert _eval("run", "labels") == 1
    assert "gate: pass: 50.0% < threshold 90.0%" in capsys.readouterr().out


def test_regressed_exits_1(project, monkeypatch, capsys):
    assert _eval("run", "labels") == 0
    monkeypatch.setenv("BROKEN", "1")
    assert _eval("run", "labels", "--baseline", "latest", "--tolerance", "0.05") == 1
    out = capsys.readouterr().out
    assert "gate=regressed" in out and "pass: -50.0 pts" in out


def test_inconclusive_exits_2_under_strict_and_0_without(project, capsys):
    assert _eval("run", "labels") == 0
    args = ("run", "labels", "--baseline", "latest", "--tolerance", "0.01")
    assert _eval(*args, "--strict") == 2
    assert "gate=inconclusive" in capsys.readouterr().out
    assert _eval(*args) == 0


def test_an_infrastructure_error_exits_3(project, monkeypatch, capsys):
    monkeypatch.setenv("BOOM", "1")
    assert _eval("run", "labels") == 3
    out = capsys.readouterr().out
    assert "gate=error" in out and "an infrastructure failure" in out


def test_what_cannot_run_exits_2(project, capsys):
    assert _eval("run", "nope") == 2
    assert "no job named 'nope'" in capsys.readouterr().err
    assert _eval("run", "labels", "--tolerance", "0.05") == 2  # a tolerance needs a baseline
    assert "--tolerance compares against a baseline" in capsys.readouterr().err
    assert _eval("run", "labels", "--report", "html") == 2
    assert "no report format ['html']" in capsys.readouterr().err
    with pytest.raises(SystemExit) as bad:
        _eval("run", "labels", "--repeats", "many")
    assert bad.value.code == 2


def test_an_unopenable_store_is_an_error_unless_no_store(project, capsys):
    (project / "operonx.toml").write_text(_toml() + '\n[evals]\nscores = "score_store:team"\n')
    (project / "resources.yaml").write_text(
        "score_store:\n  team:\n    backend: clickhouse\n    host: ${CH_HOST_UNSET}\n"
    )
    assert _eval("run", "labels") == 2
    err = capsys.readouterr().err
    assert "${CH_HOST_UNSET}" in err and "--no-store" in err
    assert _runs(project) == []  # nothing ran
    assert _eval("run", "labels", "--no-store") == 0


# ── run's flags ─────────────────────────────────────────────────────────────


def test_selection_repeats_variant_and_reports(project, capsys):
    out_dir = project / "out"
    code = _eval(
        "run", "labels", "--split", "smoke", "--repeats", "2", "--variant", "v2",
        "--report", "md,json,junit", "--out", str(out_dir), "--no-store",
    )  # fmt: skip
    assert code == 0
    (run_id,) = _runs(project)
    run = json.loads((project / "evals" / "labels" / run_id / "run.json").read_text())
    ev = run["eval"]
    assert (ev["cases"], ev["trials"], ev["repeats"]) == (10, 20, 2)
    assert ev["selection"] == {"split": "smoke"} and ev["variant"] == "v2"
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "experiment.json",
        "junit.xml",
        "report.md",
    ]
    assert (out_dir / "report.md").read_text().startswith("## Eval `labels`: PASS (exit 0)")
    ET.fromstring((out_dir / "junit.xml").read_text())
    assert not (project / ".operonx" / "runs" / "scores").exists()  # --no-store
    out = capsys.readouterr().out
    assert f"report: {out_dir / 'report.md'}" in out


def test_cases_tags_and_sample(project):
    assert _eval("run", "labels", "--cases", "c1,c2", "--no-store") == 0
    assert _eval("run", "labels", "--sample", "5", "--no-store") == 0
    first, second = _runs(project)
    items = [
        json.loads(line)["key"]
        for line in (project / "evals" / "labels" / first / "items.jsonl").read_text().splitlines()
    ]
    assert sorted(items) == ["c1", "c2"]
    assert (
        len((project / "evals" / "labels" / second / "items.jsonl").read_text().splitlines()) == 5
    )
    assert _eval("run", "labels", "--cases", "zz", "--no-store") == 2


def test_reports_default_to_the_record_directory(project):
    assert _eval("run", "labels", "--report", "junit", "--no-store") == 0
    (run_id,) = _runs(project)
    assert (project / "evals" / "labels" / run_id / "junit.xml").is_file()


# ── the other commands ──────────────────────────────────────────────────────


def test_report_and_compare(project, monkeypatch, capsys):
    assert _eval("run", "labels") == 0
    monkeypatch.setenv("BROKEN", "1")
    assert _eval("run", "labels") == 0  # threshold 0.5: 50% passes
    a, b = _runs(project)
    capsys.readouterr()

    assert _eval("report", b) == 0
    assert capsys.readouterr().out.startswith("## Eval `labels`: PASS (exit 0)")
    assert _eval("report", b, "--format", "junit", "--out", "j.xml") == 0
    assert ET.parse(project / "j.xml").getroot().tag == "testsuites"
    assert _eval("report", str(project / "evals" / "labels" / a), "--format", "json") == 0
    assert json.loads(capsys.readouterr().out)["experiment_id"] == a

    assert _eval("compare", a, b) == 0  # no tolerance: reported, not judged
    out = capsys.readouterr().out
    assert out.startswith(f"## Compare `{a}` → `{b}`") and "not judged" in out
    assert _eval("compare", a, b, "--tolerance", "0.05") == 1
    assert "REGRESSED (exit 1)" in capsys.readouterr().out
    assert _eval("compare", a, b, "--tolerance", "0.05", "--format", "json") == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "regressed"


def test_report_reads_the_store_when_the_record_is_elsewhere(project, capsys):
    assert _eval("run", "labels") == 0
    (run_id,) = _runs(project)
    import shutil

    shutil.rmtree(project / "evals")  # as on a machine that did not run it
    capsys.readouterr()
    assert _eval("report", run_id) == 0
    assert "## Eval `labels`: PASS" in capsys.readouterr().out
    assert _eval("report", "nope") == 2
    assert "no experiment 'nope'" in capsys.readouterr().err


def test_rescore(project, capsys):
    assert _eval("run", "labels", "--no-store") == 0
    (run_id,) = _runs(project)
    capsys.readouterr()
    assert _eval("rescore", run_id, "--evaluators", "checks:strict_label", "--no-store") == 0
    out = capsys.readouterr().out
    assert f"rescored {run_id}" in out and "strict_label: 40/40" in out
    assert _eval("rescore", run_id, "--no-store") == 0  # the eval's own evaluators
    assert "exact(label): 40/40" in capsys.readouterr().out


def test_calibrate(project, capsys):
    out_dir = project / "cal"
    code = _eval(
        "calibrate", "labels", "--runs", "2", "--simulations", "20", "--tolerance", "0.5",
        "--out", str(out_dir),
    )  # fmt: skip
    assert code == 0
    out = capsys.readouterr().out
    assert "2 runs" in out and "repeats  tolerance" in out and "recommended: 1 repeat" in out
    got = json.loads((out_dir / "calibration.json").read_text())
    assert got["runs"] == 2 and got["cases"] == 40 and got["recommended_repeats"] == 1
    assert len(_store(project).list_experiments(ExperimentFilter(eval="labels")).items) == 2
    # again from the stored experiments, without running
    ids = ",".join(got["experiments"])
    assert _eval("calibrate", "labels", "--experiments", ids, "--simulations", "10") == 0
    assert _eval("calibrate", "labels", "--experiments", got["experiments"][0]) == 2


def test_power(project, monkeypatch, capsys):
    assert _eval("power", "labels", "--delta", "0.05", "--discordance", "0.10") == 0
    out = capsys.readouterr().out
    assert "312 cases" in out and "has 40 cases" in out
    assert _eval("power", "labels", "--delta", "0.05") == 2  # nothing to measure yet
    assert "--discordance" in capsys.readouterr().err
    assert _eval("run", "labels") == 0
    monkeypatch.setenv("BROKEN", "1")
    assert _eval("run", "labels") == 0
    assert _eval("power", "labels", "--delta", "0.2") == 0
    out = capsys.readouterr().out
    assert "discordance 50.0% measured" in out


def test_list(project, capsys):
    assert _eval("list") == 0
    out = capsys.readouterr().out
    assert "labels" in out and "40 cases" in out and "no run yet" in out
    assert _eval("run", "labels", "--no-store") == 0
    capsys.readouterr()
    assert _eval("list") == 0
    assert "last: pass" in capsys.readouterr().out


def test_dataset_commands(project, capsys):
    assert _eval("dataset", "validate", "labels") == 0
    assert "40 cases, no problems" in capsys.readouterr().out
    bad = project / "datasets" / "bad.jsonl"
    bad.write_text('{"id": "a", "input": 1}\n{"id": "a", "input": 2}\n')
    assert _eval("dataset", "validate", "bad") == 1
    assert f"{bad}:2: duplicate id 'a' (first on line 1)" in capsys.readouterr().out
    assert _eval("dataset", "stats", "labels") == 0
    out = capsys.readouterr().out
    assert "cases     40" in out and "smoke 10" in out and "full 30" in out


def test_dataset_diff_against_git(project, capsys):
    def git(*a):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
            cwd=project,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-qm", "cases")
    rows = _cases()
    rows[0]["expected"] = {"label": "refund"}
    rows = rows[1:] + [rows[0], {"id": "new", "input": "x"}]
    del rows[5]
    (project / "datasets" / "labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert _eval("dataset", "diff", "labels") == 0
    out = capsys.readouterr().out
    assert "added 1: new" in out and "removed 1: c6" in out and "changed 1: c0" in out


def test_baseline_main_end_to_end_in_git(project, monkeypatch, capsys):
    def git(*a):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
            cwd=project, check=True, capture_output=True, text=True,
        ).stdout.strip()  # fmt: skip

    (project / ".gitignore").write_text(".operonx/\nevals/\n")
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-qm", "main")
    git("checkout", "-qb", "feature")
    (project / "notes.txt").write_text("work")
    git("add", "notes.txt")
    git("commit", "-qm", "feature")
    monkeypatch.setattr(job_module, "_versions", {})
    args = ("run", "labels", "--baseline", "git:main", "--tolerance", "0.05")
    assert _eval(*args) == 2  # main's experiment was never stored
    assert "Run the eval on main first" in capsys.readouterr().err
    assert _runs(project) == []  # nothing ran

    git("checkout", "-q", "main")
    monkeypatch.setattr(job_module, "_versions", {})
    assert _eval("run", "labels") == 0  # main's pipeline: stored by default
    git("checkout", "-q", "feature")
    monkeypatch.setattr(job_module, "_versions", {})
    monkeypatch.setenv("BROKEN", "1")
    assert _eval(*args) == 1
    out = capsys.readouterr().out
    assert "gate=regressed" in out and "git:main @ " in out
