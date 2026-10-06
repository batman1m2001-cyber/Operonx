"""`operonx run`: a Job by import path or by its application's name, and an
exit status cron or CI can read (0 ok, 1 an item or step failed, 2 could
not start)."""

from __future__ import annotations

import json
import sys
import textwrap
import uuid

import pytest

from operonx.cli.run import main

MODULE = """
from operonx.app import Application
from operonx.app.jobs import Job
from operonx.app.serve import egress, ingress
from operonx.core import END, START, graph, op


@op(bound="sync")
def shout(item: dict = None) -> dict:
    if item.get("bad"):
        raise ValueError("bad item")
    return {"reply": {"id": item["id"], "text": item["text"] + "!"}}


@graph
def flow():
    src = ingress()
    loud = shout(item=src["item"])
    out = egress(item=loud["reply"])
    START >> src >> loud >> out >> END


@op(bound="sync")
def count(results: list) -> dict:
    return {"n": len(results)}


@graph
def counted(results):
    c = count(results=results)
    START >> c >> END


ITEMS = [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}, {"id": "c", "text": "z"}]

job = Job("shout", graph=flow, items=ITEMS, key="id", reduce=counted,
          description="says it louder")
failing = Job("shout_bad", graph=flow, items=ITEMS + [{"id": "z", "text": "", "bad": True}],
              key="id")
not_a_job = 42
nightly = Job("nightly", steps=[job, failing], description="both, in order")
from_file = Job("from_file", graph=flow, items="data.jsonl", key="id", output="out.jsonl")

APP = Application("demo", jobs=[job, failing, nightly, from_file])
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project whose module declares the jobs and the application, as the cwd."""
    name = f"demo_jobs_{uuid.uuid4().hex[:6]}"
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(MODULE), encoding="utf-8")
    (tmp_path / "data.jsonl").write_text(
        "".join(json.dumps({"id": i, "text": i}) + "\n" for i in "abc"), encoding="utf-8"
    )
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname = "demo"\nsrc = ["."]\napp = "{name}:APP"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    yield name, tmp_path
    sys.modules.pop(name, None)
    if str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))


def test_runs_a_job_by_path_and_exits_zero_when_every_item_is_ok(project, capsys):
    name, root = project
    code = main([f"{name}:job", "--record-dir", str(root / "jobs")])
    out = capsys.readouterr().out
    assert code == 0
    assert "shout" in out and "ok=3 failed=0" in out
    assert str(root / "jobs" / "shout") in out


def test_a_failed_item_is_named_and_the_exit_status_says_so(project, capsys):
    name, root = project
    code = main([f"{name}:failing", "--record-dir", str(root / "jobs")])
    out = capsys.readouterr().out
    assert code == 1
    assert "ok=3 failed=1" in out
    assert "failed z: loud: ValueError: bad item" in out


def test_resume_reaches_the_runner(project, capsys):
    name, root = project
    main([f"{name}:failing", "--record-dir", str(root / "jobs")])
    main([f"{name}:failing", "--record-dir", str(root / "jobs"), "--resume"])
    assert "skipped=3" in capsys.readouterr().out  # the three that were fine


def test_show_prints_what_would_run_and_runs_nothing(project, capsys):
    name, root = project
    code = main([f"{name}:job", "--show", "--record-dir", str(root / "jobs")])
    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0] == "shout"
    assert "graph        flow" in out and "reduce       counted" in out
    assert "says it louder" in out
    assert not (root / "jobs").exists()


def test_bad_targets_are_named_on_stderr(project, capsys):
    name, _ = project
    assert main([f"{name}:nope"]) == 2
    assert "nope" in capsys.readouterr().err
    assert main([f"{name}:not_a_job"]) == 2
    assert "not a Job" in capsys.readouterr().err
    assert main(["no_such_module:job"]) == 2
    assert "no_such_module" in capsys.readouterr().err
    assert main(["not-a-ref"]) == 2


def test_a_job_runs_by_its_application_name_under_the_project(project, capsys):
    _, root = project
    code = main(["from_file"])  # the application found from the cwd
    out = capsys.readouterr().out
    assert code == 0 and "from_file " in out and "ok=3 failed=0" in out
    assert (root / ".operonx" / "jobs" / "from_file").is_dir()
    assert len((root / "out.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_items_and_set_override_a_run(project, capsys):
    _, root = project
    (root / "two.jsonl").write_text('{"id": "q", "text": "w"}\n', encoding="utf-8")
    assert main(["from_file", "--items", "two.jsonl"]) == 0
    assert "ok=1 failed=0" in capsys.readouterr().out
    assert main(["from_file", "--items", "two.csv"]) == 2
    assert ".jsonl" in capsys.readouterr().err
    assert main(["from_file", "--set", "nokey"]) == 2


def test_list_prints_every_job(project, capsys):
    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "demo"
    assert "shout " in out and "-> reduce counted" in out and "says it louder" in out
    assert "nightly" in out and "steps" in out and "shout -> shout_bad" in out
    assert "data.jsonl -> flow" in out


def test_an_unknown_name_and_a_missing_manifest_are_named(project, capsys, tmp_path_factory):
    assert main(["nope"]) == 2
    assert "no job named 'nope'" in capsys.readouterr().err
    elsewhere = tmp_path_factory.mktemp("empty")
    assert main(["shout", "-f", str(elsewhere / "operonx.toml")]) == 2
    with pytest.raises(SystemExit):  # argparse: name a job, or --list
        main([])


def test_a_job_of_steps_reports_each_step(project, capsys):
    _, root = project
    code = main(["nightly"])
    out = capsys.readouterr().out
    assert code == 1
    assert "nightly " in out and "ok=1 failed=1" in out
    assert "failed shout_bad" in out
    assert (root / ".operonx" / "jobs" / "nightly").is_dir()
    assert (root / ".operonx" / "jobs" / "shout").is_dir()  # each step keeps its own record


def test_a_job_of_steps_refuses_one_jobs_settings(project, capsys):
    assert main(["nightly", "--items", "data.jsonl"]) == 2
    assert "run that step instead" in capsys.readouterr().err


def test_a_toml_job_block_is_refused_with_where_to_go(tmp_path, capsys):
    (tmp_path / "operonx.toml").write_text(
        '[project]\nname = "old"\n\n[[job]]\nname = "x"\ngraph = "m:f"\n', encoding="utf-8"
    )
    assert main(["x", "-f", str(tmp_path / "operonx.toml")]) == 2
    assert "app/main.py" in capsys.readouterr().err
