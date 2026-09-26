"""The playground's voice, conditions and simulated user — P8.

Gates: a PCM codec offers the Voice toy, cuts the toy's audio batches into
the door's frames (padding the last) and plays back what the door sends;
conditions make a session's world worse on purpose — latency before each
inbound item, a share of items dropped (and counted), white noise mixed
into audio, silence before the first word, and a resource that fails for
that session only (another session, at the same time, is untouched); a
simulated user — an LLM persona — holds a conversation for its turns,
ends it when it is done, and the run records what it said; and what a
simulated user cannot drive, it refuses.
"""

from __future__ import annotations

import asyncio
import base64
import struct
import sys
import textwrap
import time
import uuid
from unittest.mock import Mock, patch

import pytest

from operonx.app import Application
from operonx.app.play import Bridge, PcmCodec, add_noise, pcm_frames
from operonx.core.registry import ResourceHub
from operonx.telemetry.runs.files import FilesRunStore

PROJECT = """
from operonx.core import END, START, graph, op
from operonx.core.registry import ResourceHub
from operonx.app.play import PcmCodec
from operonx.app.serve import egress, ingress


@op(bound="sync")
def echo(item=None) -> dict:
    # a voice door that plays each frame straight back
    return {"frame": item}


@graph
def voice_flow():
    src = ingress()
    e = echo(item=src["item"])
    out = egress(item=e["frame"])
    START >> src >> e >> out >> END


@op
async def answer(text: str = "") -> dict:
    tool = ResourceHub.instance().get("tool:greeter")
    return {"reply": await tool.greet(text)}


@graph
def chat_flow():
    src = ingress()
    a = answer(text=src["item"])
    out = egress(item=a["reply"])
    START >> src >> a >> out >> END


VOICE = PcmCodec(rate=8000, frame_ms=40)
"""


class Greeter:
    async def greet(self, text):
        return f"hi, you said {text}"


GREETER = Greeter()


@pytest.fixture
def project(tmp_path, monkeypatch):
    name = f"voice_{uuid.uuid4().hex[:6]}"
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(PROJECT), encoding="utf-8")
    (tmp_path / "resources.yaml").write_text("trace_local:\n  default: {}\n", encoding="utf-8")
    (tmp_path / "operonx.toml").write_text(textwrap.dedent(f"""
        [project]
        name = "voicedemo"
        trace = ["trace_local:default"]

        [resources]
        overlay = "resources.yaml"

        [[serve]]
        name  = "voice"
        kind  = "websocket"
        path  = "/voice"
        port  = 8125
        max_inflight = 64
        graph = "{name}:voice_flow"
        playground = "{name}:VOICE"

        [[serve]]
        name  = "chat"
        kind  = "websocket"
        path  = "/chat"
        port  = 8125
        max_inflight = 64
        graph = "{name}:chat_flow"
    """), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    real_get = ResourceHub.get
    monkeypatch.setattr(ResourceHub, "get",
                        lambda self, key: GREETER if key == "tool:greeter" else real_get(self, key))
    yield name, tmp_path
    sys.modules.pop(name, None)
    ResourceHub.reset_instance()


def _bridge(root):
    app = Application.find(root)
    app.bootstrap()
    events = []
    return Bridge(app, events.append), events


async def _until(events, pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        hit = [e for e in events if pred(e)]
        if hit:
            return hit[0]
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out; events: {events}")


def _pcm(n, value=1000):
    return struct.pack(f"<{n}h", *([value] * n))


def _audio(pcm):
    return {"kind": "audio", "b64": base64.b64encode(pcm).decode(), "rate": 8000}


# ── audio ─────────────────────────────────────────────────────────────────


def test_a_pcm_codec_frames_and_plays_back():
    c = PcmCodec(rate=8000, frame_ms=40)
    assert c.audio == {"rate": 8000, "encoding": "pcm16", "frame_ms": 40} and "voice" in c.toys
    frames = c.to_door_items(_audio(_pcm(700)))  # 320 + 320 + 60 samples
    assert [len(f) for f in frames] == [640, 640, 640] and frames[-1][120:] == b"\x00" * 520
    assert c.from_door(b"\x01\x02") == {"kind": "audio", "b64": "AQI=", "rate": 8000}
    assert c.to_door_items({"kind": "text", "text": "hi"}) == ["hi"]
    assert pcm_frames(b"", 8000, 40) == []


def test_a_voice_session_round_trips_audio(project):
    name, root = project
    bridge, events = _bridge(root)
    door = {d["service"]: d for d in bridge.describe()["doors"]}["voice"]
    assert door["toys"] == ["voice"] and door["audio"]["rate"] == 8000

    async def go():
        await bridge.handle({"op": "open", "sid": "v", "service": "voice", "toy": "voice",
                             "send": [_audio(_pcm(640))], "end": True})
        return await _until(events, lambda e: e["t"] == "ended")

    ended = asyncio.run(go())
    outs = [e["msg"] for e in events if e["t"] == "out"]
    assert len(outs) == 2 and all(o["kind"] == "audio" and o["rate"] == 8000 for o in outs)
    assert base64.b64decode(outs[0]["b64"]) == _pcm(320)
    run = FilesRunStore(root=root / ".operonx" / "runs", refresh_every=0).get_run(ended["trace_id"])
    # the script counts audio, it never keeps it
    (sent,) = run.summary.metadata["playground_script"]
    assert (sent["kind"], sent["size"]) == ("audio", 1280) and "b64" not in sent


# ── conditions ────────────────────────────────────────────────────────────


def test_latency_drop_noise_and_silence(project):
    name, root = project
    bridge, events = _bridge(root)

    async def session(sid, conditions, msgs):
        t0 = time.monotonic()
        await bridge.handle({"op": "open", "sid": sid, "service": "voice", "conditions": conditions,
                             "send": msgs, "end": True})
        ended = await _until(events, lambda e: e["t"] == "ended" and e["sid"] == sid)
        return ended, time.monotonic() - t0, [e["msg"] for e in events if e["t"] == "out" and e["sid"] == sid]

    async def go():
        slow = await session("slow", {"latency_ms": 150}, [_audio(_pcm(320)), _audio(_pcm(320))])
        lost = await session("lost", {"drop": 1.0}, [_audio(_pcm(640))])
        noisy = await session("noisy", {"noise_dbfs": -20}, [_audio(_pcm(320, 0))])
        quiet = await session("quiet", {"silence_ms": 80}, [_audio(_pcm(320))])
        return slow, lost, noisy, quiet

    slow, lost, noisy, quiet = asyncio.run(go())
    assert slow[1] >= 0.3 and len(slow[2]) == 2  # 150 ms before each of two messages
    assert lost[2] == [] and lost[0]["dropped"] == 2
    noise = struct.unpack("<320h", base64.b64decode(noisy[2][0]["b64"]))
    assert max(abs(x) for x in noise) > 100  # silence in, noise out
    assert [set(struct.unpack("<320h", base64.b64decode(m["b64"]))) for m in quiet[2]] == [{0}, {0}, {1000}]
    run = FilesRunStore(root=root / ".operonx" / "runs", refresh_every=0).get_run(slow[0]["trace_id"])
    assert run.summary.metadata["playground_conditions"] == {"latency_ms": 150.0}


def test_add_noise_is_bounded_and_seeded():
    loud = add_noise(_pcm(100, 32000), -6, seed=1)
    assert max(struct.unpack("<100h", loud)) <= 32767
    assert add_noise(_pcm(10, 0), -40, seed=2) == add_noise(_pcm(10, 0), -40, seed=2)


def test_a_resource_fails_for_its_session_only(project):
    name, root = project
    bridge, events = _bridge(root)

    async def go():
        await bridge.handle({"op": "open", "sid": "bad", "service": "chat", "conditions": {"fail": ["tool:greeter"]},
                             "send": [{"kind": "text", "text": "x"}]})
        await bridge.handle({"op": "open", "sid": "good", "service": "chat", "send": [{"kind": "text", "text": "y"}]})
        await _until(events, lambda e: e["t"] == "out" and e["sid"] == "good")
        await bridge.handle({"op": "end", "sid": "bad"})
        await bridge.handle({"op": "end", "sid": "good"})
        await _until(events, lambda e: e["t"] == "ended" and e["sid"] == "bad")
        return await _until(events, lambda e: e["t"] == "ended" and e["sid"] == "good")

    good = asyncio.run(go())
    bad = next(e for e in events if e["t"] == "ended" and e["sid"] == "bad")
    assert bad["status"] == "error" and "tool:greeter failed (a playground condition)" in bad["error"]
    assert good["status"] == "ok"
    assert next(e for e in events if e["t"] == "out" and e["sid"] == "good")["msg"]["text"] == "hi, you said y"
    # and the resource is itself again outside that session
    assert asyncio.run(GREETER.greet("z")) == "hi, you said z"


# ── a simulated user ──────────────────────────────────────────────────────


def _persona(lines):
    from openai.types.chat.chat_completion import ChatCompletion, Choice
    from openai.types.chat.chat_completion_message import ChatCompletionMessage
    from openai.types.completion_usage import CompletionUsage

    seen = []

    async def generate(messages, **kwargs):
        seen.append(messages)
        text = lines[min(len(seen) - 1, len(lines) - 1)]
        return ChatCompletion(id="p", created=1, model="persona", object="chat.completion", choices=[
            Choice(index=0, finish_reason="stop", message=ChatCompletionMessage(role="assistant", content=text))],
            usage=CompletionUsage(prompt_tokens=20, completion_tokens=6, total_tokens=26))

    llm = Mock()
    llm.generate = generate
    hub = Mock()
    hub.get.return_value = llm
    return hub, seen


def test_a_simulated_user_holds_a_conversation(project):
    name, root = project
    bridge, events = _bridge(root)
    hub, seen = _persona(["I need to move my class", "Tuesday works. Thanks! [END]"])

    async def go():
        with patch("operonx.providers.ops._utils.ResourceHub") as cls:
            cls.instance.return_value = hub
            await bridge.handle({"op": "simulate", "sid": "sim", "service": "chat", "llm": "llm:persona",
                                 "persona": "A busy parent who wants to reschedule a class.",
                                 "turns": 5, "quiet_ms": 100})
        return await _until(events, lambda e: e["t"] == "ended" and e["sid"] == "sim")

    ended = asyncio.run(go())
    said = [e["text"] for e in events if e["t"] == "said"]
    assert said == ["I need to move my class", "Tuesday works. Thanks!"]  # [END] closes it
    heard = [e["msg"]["text"] for e in events if e["t"] == "out"]
    assert heard == ["hi, you said I need to move my class", "hi, you said Tuesday works. Thanks!"]
    # the persona read the conversation: its second call saw the service's reply
    assert seen[1][0]["role"] == "system" and "busy parent" in seen[1][0]["content"]
    assert seen[1][1] == {"role": "assistant", "content": "I need to move my class"}
    assert seen[1][2] == {"role": "user", "content": "hi, you said I need to move my class"}
    md = FilesRunStore(root=root / ".operonx" / "runs", refresh_every=0).get_run(ended["trace_id"]).summary.metadata
    assert md["toy"] == "simulated" and md["persona"].startswith("A busy parent") and md["simulated_by"] == "llm:persona"
    assert [m["text"] for m in md["playground_script"]] == said


def test_what_a_simulated_user_cannot_drive_it_refuses(project):
    name, root = project
    bridge, events = _bridge(root)

    async def go():
        await bridge.handle({"op": "simulate", "sid": "a", "service": "chat", "llm": "llm:x"})
        await bridge.handle({"op": "simulate", "sid": "b", "service": "voice", "llm": "llm:x", "persona": "p"})

    asyncio.run(go())
    refused = {e["sid"]: e["reason"] for e in events if e["t"] == "refused"}
    assert "needs a persona and an llm" in refused["a"]
    assert refused["b"] == "a simulated user speaks text, and this door takes voice"


# ── where a playground run is recorded ────────────────────────────────────


def test_playground_runs_stay_local_unless_asked(tmp_path, monkeypatch):
    """A service that also ships to a remote tracer (Langfuse, say): a
    playground session records locally only; `remote: true` sends it on."""
    from operonx.app import Service, websocket
    from operonx.app.play import _split_consumers
    from operonx.app.serve import egress, ingress
    from operonx.core import END, START, graph, op
    from operonx.telemetry.consumer import Consumer
    from operonx.telemetry.consumers.local import LocalConsumer

    class Remote(Consumer):
        def __init__(self):
            self.got = []

        def consume(self, trace):
            self.got.append(trace.trace_id)

    @op(bound="sync")
    def up(item=None) -> dict:
        return {"out": str(item).upper()}

    @graph
    def upper_flow():
        src = ingress()
        u = up(item=src["item"])
        out = egress(item=u["out"])
        START >> src >> u >> out >> END

    monkeypatch.chdir(tmp_path)
    remote, local = Remote(), LocalConsumer({"root": str(tmp_path / "runs")})
    assert _split_consumers([local, remote]) == ([local], [remote])
    app = Application("remote-demo", services=[Service(
        "up", websocket("/up", port=8126), graph=upper_flow, max_inflight=8, trace=[local, remote])])
    events = []
    bridge = Bridge(app, events.append)
    assert bridge.describe()["doors"][0]["remote_trace"] == ["Remote"]

    async def go(sid, remote_flag):
        await bridge.handle({"op": "open", "sid": sid, "service": "up", "remote": remote_flag,
                             "send": [{"kind": "text", "text": "hi"}], "end": True})
        return await _until(events, lambda e: e["t"] == "ended" and e["sid"] == sid)

    quiet = asyncio.run(go("q", False))
    assert remote.got == [] and FilesRunStore(root=tmp_path / "runs", refresh_every=0).get_run(quiet["trace_id"])
    loud = asyncio.run(go("l", True))
    assert remote.got == [loud["trace_id"]]
