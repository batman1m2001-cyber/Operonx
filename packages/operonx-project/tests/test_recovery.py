"""P5: the crash matrix rows P3 does not cover (plan §10), with the daemon killed by SIGKILL.

Each test runs the daemon in its own process (``tests.life_daemon``) with a
scripted coder whose calls are logged across processes, kills it at one
point, and starts it again: what had ended is not done again, and the
milestone ends as if nothing had happened.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from operonx_project.model import MilestoneState as M
from operonx_project.model import Policy
from operonx_project.store import Store
from tests.flow_env import PY, RIGHT, git
from tests.life_env import SURVEY, TESTS, base_script, card_json, plan

ROOT = Path(__file__).resolve().parents[1]


LEASE = 1.0  # a killed daemon's lease expires after this; the next one waits it out


def daemon(root: Path, script, commands=(), policy=None) -> int:
    killed = root / "last-was-killed"
    if killed.exists():
        time.sleep(LEASE + 0.2)
        killed.unlink()
    cfg = root / "config.json"
    pol = (policy or Policy.w1(heldout=False, approve_plan="auto")).to_dict()
    cfg.write_text(json.dumps({"root": str(root), "script": script, "log": str(root / "calls.jsonl"),
                               "policy": pol, "commands": list(commands), "lease_ttl": LEASE}))  # fmt: skip
    proc = subprocess.run([sys.executable, "-m", "tests.life_daemon", str(cfg)], cwd=ROOT, capture_output=True,
                          text=True, timeout=600)  # fmt: skip
    if proc.returncode not in (0, -9):
        raise AssertionError(proc.stderr[-3000:])
    if proc.returncode == -9:
        killed.write_text("")
    return proc.returncode


def calls(root: Path, key: str):
    rows = [json.loads(x) for x in (root / "calls.jsonl").read_text().splitlines()]
    return [r for r in rows if r.get("key") == key or r["role"] == key]


def state(root: Path) -> M:
    store = Store(root / "proj" / ".operonx" / "project" / "project.db")
    try:
        return store.get_milestone("m1").state
    finally:
        store.close()


def script_json(**over):
    s = base_script(**over)
    s.pop("examiner", None)  # held-out tests off: the scripts must be JSON
    return s


def test_killed_while_planning_resumes_the_stage_not_the_milestone(tmp_path):
    script = script_json(**{"planner:plan": [{"die": True}, plan([card_json("C1")])]})
    assert daemon(tmp_path, script, commands=[["start", None, None]]) == -9
    assert state(tmp_path) == M.PLANNING
    assert daemon(tmp_path, script) == 0
    assert state(tmp_path) == M.DONE
    # the survey ran once; the killed plan call ran again (it had not ended), once
    assert len(calls(tmp_path, "planner:survey")) == 1 and len(calls(tmp_path, "planner:plan")) == 2


def test_an_answer_survives_a_crash_between_it_and_the_resume(tmp_path):
    ask = {"output": {"summary": "stuck", "needs_human": {"question": "Plus or minus?", "category": "requirements",
                                                          "options": [{"key": "a", "label": "plus"}]}}}  # fmt: skip
    script = script_json(implementer=[ask, {"die": True}, {"write": {"calc.py": RIGHT}}])
    assert daemon(tmp_path, script, commands=[["start", None, None]]) == 0
    assert state(tmp_path) == M.WAITING_HUMAN
    # waiting: a restart changes nothing and calls no one
    before = len(calls(tmp_path, "implementer"))
    assert daemon(tmp_path, script) == 0 and state(tmp_path) == M.WAITING_HUMAN
    assert len(calls(tmp_path, "implementer")) == before
    # the answer is applied, the resumed round dies; the restart resumes with the answer
    assert daemon(tmp_path, script, commands=[["answer", {"answer": "a"}, "C1"]]) == -9
    assert daemon(tmp_path, script) == 0
    assert state(tmp_path) == M.DONE
    after = calls(tmp_path, "implementer")
    assert len(after) == 3 and "The owner's decision: plus" in after[2]["prompt"]
    store = Store(tmp_path / "proj" / ".operonx" / "project" / "project.db")
    cmds = store._db.execute("SELECT kind, status FROM commands").fetchall()
    assert [tuple(c) for c in cmds] == [("start", "done"), ("answer", "done")]


def test_killed_while_merging_redoes_the_merge_once(tmp_path):
    marker = tmp_path / "killed"
    killer = f"{PY} {ROOT / 'tests' / 'killer.py'} {marker} && {TESTS}"
    survey = {"output": {**SURVEY["output"], "commands": [{"name": "tests", "cmd": killer, "purpose": "test"}]}}
    script = script_json(**{"planner:survey": [survey]})
    assert daemon(tmp_path, script, commands=[["start", None, None]]) == -9
    assert marker.exists() and state(tmp_path) == M.RUNNING
    store = Store(tmp_path / "proj" / ".operonx" / "project" / "project.db")
    assert store.get_card("m1", "C1").state.value == "verified"
    store.close()
    assert daemon(tmp_path, script) == 0
    assert state(tmp_path) == M.DONE
    integ = tmp_path / "worktrees" / "proj" / "m1" / "_integration"
    log = git(integ, "log", "--format=%s")
    assert log.count("merge card C1") == 1 and not (integ / ".git").is_dir()
    assert len(calls(tmp_path, "implementer")) == 1  # the card's work was not redone
