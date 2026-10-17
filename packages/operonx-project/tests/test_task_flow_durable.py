"""P3 gate: a parked card resumes through the journal from a new engine; a
card killed mid-round resumes without re-running a round that had ended."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from operonx_project.store import Store
from tests.flow_env import RIGHT, Env, impl

PKG = Path(__file__).resolve().parents[1]


async def test_park_then_resume_from_a_new_engine(tmp_path):
    ask = {"output": {"summary": "stuck", "needs_human": {
        "question": "Should add accept strings?", "why": "the criteria do not say", "category": "requirements",
        "options": [{"key": "A", "label": "numbers only"}, {"key": "B", "label": "strings too"}]}}}  # fmt: skip
    script = {
        "implementer": [ask, impl(RIGHT)],
        "triage": [{"output": {"action": "escalate", "category": "requirements",
                               "message": {"problem": "Should add accept strings?", "why": "not in the criteria",
                                           "options": [{"key": "A", "label": "numbers only"},
                                                       {"key": "B", "label": "strings too"}],
                                           "recommended": "A"}}}],
    }  # fmt: skip
    log = tmp_path / "calls.jsonl"
    env = Env(tmp_path, script, log=log)
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "waiting_human"
    assert out.question["problem"] == "Should add accept strings?" and out.question["interrupt_id"]
    env.store.update_card("m1", "C1", detail={**env.card().detail, "question": out.question}, run_id=out.run_id)
    env.store.close()

    # another process: a new store connection, runtime, engine; the same journal
    env2 = Env(tmp_path, script, log=log, repo=env.repo)
    assert env2.runner.engine is not env.runner.engine
    out = await env2.runner.resume(env2.card(), {"choice": "A", "text": "keep it simple"}, env2.stage)
    assert out.status == "verified", out
    impl_calls = env2.coder.of("implementer")
    assert len(impl_calls) == 2
    assert "The owner's decision: numbers only (keep it simple)" in impl_calls[1]["prompt"]
    assert impl_calls[1]["session_in"] == impl_calls[0]["session"]
    assert len(env2.coder.of("test_author")) == 1 and len(env2.coder.of("triage")) == 1


async def test_triage_answers_from_evidence_without_asking(tmp_path):
    ask = {"output": {"summary": "?", "needs_human": {"question": "Which file?", "category": "requirements"}}}
    env = Env(tmp_path, {
        "implementer": [ask, impl(RIGHT)],
        "triage": [{"output": {"action": "answer", "answer": "calc.py, per the card", "evidence": ["calc.py"]}}],
    })  # fmt: skip
    out = await env.runner.run(env.card(), env.stage)
    assert out.status == "verified"
    assert "calc.py, per the card" in env.coder.of("implementer")[1]["prompt"]


def _drive(root: Path, scenario: str, mode: str) -> int:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    p = subprocess.run([sys.executable, "-m", "tests.crash_driver", str(root), scenario, mode], cwd=PKG, env=env,
                       capture_output=True, text=True, timeout=300)  # fmt: skip
    if p.returncode not in (0, -signal.SIGKILL):
        raise AssertionError(p.stdout + p.stderr)
    return p.returncode


def _calls(root: Path):
    return [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]


@pytest.mark.slow
def test_kill_9_mid_implement_resumes_without_rerunning_rounds(tmp_path):
    assert _drive(tmp_path, "implement", "run") == -signal.SIGKILL
    assert not (tmp_path / "outcome.json").exists()
    assert _drive(tmp_path, "implement", "recover") == 0
    assert json.loads((tmp_path / "outcome.json").read_text())["status"] == "verified"
    calls = _calls(tmp_path)
    roles = [c["role"] for c in calls]
    assert roles.count("test_author") == 1 and roles.count("examiner") == 1  # prepare: not run again
    impl_calls = [c for c in calls if c["role"] == "implementer"]
    assert [c["i"] for c in impl_calls] == [0, 1, 2, 3]  # rounds 1 and 2 not re-run; round 3 twice
    assert impl_calls[3]["session_in"] == impl_calls[0]["session"]  # resumed by the stored session id
    rounds = [e["data"]["round"] for e in Store(tmp_path / "state" / "project.db").events("m1") if e["kind"] == "round"]
    assert rounds == [1, 2, 3]


@pytest.mark.slow
def test_kill_9_mid_review_keeps_the_ended_implement(tmp_path):
    assert _drive(tmp_path, "review", "run") == -signal.SIGKILL
    assert _drive(tmp_path, "review", "recover") == 0
    assert json.loads((tmp_path / "outcome.json").read_text())["status"] == "verified"
    roles = [c["role"] for c in _calls(tmp_path)]
    assert roles.count("implementer") == 2  # round 2's implement had ended: its checkpoint is used
    assert roles.count("reviewer") == 2
