"""Replay recording — a service that opts in writes down what its clients send.

The gates:

* off by default: a service's run carries no script;
* ``Service(replay=True)`` (or ``[[serve]] replay = true``): the run carries
  every inbound item as a toy message, in order, stamped, and the
  connection's query — the same shape a playground session keeps;
* text and JSON are kept; bytes, audio (as a codec says) and anything too
  large are counted, never kept; a script stops at its limit;
* the served session still behaves exactly as before.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.app import Service, websocket
from operonx.app.declare import describe_service
from operonx.app.manifest import Manifest, ServeSpec
from operonx.app.play import SCRIPT_LIMIT, SCRIPT_MESSAGE_LIMIT, Codec
from operonx.app.serve import MemoryTransport, ServeRunner, egress, ingress
from operonx.core import END, START, Operon, graph, op
from operonx.telemetry.consumer import Consumer


class Capture(Consumer):
    def __init__(self):
        super().__init__()
        self.traces = []

    def consume(self, trace):
        self.traces.append(trace)


@op(bound="io")
async def echo(item=None):
    return {"back": item}


@graph
def echo_flow():
    src = ingress()
    step = echo(item=src["item"])
    out = egress(item=step["back"])
    START >> src >> step >> out >> END


async def _serve(items, *, replay=True, query=None, bound=64, **options):
    cap = Capture()
    opts = {"replay": True} if replay else {}
    opts.update(options)
    spec = ServeSpec(name="chat", kind="memory", graph="x:y", max_inflight=bound, options=opts)
    transport = MemoryTransport(max_inflight=bound)
    runner = ServeRunner(Operon(echo_flow, trace=[cap]), spec, transport=transport)
    session = transport.open(meta={"query": query or {}})
    for item in items:
        session.feed_nowait(item)
    session.end_input()
    transport.stop()
    await runner.run()
    await asyncio.sleep(0.05)  # consumers run in a thread at the run's end
    (trace,) = cap.traces
    return session, trace.metadata


async def test_off_by_default_a_run_carries_no_script():
    session, md = await _serve(["hi"], replay=False)
    assert session.sent == ["hi"]
    assert "replay_script" not in md and "replay_query" not in md


async def test_a_replayable_service_writes_down_what_its_client_sent():
    session, md = await _serve(["hello", {"a": 1}, b"\x00\x01\x02"], query={"user": "42"})
    assert session.sent == ["hello", {"a": 1}, b"\x00\x01\x02"]  # served exactly as before
    script = md["replay_script"]
    assert [{k: v for k, v in m.items() if k != "at"} for m in script] == [
        {"kind": "text", "text": "hello"},
        {"kind": "json", "value": {"a": 1}},
        {"kind": "bytes", "size": 3},  # counted, never kept
    ]
    assert all(isinstance(m["at"], float) for m in script)
    assert [m["at"] for m in script] == sorted(m["at"] for m in script)
    assert md["replay_query"] == {"user": "42"}
    assert md["origin"] == "service" and md["service"] == "chat"


async def test_a_message_too_large_is_counted_not_kept():
    big = "x" * (SCRIPT_MESSAGE_LIMIT + 10)
    _, md = await _serve([big, "small"])
    first, second = md["replay_script"]
    assert first["kind"] == "large" and first["size"] > SCRIPT_MESSAGE_LIMIT and "text" not in first
    assert second["text"] == "small"


async def test_a_script_stops_at_its_limit():
    _, md = await _serve([f"m{i}" for i in range(SCRIPT_LIMIT + 5)], bound=SCRIPT_LIMIT + 10)
    assert len(md["replay_script"]) == SCRIPT_LIMIT


class AudioInJson(Codec):
    """A door whose JSON frames carry audio: the codec says which are audio."""

    def to_toy(self, item):
        if isinstance(item, dict) and item.get("event") == "media":
            return {"kind": "audio", "size": len(item["payload"])}
        return super().to_toy(item)


async def test_a_codec_decides_what_is_audio_and_audio_is_never_kept():
    frames = [{"event": "start"}, {"event": "media", "payload": "AAAA" * 10}, {"event": "stop"}]
    _, md = await _serve(frames, playground=AudioInJson)
    kinds = [(m["kind"], m.get("value"), m.get("size")) for m in md["replay_script"]]
    assert kinds == [
        ("json", {"event": "start"}, None),
        ("audio", None, 40),
        ("json", {"event": "stop"}, None),
    ]
    assert "payload" not in str(md["replay_script"])


def test_declared_in_python_and_in_the_manifest():
    spec = Service("chat", websocket("/ws/chat"), graph=echo_flow, max_inflight=8, replay=True)
    assert spec.options["replay"] is True and describe_service(spec)["replay"] is True
    quiet = Service("quiet", websocket("/ws/q"), graph=echo_flow, max_inflight=8)
    assert "replay" not in quiet.options and describe_service(quiet)["replay"] is False
    m = Manifest.from_dict(
        {
            "serve": [
                {
                    "kind": "websocket",
                    "path": "/ws/chat",
                    "graph": "x:y",
                    "max_inflight": 8,
                    "replay": True,
                }
            ]
        }
    )
    assert m.serves[0].options["replay"] is True


@pytest.mark.parametrize(
    "item,kind", [("hi", "text"), ({"k": [1, 2]}, "json"), (7, "json"), (b"ab", "bytes")]
)
def test_to_toy_is_the_inverse_of_to_door(item, kind):
    message = Codec().to_toy(item)
    assert message["kind"] == kind
    if kind != "bytes":
        assert Codec().to_door(message) == item
