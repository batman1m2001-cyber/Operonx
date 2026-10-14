"""`[retry]` in operonx.toml: the project's default op retry (docs/RETRY_PLAN.md R2).

It reaches the engines of the project's jobs and request-response services;
a stream door (a live call) is left alone, since an item already sent to the
caller cannot be sent again. Mistakes fail at load, naming the key.
"""

from __future__ import annotations

import sys
import textwrap
import uuid

import pytest

from operonx.app import Application, ManifestError
from operonx.app.manifest import Manifest, _toml
from operonx.core.policy import Retry

pytestmark = pytest.mark.unit


def _manifest(text: str) -> Manifest:
    return Manifest.from_dict(_toml.loads(textwrap.dedent(text)))


def test_retry_is_parsed_as_a_retry():
    m = _manifest("""
    [project]
    name = "x"
    [retry]
    max_attempts = 4
    initial = 1
    max_interval = 60
    """)
    assert m.retry == Retry(max_attempts=4, initial=1, max_interval=60)


def test_transient_names_are_added_to_the_one_list_and_checked_at_start():
    from operonx.app.manifest import check_transient
    from operonx.core.policy import TRANSIENT

    kept = TRANSIENT._types, TRANSIENT._names
    try:
        m = _manifest("""
        [project]
        name = "x"
        [retry]
        transient = ["no_such_crm_xyz.errors:Throttled"]
        """)
        assert m.retry == Retry()
        assert "no_such_crm_xyz.errors:Throttled" in TRANSIENT._names
        with pytest.raises(ManifestError, match="no_such_crm_xyz"):
            check_transient()
    finally:
        TRANSIENT._types, TRANSIENT._names = kept


def test_without_retry_there_is_no_default():
    assert _manifest('[project]\nname = "x"\n').retry is None


@pytest.mark.parametrize(
    "table, match",
    [
        ("max_retries = 3", r"\[retry\] takes max_attempts.*not max_retries"),
        ("max_attempts = 0", r"\[retry\]: Retry\(max_attempts=0\)"),
        ('max_attempts = "3"', r"\[retry\]: Retry\(max_attempts=\.\.\.\) takes an int"),
        ('transient = "crm:Throttled"', r"\[retry\] transient is a list"),
        ('transient = ["Throttled"]', r"\[retry\] transient is a list"),
    ],
)
def test_a_mistake_fails_at_load_naming_the_key(table, match):
    with pytest.raises(ManifestError, match=match):
        _manifest(f'[project]\nname = "x"\n[retry]\n{table}\n')


MODULE = """
from operonx.app import Application, Service, http, websocket
from operonx.app.jobs import Job
from operonx.core import END, START, Operon, graph, op
from operonx.core.policy import Retry


@op
def one() -> dict:
    return {"n": 1}


@graph
def flow():
    o = one()
    START >> o >> END


member = Job("member", graph=flow)
APP = Application(
    "demo",
    services=[
        Service("ask", http("POST", "/ask", port=8473), graph=flow),
        Service(
            "call", websocket("/call", port=8474), graph=flow, max_inflight=8
        ),
    ],
    jobs=[
        Job("backfill", graph=flow),
        Job("nightly", steps=[member]),
        Job("own", graph=Operon(flow(name="flow"), retry=Retry(max_attempts=9))),
    ],
    resources=None,
    src=["."],
)
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    name = f"rt_{uuid.uuid4().hex[:8]}"
    (tmp_path / f"{name}.py").write_text(MODULE, encoding="utf-8")
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname = "demo"\nsrc = ["."]\napp = "{name}:APP"\n\n[retry]\nmax_attempts = 4\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    sys.modules.pop(name, None)


def test_a_request_response_service_takes_it_and_a_stream_door_does_not(project):
    from operonx.app.serve.app import engine_for

    app = Application.find(project)
    assert app.service("ask").options["retry"] == Retry(max_attempts=4)
    assert engine_for(app.service("ask"))._retry == Retry(max_attempts=4)
    assert "retry" not in app.service("call").options
    assert engine_for(app.service("call"))._retry is None


def test_every_job_and_step_takes_it_and_an_engine_given_as_is_keeps_its_own(project):
    app = Application.find(project)
    assert app.job("backfill").engine()._retry == Retry(max_attempts=4)
    (member,) = app.job("nightly").steps
    assert member.engine()._retry == Retry(max_attempts=4)
    assert app.job("own").engine()._retry == Retry(max_attempts=9)
