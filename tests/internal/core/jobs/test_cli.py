"""`operonx-run`: a Job by import path, an exit status a cron can read."""

from __future__ import annotations

import sys
import textwrap
import uuid

import pytest

from operonx.cli.run import main

MODULE = '''
from operonx.core import END, START, graph, op
from operonx.core.jobs import Job
from operonx.core.serve import egress, ingress


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


ITEMS = [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}, {"id": "c", "text": "z"}]

job = Job("shout", graph=flow, source=ITEMS, key="id", description="says it louder")
failing = Job("shout_bad", graph=flow, source=ITEMS + [{"id": "z", "text": "", "bad": True}], key="id")
not_a_job = 42
'''


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project directory with a module declaring jobs, as the cwd."""
    name = f"demo_jobs_{uuid.uuid4().hex[:6]}"
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(MODULE), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    yield name, tmp_path
    sys.modules.pop(name, None)
    if str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))


def test_runs_a_job_and_exits_zero_when_every_item_is_ok(project, capsys):
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
    out = capsys.readouterr().out
    assert "skipped=3" in out                        # the three that were fine


def test_show_prints_what_would_run_and_runs_nothing(project, capsys):
    name, root = project
    code = main([f"{name}:job", "--show", "--record-dir", str(root / "jobs")])
    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0] == "shout"
    assert "graph        flow" in out and "says it louder" in out
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
