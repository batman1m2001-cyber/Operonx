"""``Gate(baseline="main" | "git:<ref>")`` — the experiment of the merge-base (D40).

A real git repository: main holds an eval's code and cases, a branch moves
on. The branch's eval finds main's experiment at ``git merge-base HEAD
<ref>`` through the score store, compares against it, and says which;
experiments it must not use (a dirty tree, an errored run, another eval)
are passed over; when there is none the run does not start.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from operonx.app.evals import Eval, Gate, exact
from operonx.app.evals import job as job_module
from operonx.app.evals.experiments import ExperimentData, load_experiment, merge_base
from operonx.core import END, START, graph, op
from operonx.telemetry.scores import Experiment, ExperimentFilter, open_score_store

RUNS = {"n": 0}


@op(bound="sync")
def classify(text: str = "", broken: bool = False) -> dict:
    RUNS["n"] += 1
    label = "refund" if "money back" in text else "other"
    if broken and text.endswith("!"):
        label = "other" if label == "refund" else "refund"
    return {"label": label}


@graph
def flow(text: str = "", broken: bool = False):
    c = classify(text=text, broken=broken)
    START >> c >> END


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _cases(path: Path, n: int = 40) -> None:
    rows = []
    for i in range(n):
        text = ("I want my money back" if i % 3 else "hello") + ("!" if i % 2 == 0 else "")
        rows.append(
            {"id": f"c{i}", "input": text, "expected": {"label": "refund" if i % 3 else "other"}}
        )
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """main with the cases committed; untracked eval output never dirties it."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "datasets").mkdir()
    _cases(root / "datasets" / "labels.jsonl")
    (root / "notes.txt").write_text("v1\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "main: the eval's cases")
    monkeypatch.setattr(job_module, "_versions", {})  # code_version is read once per process
    RUNS["n"] = 0
    return root


def _commit(root: Path, text: str, monkeypatch) -> str:
    (root / "notes.txt").write_text(text)
    git(root, "commit", "-qam", text)
    monkeypatch.setattr(job_module, "_versions", {})
    return git(root, "rev-parse", "HEAD")[:12]


def _eval(root: Path, store, gate=None, broken=False, name="labels", **kw) -> Eval:
    return Eval(
        name,
        graph=flow,
        input="text",
        inputs={"broken": broken},
        dataset=root / "datasets" / "labels.jsonl",
        evaluators=[exact("label")],
        record_dir=root / "evals",
        root=root,
        trace=[],
        scores=store,
        gate=gate,
        **kw,
    )


GATE = Gate(baseline="git:main", tolerance=0.05)


def test_the_branch_compares_against_mains_experiment(repo, monkeypatch, tmp_path):
    store = open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    on_main = _eval(repo, store).run_sync()
    main_sha = git(repo, "rev-parse", "HEAD")[:12]
    assert on_main.meta["eval"]["fingerprint"]["code_version"] == main_sha

    git(repo, "checkout", "-qb", "feature")
    _commit(repo, "feature work", monkeypatch)
    _commit(repo, "more feature work", monkeypatch)
    # main moves on after the branch: the merge-base is still the old commit
    git(repo, "checkout", "-q", "main")
    newer_main = _commit(repo, "main moves on", monkeypatch)
    git(repo, "checkout", "-q", "feature")
    monkeypatch.setattr(job_module, "_versions", {})
    assert merge_base(repo, "main") == main_sha != newer_main

    run = _eval(repo, store, GATE, broken=True).run_sync()
    gate = run.meta["eval"]["gate"]
    assert gate["comparison"]["baseline"] == on_main.run_id
    assert gate["comparison"]["baseline_ref"] == f"git:main @ {main_sha}"
    assert (gate["verdict"], gate["exit_code"]) == ("regressed", 1)
    test = gate["comparison"]["tests"][0]
    assert (test["regressed"], test["fixed"]) == (20, 0)  # the 20 cases ending in "!"


def test_main_is_origin_main(repo, monkeypatch, tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    store = open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    on_main = _eval(repo, store).run_sync()
    git(repo, "checkout", "-qb", "feature")
    _commit(repo, "feature work", monkeypatch)
    run = _eval(repo, store, Gate(baseline="main", tolerance=0.05)).run_sync()
    comparison = run.meta["eval"]["gate"]["comparison"]
    assert comparison["baseline"] == on_main.run_id
    assert comparison["baseline_ref"].startswith("git:origin/main @ ")


def test_what_cannot_be_a_baseline_is_passed_over(repo, monkeypatch, tmp_path):
    store = open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    good = _eval(repo, store).run_sync()
    sha = good.meta["eval"]["fingerprint"]["code_version"]
    exp = store.get_experiment(good.run_id).experiment
    # newer, but each unusable: a dirty tree, an errored run, another eval, still running
    for i, change in enumerate(
        [
            {"version_dirty": True},
            {"gate": {"verdict": "error", "exit_code": 3}},
            {"eval": "other"},
            {"ended_at": None, "status": "running"},
        ]
    ):
        fields = {**exp.to_dict(), "experiment_id": f"z{i}", "started_at": exp.started_at + 10 + i}
        fields.update(change)
        store.put_experiment(Experiment.from_dict(fields))
    git(repo, "checkout", "-qb", "feature")
    _commit(repo, "feature", monkeypatch)
    run = _eval(repo, store, GATE).run_sync()
    assert run.meta["eval"]["gate"]["comparison"]["baseline"] == good.run_id
    assert sha == merge_base(repo, "main")


def test_the_same_dataset_version_is_preferred(repo, monkeypatch, tmp_path):
    store = open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    full = _eval(repo, store).run_sync()
    exp = store.get_experiment(full.run_id).experiment
    # newer at the same commit, over other cases (a --cases slice, say)
    store.put_experiment(
        Experiment.from_dict(
            {
                **exp.to_dict(),
                "experiment_id": "slice",
                "dataset_version": "0" * 12,
                "started_at": exp.started_at + 60,
            }
        )
    )
    git(repo, "checkout", "-qb", "feature")
    _commit(repo, "feature", monkeypatch)
    run = _eval(repo, store, GATE).run_sync()
    assert run.meta["eval"]["gate"]["comparison"]["baseline"] == full.run_id


def test_another_dataset_version_is_used_when_nothing_better_with_a_warning(
    repo, monkeypatch, tmp_path
):
    store = open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    base = _eval(repo, store).run_sync()
    git(repo, "checkout", "-qb", "feature")
    _cases(repo / "datasets" / "labels.jsonl", n=50)  # the branch adds ten cases
    git(repo, "commit", "-qam", "more cases")
    monkeypatch.setattr(job_module, "_versions", {})
    run = _eval(repo, store, GATE).run_sync()
    gate = run.meta["eval"]["gate"]
    assert gate["comparison"]["baseline"] == base.run_id and gate["comparison"]["cases"] == 40
    assert any("dataset_version differs" in w for w in gate["warnings"])


def test_no_experiment_at_the_merge_base_stops_before_any_case(repo, monkeypatch, tmp_path):
    store = open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    git(repo, "checkout", "-qb", "feature")
    _commit(repo, "feature", monkeypatch)
    sha = merge_base(repo, "main")
    with pytest.raises(ValueError) as err:
        _eval(repo, store, GATE).run_sync()
    text = str(err.value)
    assert sha in text and "git:main" in text and "labels" in text
    assert "Run the eval on main first" in text
    assert RUNS["n"] == 0  # nothing ran
    assert not (repo / "evals" / "labels").exists()  # no record was opened


def test_an_unknown_ref_says_how_to_fetch_it(repo, tmp_path):
    store = open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    with pytest.raises(ValueError, match=r"git merge-base HEAD origin/main.*git fetch"):
        _eval(repo, store, Gate(baseline="main", tolerance=0.05)).run_sync()


def test_without_scores_the_projects_store_is_read(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("OPERONX_RUNS_DIR", str(tmp_path / "runs"))
    (repo / "operonx.toml").write_text('[project]\nname = "p"\n')
    project = open_score_store({"backend": "files", "root": str(tmp_path / "runs" / "scores")})
    on_main = _eval(repo, project).run_sync()
    git(repo, "checkout", "-qb", "feature")
    _commit(repo, "feature", monkeypatch)
    run = _eval(repo, None, GATE).run_sync()
    assert run.meta["eval"]["gate"]["comparison"]["baseline"] == on_main.run_id


def test_an_experiment_reads_the_same_from_its_record_and_the_store(repo, tmp_path):
    store = open_score_store({"backend": "files", "root": str(tmp_path / "scores")})
    run = _eval(repo, store, Gate(threshold=0.9), broken=True).run_sync()
    from_record = load_experiment(run.run_id, record_dirs=[repo / "evals"])
    from_store = load_experiment(run.run_id, store=store)
    assert isinstance(from_store, ExperimentData)
    assert from_record.source.startswith("record") and from_store.source.startswith("store")
    for d in (from_record, from_store):
        assert d.experiment_id == run.run_id and d.eval == "labels"
        assert d.summary["metrics"] == run.meta["eval"]["metrics"]
        assert d.summary["gate"]["verdict"] == "failed"
        assert (
            d.summary["fingerprint"]["dataset_version"]
            == (run.meta["eval"]["fingerprint"]["dataset_version"])
        )
    a, b = from_record.outcomes(), from_store.outcomes()
    assert a.keys() == b.keys() and len(a) == 40
    for case in a:
        assert (a[case].passed, a[case].checks, a[case].case_hash) == (
            b[case].passed,
            b[case].checks,
            b[case].case_hash,
        )
    # by path too
    assert load_experiment(run.path).experiment_id == run.run_id
    with pytest.raises(ValueError, match="no experiment 'nope'"):
        load_experiment("nope", store=store, record_dirs=[repo / "evals"])
