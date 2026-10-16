"""P9: the bounds and the waits — a rate limit pauses the milestone until its reset,
``max_hours`` stops new work like the dollar budget, and the checks run in
docker with no network when the policy says so."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time

import pytest

from operonx_project.daemon import Daemon
from operonx_project.interactions import Interactions
from operonx_project.model import MilestoneState as M
from operonx_project.model import Policy
from operonx_project.runtime import Runtime
from tests.flow_env import ACCEPTANCE, PY, RIGHT, author_step, examiner_step, impl
from tests.life_env import DOUBLE, SURVEY, Life, base_script, card_json, double_author, plan


def states(life):
    return [(e["data"]["from"], e["data"]["to"], e["actor"]) for e in life.store.events("m1", limit=100_000)
            if e["kind"] == "milestone.state"]  # fmt: skip


def test_a_long_rate_limit_pauses_the_milestone_and_a_timer_resumes_it(tmp_path):
    def limited(req, i):
        return {"status": "rate_limited", "resets_at": time.time() + 1.5}

    life = Life(tmp_path, base_script(implementer=[limited, impl(RIGHT)]),
                policy=Policy.w1(heldout=False, approve_plan="auto"), hold_max_s=0.5)  # fmt: skip
    life.command("start")
    t0 = time.time()
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    assert time.time() - t0 >= 1.0  # it waited for the reset
    moves = states(life)
    assert ("running", "paused", "engine") in moves and ("paused", "running", "engine") in moves
    paused = [e for e in life.store.events("m1", limit=100_000) if e["kind"] == "milestone.state"
              and e["data"]["to"] == "paused"][0]  # fmt: skip
    assert paused["data"]["reason"].startswith("rate limited: the coder's limit resets at")
    [notice] = [e for e in life.store.events("m1", limit=100_000) if e["kind"] == "notice"]
    assert "paused: the coder's rate limit resets at" in notice["data"]["text"]
    assert life.store.get_milestone("m1").resume_at is None
    assert len(life.coder.of("implementer")) == 2


def test_a_short_rate_limit_is_held_without_pausing(tmp_path):
    def limited(req, i):
        return {"status": "rate_limited", "resets_at": time.time() + 0.3}

    life = Life(tmp_path, base_script(implementer=[limited, impl(RIGHT)]),
                policy=Policy.w1(heldout=False, approve_plan="auto"))  # fmt: skip
    life.command("start")
    assert life.serve() == M.DONE
    assert not any(to == "paused" for _, to, _ in states(life))


async def _serve_until(life, daemon, pred, timeout=60.0):
    stop = asyncio.Event()
    task = asyncio.ensure_future(daemon.serve(stop=stop))
    t0 = time.time()
    while not pred() and time.time() - t0 < timeout:
        await asyncio.sleep(0.05)
    stop.set()
    await task


def test_the_owner_pausing_during_a_rate_pause_holds_it_past_the_reset(tmp_path):
    def limited(req, i):
        return {"status": "rate_limited", "resets_at": time.time() + 1.0}

    life = Life(tmp_path, base_script(implementer=[limited, impl(RIGHT)]),
                policy=Policy.w1(heldout=False, approve_plan="auto"), hold_max_s=0.2)  # fmt: skip
    life.command("start")
    d = Daemon(life.life, poll_s=0.02, lease_ttl=1)
    asyncio.run(_serve_until(life, d, lambda: life.store.get_milestone("m1").resume_at is not None))
    assert life.state() == M.PAUSED
    life.command("pause")
    asyncio.run(_serve_until(life, Daemon(life.life, poll_s=0.02, lease_ttl=1),
                             lambda: not life.store.pending_commands()))  # fmt: skip
    time.sleep(1.2)  # past the reset
    asyncio.run(Daemon(life.life, poll_s=0.02, lease_ttl=1).serve(once=True))
    ms = life.store.get_milestone("m1")
    assert ms.state == M.PAUSED and ms.reason == "paused by the owner" and ms.resume_at is None
    life.command("unpause")
    assert life.serve() == M.DONE


def test_max_hours_stops_new_work_and_an_extension_goes_on(tmp_path):
    def slow(req, i):
        time.sleep(1.5)
        return impl(RIGHT)

    second = card_json(
        "C2",
        title="double a number",
        intent="calc.double doubles",
        deps=["C1"],
        criteria=[{"row": "", "text": "WHEN double(x) is called THE SYSTEM SHALL return 2x"}],
    )
    script = base_script(**{"planner:plan": [plan([card_json("C1"), second])],
                            "test_author": [author_step("C1"), double_author()],
                            "implementer": [slow, impl(DOUBLE)]})  # fmt: skip
    life = Life(tmp_path, script, policy=Policy.w1(heldout=False, max_parallel=1))
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL  # the plan's wait does not count; one second of running is left
    ms = life.store.get_milestone("m1")
    ms.policy.budget.max_hours = (life.store.running_seconds("m1") + 1.0) / 3600
    life.store.update_milestone("m1", policy=ms.policy)
    life.command("approve")
    assert life.serve() == M.BUDGET_EXHAUSTED
    ms = life.store.get_milestone("m1")
    assert ms.reason.startswith("ran ") and " h of " in ms.reason
    assert life.cards() == {"C1": "done", "C2": "ready"}  # C1 ended its round; C2 never started
    inter = Interactions(life.store, project=life.project)
    inter.sync()
    [q] = inter.open("m1")
    assert q["kind"] == "budget" and q["payload"]["what"] == "hours" and "h of" in q["payload"]["problem"]
    assert inter.submit_answer(q["id"], "+1h", via="cli").ok
    assert life.serve() == M.DONE
    assert life.cards() == {"C1": "done", "C2": "done"}
    assert life.store.get_milestone("m1").policy.budget.max_hours > 1.0


def test_waiting_for_the_owner_does_not_count_toward_max_hours(tmp_path):
    life = Life(tmp_path, base_script())
    s = life.store
    s.move_milestone("m1", M.PLANNING)
    time.sleep(0.3)
    s.move_milestone("m1", M.AWAITING_APPROVAL)
    time.sleep(0.5)
    s.move_milestone("m1", M.RUNNING)
    time.sleep(0.2)
    s.move_milestone("m1", M.WAITING_HUMAN)
    time.sleep(0.5)
    spent = s.running_seconds("m1")
    assert 0.45 <= spent <= 0.7, spent


def test_a_host_check_runs_the_projects_python_not_whatever_path_has(tmp_path):
    life = Life(tmp_path, base_script())
    rt: Runtime = life.life.rt("m1")
    check = rt.test_check("acceptance", "python -m pytest -q tests")
    assert check.cmd.startswith(str(rt.target_python())) and check.cmd.endswith("-m pytest -q tests")
    rt.check_mode, rt.check_image = "docker", "python:3.11-slim"
    assert rt.test_check("acceptance", "python -m pytest -q tests").cmd == "python -m pytest -q tests"


CHECK_IMAGE = "operonx-project-check:py311-pytest"


def _check_image() -> str:
    have = subprocess.run(["docker", "image", "inspect", CHECK_IMAGE], capture_output=True)
    if have.returncode != 0:
        subprocess.run(["docker", "build", "-q", "-t", CHECK_IMAGE, "-"], check=True, capture_output=True,
                       input=b"FROM python:3.11-slim\nRUN pip install -q pytest\n", timeout=600)  # fmt: skip
    return CHECK_IMAGE


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("docker") is None, reason="no docker")
def test_docker_checks_run_a_milestones_acceptance_and_heldout_tests(tmp_path):
    image = _check_image()
    # the acceptance test passes only inside a container: on the host the card would fail
    inside = ACCEPTANCE + "\n\ndef test_in_a_container():\n    import os\n\n    assert os.getcwd() == '/work'\n"
    path = "tests/acceptance/C1/test_c1_add.py"
    survey = {
        "output": {
            **SURVEY["output"],
            "commands": [  # the repo's own check, on the host: not the C1 tests
                {"name": "tests", "cmd": f"{PY} -m pytest -q -p no:cacheprovider tests/test_base.py", "purpose": "test"}
            ],
        }
    }
    script = base_script(examiner=[examiner_step(None)], test_author=[{"write": {path: inside},
                                                                       "output": {"files": [path]}}],
                         **{"planner:survey": [survey]})  # fmt: skip
    life = Life(tmp_path, script, policy=Policy.w1(approve_plan="auto"),
                runtime={"check_mode": "docker", "check_image": image, "check_timeout_s": 300.0})  # fmt: skip
    life.command("start")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    c1 = life.store.get_card("m1", "C1")
    assert c1.detail["heldout"]["files"] and life.cards() == {"C1": "done"}  # the held-out tests ran mounted
    assert not list(life.integ().rglob("__pycache__"))  # the container wrote no bytecode into the worktree
