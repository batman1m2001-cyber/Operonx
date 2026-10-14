"""`[durable]` in operonx.toml: the project's jobs and services journal their
runs (docs/N8N_LANGGRAPH_GAP_PLAN.md L2).

* a job item retried (`Job(retry=)`) or resumed (`--resume`) continues from
  its failed op: what ended before the failure is not done again;
* a request-response service's run that stops for a person replies with its
  run id and questions, and `POST <path>/resume` answers it;
* a graph with a door is never journalled; without `[durable]` nothing changes.
"""

from __future__ import annotations

import sys
import textwrap
import uuid

import pytest

from operonx import END, START, graph, op
from operonx.app import Application, ManifestError
from operonx.app.jobs.job import _compile
from operonx.app.manifest import DurableSpec, Manifest, _toml
from operonx.app.serve import ingress
from operonx.durable import MemoryJournal

pytestmark = pytest.mark.unit


def _manifest(text: str) -> Manifest:
    return Manifest.from_dict(_toml.loads(textwrap.dedent(text)))


def test_durable_is_parsed():
    m = _manifest('[project]\nname = "x"\n[durable]\njournal = "sqlite:.operonx/j.db"\n')
    assert (m.durable.journal, m.durable.durability) == ("sqlite:.operonx/j.db", "async")
    assert _manifest('[project]\nname = "x"\n').durable is None


@pytest.mark.parametrize(
    "table, match",
    [
        ('journal = "postgres://x"', "journal is"),
        ('journal = "sqlite:"', "journal is"),
        ('journal = "memory"\ndurability = "later"', "durability is one of"),
        ('journal = "memory"\nkeep = 3', "takes journal and durability"),
    ],
)
def test_a_mistake_fails_at_load(table, match):
    with pytest.raises(ManifestError, match=match):
        _manifest(f'[project]\nname = "x"\n[durable]\n{table}\n')


def test_one_journal_per_file(tmp_path):
    a = DurableSpec("sqlite:j.db", root=tmp_path).open()
    b = DurableSpec("sqlite:j.db", root=tmp_path).open()
    assert a is b and a.path == tmp_path / "j.db"


MODULE = """
from operonx.app import Application, Service, http
from operonx.app.jobs import Job
from operonx.core import END, START, InterruptOp, graph, op
from operonx.core.policy import Retry

CALLS = []
BROKEN = {"on": True}


@op
async def charge(order: int) -> dict:
    CALLS.append("charge")
    return {"receipt": f"R-{order}"}


@op
async def ship(receipt: str) -> dict:
    CALLS.append("ship")
    if BROKEN["on"]:
        BROKEN["on"] = BROKEN.get("again", False)
        raise RuntimeError("the courier is down")
    return {"done": f"{receipt}:shipped"}


@graph
def order(order):
    c = charge(order=order)
    s = ship(receipt=c["receipt"])
    START >> c >> s >> END


@op
async def plan(x: int) -> dict:
    return {"plan": f"refund {x}"}


@op
async def act(response: str, plan: str) -> dict:
    return {"done": f"{plan}: {response}"}


@graph
def refund(x):
    p = plan(x=x)
    ask = InterruptOp(payload=p["plan"])
    a = act(response=ask["response"], plan=p["plan"])
    START >> p >> ask >> a >> END


APP = Application(
    "demo",
    services=[Service("refund", http("POST", "/refund", port=8475), graph=refund)],
    jobs=[
        Job("orders", graph=order, items=[{"order": 7}], key="order",
            retry=Retry(max_attempts=2, initial=0.001, jitter=False)),
        Job("once", graph=order, items=[{"order": 8}], key="order"),
    ],
    resources=None,
    src=["."],
)
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    name = f"dur_{uuid.uuid4().hex[:8]}"
    (tmp_path / f"{name}.py").write_text(MODULE, encoding="utf-8")
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname = "demo"\nsrc = ["."]\napp = "{name}:APP"\n\n'
        '[durable]\njournal = "sqlite:.operonx/journal.db"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path, name
    sys.modules.pop(name, None)


def _module(name):
    return sys.modules[name]


async def test_a_retried_item_continues_from_its_failed_op(project):
    root, name = project
    app = Application.find(root)
    assert app.job("orders").engine()._journal is not None
    run = await app.run("orders")
    mod = _module(name)
    assert run.status == "ok", run
    assert mod.CALLS == ["charge", "ship", "ship"]  # charged once


async def test_resume_finishes_a_failed_item_without_redoing_what_ended(project):
    root, name = project
    app = Application.find(root)
    first = await app.run("once")
    mod = _module(name)
    assert first.counts.get("failed") == 1 and mod.CALLS == ["charge", "ship"]
    mod.BROKEN["on"] = False  # the courier is back
    second = await app.run("once", resume=True)
    assert second.counts.get("ok") == 1
    assert mod.CALLS == ["charge", "ship", "ship"]  # charged once, across both runs


def test_a_parked_service_run_is_answered_at_resume(project):
    from starlette.testclient import TestClient

    root, _ = project
    app = Application.find(root)
    with TestClient(app.asgi()) as client:
        parked = client.post("/refund", json={"x": 40}).json()
        assert parked["run_id"] and parked["interrupted"][0]["payload"] == "refund 40"
        answer = {
            "run_id": parked["run_id"],
            "answers": {parked["interrupted"][0]["id"]: "approved"},
        }
        done = client.post("/refund/resume", json=answer)
        assert done.status_code == 200 and done.json() == {"done": "refund 40: approved"}
        again = client.post("/refund/resume", json=answer)
        assert again.status_code == 409
        assert client.post("/refund/resume", json={"run_id": "nope"}).status_code == 404
        assert client.post("/refund/resume", json={"answers": {}}).status_code == 400


def test_without_durable_nothing_is_journalled(tmp_path, monkeypatch):
    name = f"dur_{uuid.uuid4().hex[:8]}"
    (tmp_path / f"{name}.py").write_text(MODULE, encoding="utf-8")
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname = "demo"\nsrc = ["."]\napp = "{name}:APP"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        app = Application.find(tmp_path)
        assert app.job("orders").engine()._journal is None
        assert app.service("refund").resume_spec() is None
    finally:
        sys.modules.pop(name, None)


@op
async def echo(item: str) -> dict:
    return {"out": item}


@graph
def live():
    i = ingress()
    e = echo(item=i["item"])
    START >> i >> e >> END


def test_a_graph_with_a_door_is_never_journalled():
    engine = _compile("j", "graph", live(name="live"), trace=[], journal=MemoryJournal())
    assert engine._journal is None


async def test_the_bridge_lists_a_parked_run_and_answers_it(project):
    # docs/N8N_LANGGRAPH_GAP_PLAN.md S1c: the studio's way in
    from operonx.app.play import Bridge

    root, _ = project
    app = Application.find(str(root))
    app.bootstrap()
    bridge = Bridge(app, lambda event: None)
    ((owner, engine),) = [(o, e) for o, e in await bridge._durable_engines() if "service" in o]
    assert owner == {"service": "refund"}
    out = await engine.run({"x": 9}, run_id="ask-1")
    asked = out["$interrupted"][0]

    listed = await bridge.parked()
    assert [r["run_id"] for r in listed["runs"]] == ["ask-1"]
    run = listed["runs"][0]
    assert run["owner"] == {"service": "refund"} and run["inputs"] == {"x": 9}
    assert run["questions"] == [{"id": asked["interrupt_id"], "op": ".ask", "payload": "refund 9"}]

    refused = await bridge.answer({"run_id": "nope", "answers": {"a": 1}})
    assert refused["status"] == "error" and "no journalled" in refused["error"]
    done = await bridge.answer({"run_id": "ask-1", "answers": {asked["interrupt_id"]: "approve"}})
    assert done == {
        "t": "answer",
        "run_id": "ask-1",
        "owner": {"service": "refund"},
        "status": "ok",
    }
    assert (await bridge.parked())["runs"] == []
    again = await bridge.answer({"run_id": "ask-1", "answers": {asked["interrupt_id"]: "x"}})
    assert again["status"] == "error" and "not waiting" in again["error"]


async def test_the_bridge_runs_a_failed_item_again_from_its_failed_op(project):
    # docs/N8N_LANGGRAPH_GAP_PLAN.md S2: the studio's "Retry from the failure"
    from operonx.app.play import Bridge

    root, name = project
    app = Application.find(str(root))
    app.bootstrap()
    first = await app.run("once")
    assert first.counts.get("failed") == 1
    mod = _module(name)
    bridge = Bridge(app, lambda event: None)
    ((owner, engine),) = [
        (o, e) for o, e in await bridge._durable_engines() if o == {"job": "once"}
    ]
    (header,) = engine._journal.runs()
    run_id = header.run_id

    refused = await bridge.retry({"run_id": run_id, "service": "nope"})
    assert refused["status"] == "error" and "not journalled" in refused["error"]
    assert "needs" in (await bridge.retry({"run_id": run_id}))["error"]

    mod.BROKEN["on"] = False  # fixed
    done = await bridge.retry({"run_id": run_id, "job": "once"})
    assert done == {"t": "retry", "run_id": run_id, "owner": {"job": "once"}, "status": "ok"}
    assert mod.CALLS == ["charge", "ship", "ship"]  # charged once
    again = await bridge.retry({"run_id": run_id, "job": "once"})
    assert again["status"] == "error" and "no op of the run failed" in again["error"]


async def test_the_bridge_forks_a_run_from_an_op(project):
    # docs/N8N_LANGGRAPH_GAP_PLAN.md L4/S3: "change this output and continue"
    from operonx.app.play import Bridge

    root, name = project
    app = Application.find(str(root))
    app.bootstrap()
    mod = _module(name)
    mod.BROKEN["on"] = False
    bridge = Bridge(app, lambda event: None)
    ((_, engine),) = [(o, e) for o, e in await bridge._durable_engines() if o == {"job": "once"}]
    await engine.run({"order": 3}, run_id="o-3")

    done = await bridge.fork(
        {"run_id": "o-3", "job": "once", "at": "c", "outputs": {"receipt": "R-77"}}
    )
    assert done["status"] == "ok" and done["forked_from"] == "o-3"
    assert done["run_id"].startswith("o-3~")
    assert mod.CALLS == ["charge", "ship", "ship"]  # charged once
    bad = await bridge.fork({"run_id": "o-3", "job": "once", "at": "c", "outputs": {"x": 1}})
    assert bad["status"] == "error" and "has no output 'x'" in bad["error"]
