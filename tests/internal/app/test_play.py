"""The playground bridge (`operonx.app.play`) — P6.

Gates: a door is described with the toys its codec speaks (built-in for
http and websocket, declared for anything else, none otherwise); a toy's
messages go through the service's real gate (`on_session`, which can tell
a playground session apart), its real door ops and its trace consumers;
replies come back as toy messages in order; the run is filed as
``origin=playground`` with what the toy sent, so it can be replayed; a
failing run and a refused session say why; one op re-runs with recorded
inputs and is recorded as a run of its own; startup hooks run once; and
the stdio bridge keeps its protocol stream clean of the project's prints.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from operonx.app import Application
from operonx.app.declare import Listener, Service, describe_service
from operonx.app.play import Bridge, Codec, JsonCodec, TextCodec, codec_for, toy_message
from operonx.telemetry.runs.files import FilesRunStore

PROJECT = """
from operonx.core import END, START, graph, op
from operonx.app.play import Codec
from operonx.app.serve import RunRequest, egress, ingress

SEEN = {"startup": 0, "sessions": [], "closed": 0}


def warm():
    SEEN["startup"] += 1


def gate(session):
    SEEN["sessions"].append(dict(session.meta))
    if session.meta["query"].get("deny"):
        return None
    return RunRequest(inputs={"prefix": session.meta["query"].get("prefix", ">")})


def closed(session, handle):
    SEEN["closed"] += 1


@op(bound="sync")
def score(call: dict = None) -> dict:
    if not call.get("text"):
        raise ValueError("empty text")
    print("scoring", call["call_id"])          # must never reach the protocol stream
    return {"result": {"call_id": call["call_id"], "words": len(call["text"].split())}}


@graph
def score_flow():
    src = ingress()
    scored = score(call=src["item"])
    out = egress(item=scored["result"])
    START >> src >> scored >> out >> END


@op(bound="sync")
def shout(text: str = "", prefix: str = ">") -> dict:
    if isinstance(text, dict):
        return {"reply": {"event": "got_json", "keys": sorted(text)}}
    return {"reply": f"{prefix} {text.upper()}"}


@graph
def chat_flow(prefix: str = ">"):
    src = ingress()
    said = shout(text=src["item"], prefix=prefix)
    out = egress(item=said["reply"])
    START >> src >> said >> out >> END


class Upper(Codec):
    toys = ("chat",)

    def to_door(self, message):
        return {"said": message.get("text", "")}

    def from_door(self, item):
        return {"kind": "text", "text": str(item).upper()}
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    name = f"play_{uuid.uuid4().hex[:6]}"
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(PROJECT), encoding="utf-8")
    (tmp_path / "resources.yaml").write_text("trace_local:\n  default: {}\n", encoding="utf-8")
    (tmp_path / "operonx.toml").write_text(textwrap.dedent(f"""
        [project]
        name = "playdemo"
        trace = ["trace_local:default"]
        on_startup = ["{name}:warm"]

        [resources]
        overlay = "resources.yaml"

        [[job]]
        name   = "nightly"
        graph  = "{name}:score_flow"
        source = [{{call_id = "j1", text = "a b"}}]
        key    = "call_id"

        [[serve]]
        name  = "score"
        kind  = "http"
        path  = "/score"
        port  = 8124
        graph = "{name}:score_flow"

        [[serve]]
        name  = "chat"
        kind  = "websocket"
        path  = "/chat"
        port  = 8124
        max_inflight = 8
        graph = "{name}:chat_flow"
        on_session = "{name}:gate"
        on_close = "{name}:closed"

        [[serve]]
        name  = "raw"
        kind  = "memory"
        path  = "/raw"
        max_inflight = 4
        graph = "{name}:score_flow"

        [[serve]]
        name  = "custom"
        kind  = "memory"
        path  = "/custom"
        max_inflight = 4
        graph = "{name}:score_flow"
        playground = "{name}:Upper"
    """), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPERONX_RUNS_DIR", raising=False)
    yield name, tmp_path
    sys.modules.pop(name, None)
    from operonx.core.registry import ResourceHub

    ResourceHub.reset_instance()


def _bridge(root):
    app = Application.find(root)
    app.bootstrap()
    events = []
    return Bridge(app, events.append), events


async def _until(events, pred, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        hit = [e for e in events if pred(e)]
        if hit:
            return hit[0]
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out; events: {events}")


def _store(root):
    return FilesRunStore(root=root / ".operonx" / "runs", refresh_every=0)


def test_doors_are_described_with_their_toys(project):
    name, root = project
    bridge, _ = _bridge(root)
    doors = {d["service"]: d for d in bridge.describe()["doors"]}
    assert doors["score"]["toys"] == ["form"] and doors["score"]["codec"] == "JsonCodec"
    assert doors["chat"]["toys"] == ["chat", "form"] and doors["chat"]["inputs"] == ["prefix"]
    assert doors["chat"]["custom_hook"] and doors["chat"]["session"] == "per_connection"
    assert doors["raw"]["toys"] == [] and doors["raw"]["codec"] is None   # no codec, no toy
    assert doors["custom"]["toys"] == ["chat"] and doors["custom"]["codec"] == "Upper"


def test_a_form_request_runs_the_real_door_and_is_filed_as_playground(project):
    name, root = project
    bridge, events = _bridge(root)

    async def go():
        payload = {"call_id": "c1", "text": "one two three"}
        await bridge.handle({"op": "open", "sid": "f1", "service": "score", "toy": "form",
                             "send": [{"kind": "json", "value": payload}], "end": True})
        return await _until(events, lambda e: e["t"] == "ended")

    ended = asyncio.run(go())
    opened = next(e for e in events if e["t"] == "opened")
    (out,) = [e for e in events if e["t"] == "out"]
    assert out["msg"] == {"kind": "json", "value": {"call_id": "c1", "words": 3}}
    assert ended["status"] == "ok" and ended["trace_id"] == opened["trace_id"] and ended["sent"] == 1

    run = _store(root).get_run(opened["trace_id"])
    meta = run.summary.metadata
    assert meta["origin"] == "playground" and meta["service"] == "score" and meta["toy"] == "form"
    assert meta["playground_script"] == [{"kind": "json", "value": {"call_id": "c1", "text": "one two three"}}]
    assert "origin:playground" in meta["tags"] and meta.get("project") == "playdemo"
    assert (root / ".operonx" / "runs" / "playground").is_dir()


def test_a_chat_session_goes_through_the_gate_and_answers_in_order(project):
    name, root = project
    bridge, events = _bridge(root)

    async def go():
        await bridge.handle({"op": "open", "sid": "c1", "service": "chat", "query": {"prefix": "bot:"}})
        for text in ("hello", "how are you"):
            await bridge.handle({"op": "send", "sid": "c1", "msg": {"kind": "text", "text": text}})
        await bridge.handle({"op": "send", "sid": "c1", "msg": {"kind": "json", "value": {"a": 1, "b": 2}}})
        await _until(events, lambda e: e["t"] == "out" and e["msg"]["kind"] == "json")
        await bridge.handle({"op": "end", "sid": "c1"})
        return await _until(events, lambda e: e["t"] == "ended")

    ended = asyncio.run(go())
    mod = sys.modules[name]
    outs = [e["msg"] for e in events if e["t"] == "out"]
    assert outs == [{"kind": "text", "text": "bot: HELLO"}, {"kind": "text", "text": "bot: HOW ARE YOU"},
                    {"kind": "json", "value": {"event": "got_json", "keys": ["a", "b"]}}]
    assert ended["status"] == "ok"
    # the service's own hook saw a playground session, and its on_close ran
    assert mod.SEEN["sessions"][-1]["playground"] is True and mod.SEEN["closed"] == 1
    assert next(e for e in events if e["t"] == "opened")["inputs"] == {"prefix": "bot:"}
    run = _store(root).get_run(ended["trace_id"])
    assert [m.get("text") for m in run.summary.metadata["playground_script"]] == ["hello", "how are you", None]
    assert run.summary.metadata["playground_query"] == {"prefix": "bot:"}


def test_refusals_and_failures_say_why(project):
    name, root = project
    bridge, events = _bridge(root)

    async def go():
        await bridge.handle({"op": "open", "sid": "no", "service": "chat", "query": {"deny": "1"}})
        await bridge.handle({"op": "open", "sid": "raw", "service": "raw"})
        await bridge.handle({"op": "open", "sid": "bad", "service": "score",
                             "send": [{"kind": "json", "value": {"call_id": "c2", "text": ""}}], "end": True})
        await bridge.handle({"op": "send", "sid": "ghost", "msg": {"kind": "text", "text": "x"}})
        await bridge.handle({"id": 9, "op": "fly"})
        return await _until(events, lambda e: e["t"] == "ended" and e["sid"] == "bad")

    ended = asyncio.run(go())
    refused = {e["sid"]: e["reason"] for e in events if e["t"] == "refused"}
    assert "on_session refused" in refused["no"] and "no playground codec" in refused["raw"]
    assert ended["status"] == "error" and ended["error"] == "scored: ValueError: empty text"
    errors = [e for e in events if e["t"] == "error"]
    assert errors[0]["sid"] == "ghost" and errors[1] == {"t": "error", "text": "unknown op 'fly'", "id": 9}


def test_a_door_with_no_consumers_is_still_recorded(project, tmp_path, monkeypatch):
    """No `trace` anywhere: the playground records locally, as a job does."""
    name, root = project
    toml = root / "operonx.toml"
    toml.write_text(toml.read_text().replace('trace = ["trace_local:default"]\n', ""))
    bridge, events = _bridge(root)

    async def go():
        await bridge.handle({"op": "open", "sid": "n", "service": "score", "end": True,
                             "send": [{"kind": "json", "value": {"call_id": "n1", "text": "a"}}]})
        return await _until(events, lambda e: e["t"] == "ended")

    ended = asyncio.run(go())
    assert _store(root).get_run(ended["trace_id"]).summary.origin == "playground"


def test_a_declared_codec_translates_both_ways(project):
    name, root = project
    app = Application.find(root)
    app.bootstrap()  # the project importable, as the bridge makes it
    codec = codec_for(app.service("custom"))
    assert codec.to_door({"kind": "text", "text": "hi"}) == {"said": "hi"}
    assert codec.from_door("ok") == {"kind": "text", "text": "OK"}


def test_one_op_reruns_with_recorded_inputs_as_its_own_run(project):
    name, root = project
    bridge, _ = _bridge(root)

    async def go():
        ok = await bridge.rerun({"service": "score", "op_name": "scored",
                                 "inputs": {"call": {"call_id": "c3", "text": "a b c d"}}, "of": "run-1"})
        bad = await bridge.rerun({"service": "score", "op_name": "scored",
                                  "inputs": {"call": {"call_id": "c3", "text": ""}}, "of": "run-1"})
        ghost = await bridge.rerun({"service": "score", "op_name": "nope", "inputs": {}})
        job = await bridge.rerun({"job": "nightly", "op_name": "scored",
                                  "inputs": {"call": {"call_id": "j1", "text": "x y"}}, "of": "job-run"})
        return ok, bad, ghost, job

    ok, bad, ghost, job = asyncio.run(go())
    assert job["status"] == "ok" and job["outputs"] == {"result": {"call_id": "j1", "words": 2}}
    assert _store(root).get_run(job["trace_id"]).summary.metadata["job"] == "nightly"
    assert ok["status"] == "ok" and ok["outputs"] == {"result": {"call_id": "c3", "words": 4}}
    assert bad["status"] == "error" and bad["error"] == "scored: ValueError: empty text"
    assert ghost["status"] == "error" and "no op 'nope'" in ghost["error"]
    run = _store(root).get_run(ok["trace_id"])
    assert run.summary.metadata["toy"] == "rerun" and run.summary.metadata["rerun_of"] == "run-1" and run.summary.metadata["op"] == "scored"
    # recorded as the graph records it: this input is fed from the door's
    # transient stream, so its value is summarised, never kept
    assert [n["op_name"] for n in run.nodes] == ["scored"] and "transient" in run.nodes[0]["inputs"]["call"]
    assert _store(root).get_run(bad["trace_id"]).nodes[0]["status"] == "error"


def test_startup_hooks_run_once_however_many_sessions(project):
    name, root = project
    bridge, events = _bridge(root)

    async def go():
        for i in range(3):
            await bridge.handle({"op": "open", "sid": f"s{i}", "service": "score",
                                 "send": [{"kind": "json", "value": {"call_id": f"c{i}", "text": "x"}}], "end": True})
        await bridge.handle({"op": "open", "sid": "chat", "service": "chat", "send": [], "end": True})
        await _until(events, lambda e: e["t"] == "ended" and e["sid"] == "chat")
        await bridge.drain()

    asyncio.run(go())
    assert sys.modules[name].SEEN["startup"] == 1
    assert len([e for e in events if e["t"] == "ended"]) == 4


def test_toy_messages_for_any_item():
    assert toy_message("hi") == {"kind": "text", "text": "hi"}
    assert toy_message(b"\x00\x01") == {"kind": "bytes", "size": 2, "b64": "AAE="}
    assert toy_message({"a": [1]}) == {"kind": "json", "value": {"a": [1]}}
    assert toy_message({1, 2})["kind"] == "json"  # not JSON: shown as its repr
    assert Codec().to_door({"kind": "bytes", "b64": "AAE="}) == b"\x00\x01"
    with pytest.raises(ValueError, match="text, json, bytes or audio"):
        TextCodec().to_door({"kind": "video"})
    assert JsonCodec.toys == ("form",) and TextCodec.toys == ("chat", "form")


def test_service_declares_its_playground_codec():
    spec = Service("door", Listener(kind="memory"), graph="m:g", max_inflight=2, playground="m:Codec")
    assert spec.options["playground"] == "m:Codec"
    assert describe_service(spec)["playground"] == "m:Codec"
    assert describe_service(Service("h", Listener(kind="http", path="/h"), graph="m:g"))["playground"] is None


def test_the_stdio_bridge_keeps_its_stream_clean(project):
    """The graph prints; the protocol stream must still be one JSON object
    per line, and the print must land on stderr."""
    name, root = project
    requests = [
        {"id": 1, "op": "describe"},
        {"op": "open", "sid": "a", "service": "score",
         "send": [{"kind": "json", "value": {"call_id": "c1", "text": "a b"}}], "end": True},
    ]
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys, time, threading\n"
         "from operonx.app.play import main\n"
         "sys.exit(main(['--root', '.']))"],
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True, text=True, timeout=60, cwd=root,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    events = [json.loads(ln) for ln in lines]  # every line is protocol
    kinds = [e["t"] for e in events]
    assert kinds[0] == "ready" and "doors" in kinds and "ended" in kinds, (proc.stdout, proc.stderr)
    assert next(e for e in events if e["t"] == "out")["msg"]["value"] == {"call_id": "c1", "words": 2}
    assert "scoring c1" in proc.stderr and "scoring" not in proc.stdout
