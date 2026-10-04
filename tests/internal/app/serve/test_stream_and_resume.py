"""K8: an http door streams what a run sends as server-sent events, and
a door whose runs stop for a human answers a second route, ``<path>/resume``.

Both exist for one client: an HTTP caller of a run that takes a while and
may end waiting for an approval. It reads each item as the run sends it
(``Accept: text/event-stream``), and later posts the decision to the
door's own resume route, where the run is continued by another graph.
"""

import asyncio
import json
import threading

import pytest

from operonx.app import Application, Service, http, websocket
from operonx.app.declare import describe_service
from operonx.app.manifest import Manifest, ManifestError
from operonx.app.serve import egress, ingress
from operonx.app.serve.app import TRACE_HEADER, build_app
from operonx.core import END, START, graph
from operonx.core.ops import op

starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

#: Set by the test once it has read the first frame; the run waits on it.
GATE = threading.Event()
#: What the run saw: whether the gate opened before it gave up waiting.
SEEN = {}


@op(bound="io", transient=True)
async def three(item=None):
    """Sends ``item`` three times, waiting for the test between the first
    and the second: a door that buffers the run never lets the test open
    the gate, so the run waits out its timeout."""
    yield {"word": f"{item}-1"}
    SEEN["gate_opened"] = await asyncio.to_thread(GATE.wait, 5)
    yield {"word": f"{item}-2"}
    yield {"word": f"{item}-3"}


@graph
def chatty():
    src = ingress()
    t = three(item=src["item"])
    out = egress(item=t["word"])
    START >> src >> t >> out >> END


@op(bound="sync")
def swallow(item=None) -> dict:
    return {}


@graph
def silent():
    src = ingress()
    s = swallow(item=src["item"])
    START >> src >> s >> END


@op(bound="sync")
def decide(item=None) -> dict:
    return {"said": {"resumed": item}}


@graph
def resumer():
    src = ingress()
    d = decide(item=src["item"])
    out = egress(item=d["said"])
    START >> src >> d >> out >> END


@op(bound="sync")
def start(item=None) -> dict:
    return {"said": {"started": item}}


@graph
def starter():
    src = ingress()
    s = start(item=src["item"])
    out = egress(item=s["said"])
    START >> src >> s >> out >> END


def _frames(text: str) -> list:
    """The ``data:`` payloads of an SSE body, decoded, in order."""
    out = []
    for block in text.split("\n\n"):
        data = [line[len("data: ") :] for line in block.splitlines() if line.startswith("data: ")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


SSE = {"accept": "text/event-stream"}


@pytest.fixture
def served():
    """A real server on a free port: starlette's TestClient reads a whole
    response before it returns any of it, so it cannot tell a stream
    from a buffered reply."""
    import socket
    import time

    import uvicorn

    servers = []

    def serve(app) -> str:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        servers.append((server, thread))
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "the server did not start"
            time.sleep(0.02)
        return f"http://127.0.0.1:{port}"

    yield serve
    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=10)


class TestServerSentEvents:
    def test_each_item_is_a_frame_sent_as_the_run_sends_it(self, served):
        import httpx

        GATE.clear()
        SEEN.clear()
        base = served(build_app((Service("c", http("POST", "/c"), graph=chatty),)))
        with httpx.stream("POST", f"{base}/c", json="w", headers=SSE, timeout=10) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers[TRACE_HEADER]
            lines = response.iter_lines()
            first = next(line for line in lines if line.startswith("data: "))
            # the first frame arrived while the run was still waiting
            assert json.loads(first[len("data: ") :]) == "w-1"
            GATE.set()
            rest = "\n".join(lines)
        assert SEEN["gate_opened"] is True
        assert _frames(rest + "\n\n") == ["w-2", "w-3"]

    def test_without_the_accept_header_the_reply_is_json_as_before(self):
        GATE.set()
        app = build_app((Service("c", http("POST", "/c"), graph=chatty),))
        with TestClient(app) as client:
            response = client.post("/c", json="w")
        assert response.headers["content-type"] == "application/json"
        assert response.json() == ["w-1", "w-2", "w-3"]

    def test_a_run_that_sends_nothing_is_a_500_not_an_empty_stream(self):
        app = build_app((Service("s", http("POST", "/s"), graph=silent),))
        with TestClient(app) as client:
            response = client.post("/s", json="w", headers=SSE)
        assert response.status_code == 500
        assert response.json() == {
            "error": "the graph produced no output",
            "endpoint": "s",
            "trace_id": response.headers[TRACE_HEADER],
        }


class TestResumeRoute:
    def _app(self):
        return Application(
            "r",
            services=[Service("agent", http("POST", "/agent"), graph=starter, resume=resumer)],
        )

    def test_the_door_answers_its_resume_route_with_the_resume_graph(self):
        app = build_app(self._app().services)
        with TestClient(app) as client:
            started = client.post("/agent", json={"q": 1})
            resumed = client.post("/agent/resume", json={"run": "x", "approve": True})
        assert started.json() == {"started": {"q": 1}}
        assert resumed.json() == {"resumed": {"run": "x", "approve": True}}
        assert resumed.headers[TRACE_HEADER] != started.headers[TRACE_HEADER]

    def test_the_resume_route_streams_too(self):
        app = build_app(self._app().services)
        with TestClient(app) as client:
            response = client.post("/agent/resume", json={"run": "x"}, headers=SSE)
        assert response.headers["content-type"].startswith("text/event-stream")
        assert _frames(response.text) == [{"resumed": {"run": "x"}}]

    def test_its_runs_are_filed_under_the_service(self):
        from operonx.app.serve.runner import ServeRunner

        built = build_app(self._app().services)
        runners = {r.spec.name: r for r in built.state.operonx_runners}
        assert set(runners) == {"agent", "agent.resume"}
        resume = runners["agent.resume"]
        assert isinstance(resume, ServeRunner) and resume.spec.path == "/agent/resume"
        assert resume.spec.method == "POST" and resume.spec.resume is None

    def test_describe_names_it(self):
        (spec,) = self._app().services
        assert describe_service(spec)["resume"] == f"{__name__}:resumer"
        assert spec.resume_spec().path == "/agent/resume"

    def test_the_resume_graph_is_one_of_the_projects_graphs(self):
        graphs = {g["name"]: g for g in self._app().describe()["graphs"]}
        assert graphs["resumer"]["used_by"] == ["serve:agent.resume"]
        assert graphs["starter"]["used_by"] == ["serve:agent"]

    def test_the_toml_block_says_the_same(self):
        spec = Manifest.from_dict(
            {
                "serve": [
                    {
                        "name": "agent",
                        "kind": "http",
                        "path": "/agent/",
                        "graph": f"{__name__}:starter",
                        "resume": f"{__name__}:resumer",
                    }
                ]
            }
        ).serve("agent")
        assert spec.resume == f"{__name__}:resumer" and "resume" not in spec.options
        assert spec.resume_spec().path == "/agent/resume"
        with TestClient(build_app((spec,))) as client:
            assert client.post("/agent/resume", json=1).json() == {"resumed": 1}

    def test_only_an_http_door_takes_one(self):
        with pytest.raises(ManifestError, match="resume=.*http.*websocket door resumes"):
            Service("w", websocket("/w"), graph=starter, resume=resumer, max_inflight=4)
        with pytest.raises(ManifestError, match="resume.*variants"):
            Service("v", http("POST", "/v"), graph=starter, resume=resumer, variants={"a": {}})
        with pytest.raises(ManifestError, match="resume.*not a `module:attr`"):
            Manifest.from_dict(
                {"serve": [{"name": "a", "kind": "http", "graph": "a:b", "resume": "nope"}]}
            )


def test_a_session_says_whether_its_peer_reads_a_stream():
    """`BoundedSession.stream`: a graph that can answer with every event or
    with one result (an agent run) asks the session which its peer wants."""
    from operonx.app.jobs.session import JobSession
    from operonx.app.serve import MemorySession
    from operonx.app.serve.asgi import HttpSession, WebSocketSession

    assert HttpSession("x").stream is False
    assert HttpSession("x", stream=True).stream is True
    assert WebSocketSession(websocket=None).stream is True
    assert MemorySession().stream is True
    assert JobSession(sink=None, key="k").stream is False
