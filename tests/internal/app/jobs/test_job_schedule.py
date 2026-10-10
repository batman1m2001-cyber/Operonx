"""A schedule starts a Job (DOORLESS_SERVICES_PLAN S2, gate G2).

``Job(schedule=schedule(...))``: the job's clock runs inside the server on
the schedule's port. Each tick is one fresh job run, with per-item records
and the tick in the run's record; the clock's rules are the schedule
door's: a tick while the last run is going is skipped and counted, and a
failing run does not stop the clock.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from starlette.testclient import TestClient

from operonx import END, START, graph, op
from operonx.app import Application, Service, http, schedule
from operonx.app.declare import describe_service
from operonx.app.jobs import Job

pytestmark = pytest.mark.unit

SEEN: list = []
MODE = {"fail": False, "slow": 0.0}


@pytest.fixture(autouse=True)
def _clear():
    SEEN.clear()
    MODE.update(fail=False, slow=0.0)
    yield
    SEEN.clear()


@op
async def check(policy: str) -> dict:
    if MODE["slow"]:
        await asyncio.sleep(MODE["slow"])
    SEEN.append(policy)
    if MODE["fail"]:
        raise RuntimeError("the policy store is down")
    return {"policy": policy, "ok": True}


@graph
def check_policy(policy):
    c = check(policy=policy)
    START >> c >> END


def _policies():
    return ["p1", "p2"]


def _job(tmp_path, every=0.1, port=8931, **kw):
    return Job(
        "policy_sweep",
        graph=check_policy,
        items=_policies,
        key=str,
        schedule=schedule(every=every, port=port),
        record_dir=tmp_path,
        **kw,
    )


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _clock(asgi_app):
    return next(r for r in asgi_app.state.operonx_runners if hasattr(r, "job"))


def test_each_tick_is_a_job_run_with_per_item_records(tmp_path):
    app = Application("t", jobs=[_job(tmp_path)]).asgi()
    with TestClient(app):
        clock = _clock(app)
        assert _wait(lambda: len(clock.runs) >= 2)
    first = clock.runs[0]
    assert first.status == "ok" and first.counts["ok"] == 2
    assert set(first.results) == {"p1", "p2"}
    trigger = json.loads((first.path / "run.json").read_text())["trigger"]
    assert trigger["by"] == "schedule" and trigger["slot"] and trigger["at"]
    assert clock.runs[0].run_id != clock.runs[1].run_id  # a fresh run per tick, never resumed


def test_a_tick_during_a_slow_run_is_skipped_and_counted(tmp_path):
    MODE["slow"] = 0.3
    app = Application("t", jobs=[_job(tmp_path, every=0.05, port=8932)]).asgi()
    with TestClient(app):
        clock = _clock(app)
        assert _wait(lambda: len(clock.runs) >= 1)
    assert clock.skipped > 0
    assert SEEN.count("p1") == len(clock.runs) or SEEN.count("p1") == len(clock.runs) + 1


def test_a_failing_run_does_not_stop_the_clock(tmp_path):
    MODE["fail"] = True
    app = Application("t", jobs=[_job(tmp_path, port=8933)]).asgi()
    with TestClient(app):
        clock = _clock(app)
        assert _wait(lambda: len(clock.runs) >= 2)
    assert [r.status for r in clock.runs[:2]] == ["failed", "failed"]
    assert clock.failed >= 2


def test_a_job_whose_run_raises_does_not_stop_the_clock(tmp_path):
    def broken():
        raise OSError("the source is gone")

    job = Job(
        "broken",
        graph=check_policy,
        items=broken,
        schedule=schedule(every=0.05, port=8934),
        record_dir=tmp_path,
    )
    app = Application("t", jobs=[job]).asgi()
    with TestClient(app):
        clock = _clock(app)
        assert _wait(lambda: clock.failed >= 2)


def test_the_clock_runs_beside_the_ports_services(tmp_path):
    @op
    def pong() -> dict:
        return {"pong": True}

    @graph
    def ping():
        p = pong()
        START >> p >> END

    service = Service("ping", http("POST", "/ping", port=8935), graph=ping)
    app = Application("t", services=[service], jobs=[_job(tmp_path, port=8935)]).asgi()
    with TestClient(app) as client:
        assert client.post("/ping").json() == {"pong": True}
        assert _wait(lambda: len(_clock(app).runs) >= 1)


def test_a_scheduled_job_still_runs_on_demand(tmp_path):
    app = Application("t", jobs=[_job(tmp_path, every="1h", port=8936)])
    run = app.run_sync("policy_sweep")
    assert run.status == "ok"
    assert "trigger" not in json.loads((run.path / "run.json").read_text())


def test_describe_shows_the_schedule(tmp_path):
    app = Application("t", jobs=[_job(tmp_path, every="1h", port=8937)])
    d = app.describe()
    job = next(j for j in d["jobs"] if j["name"] == "policy_sweep")
    assert job["schedule"] == {"every": "1h", "port": 8937}
    clock = next(s for s in d["services"] if s["name"] == "policy_sweep")
    assert clock["kind"] == "schedule" and clock["job"] == "policy_sweep"
    assert describe_service(app.service("policy_sweep"))["job"] == "policy_sweep"


def test_schedule_must_be_a_schedule_listener():
    with pytest.raises(TypeError, match="schedule"):
        Job("j", graph=check_policy, schedule=http("POST", "/x"))


def test_two_jobs_on_the_same_clock_time_and_port(tmp_path):
    a = Job("a", graph=check_policy, items=["x"], schedule=schedule(at="07:00", port=8938))
    b = Job("b", graph=check_policy, items=["y"], schedule=schedule(at="07:00", port=8938))
    app = Application("t", jobs=[a, b])
    assert {s.name for s in app.services} == {"a", "b"}
