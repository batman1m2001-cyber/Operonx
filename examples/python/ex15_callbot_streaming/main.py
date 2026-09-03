"""15 Callbot Streaming — multi-level streaming pipeline (audio → VAD → STT
→ LLM router → TTS).

Tier 1 — pure compute, no API keys (everything is mocked). Run from this
directory:

    uv sync
    uv run python main.py
"""

from __future__ import annotations

import asyncio

from operonx.core import END, START, Operon, graph, op


@op
async def customer_audio(sample_count: int):
    """Mic input — yields fixed 32 ms audio chunks."""
    for i in range(sample_count):
        await asyncio.sleep(0.005)
        yield {"audio": f"chunk_{i}", "timestamp_ms": i * 32}


@op
async def vad(audio: str, timestamp_ms: int):
    """Voice Activity Detection — N-to-M generator.

    Variable-length speech segments out of fixed-size input. Silence
    chunks yield zero outputs.
    """
    speech_timestamps = {64, 128}
    if timestamp_ms in speech_timestamps:
        yield {
            "segment": f"speech_from_{audio}",
            "start_ms": timestamp_ms,
            "end_ms": timestamp_ms + 32,
        }


@op
def stt(segment: str, start_ms: int, end_ms: int):
    return {"transcript": f"Hello from {segment} [{start_ms}-{end_ms}ms]"}


@op
def classify_intent(transcript: str):
    if "hello" in transcript.lower():
        return {"intent": "greeting", "confidence": 0.95}
    return {"intent": "general", "confidence": 0.8}


@op
def handle_intent(intent: str, transcript: str):
    if intent == "greeting":
        return {"response": "Hello! How can I help you today?"}
    return {"response": f"I understand. Let me help with: {transcript}"}


@graph
def llm_router(transcript):
    """Nested @graph — classify_intent → handle_intent."""
    c = classify_intent(transcript=transcript)
    h = handle_intent(intent=c["intent"], transcript=transcript)
    START >> c >> h >> END


@op
async def tts(response: str):
    """TTS — yields audio chunks word by word."""
    for i, word in enumerate(response.split()):
        await asyncio.sleep(0.003)
        yield {"audio_out": f"tts_{i}_{word}", "index": i}


@graph
def callbot(samples):
    """audio → vad → stt → llm_router → tts."""
    audio = customer_audio(sample_count=samples)
    v = vad(audio=audio["audio"], timestamp_ms=audio["timestamp_ms"])
    transcribe = stt(
        segment=v["segment"],
        start_ms=v["start_ms"],
        end_ms=v["end_ms"],
    )
    router = llm_router(transcript=transcribe["transcript"])
    speak = tts(response=router["response"])
    START >> audio >> v >> transcribe >> router >> speak >> END


async def main() -> None:
    g = callbot(samples=5)
    result = await Operon(g).run(inputs={})
    content = {k: v for k, v in result.items() if k != "$state"}
    print(f"[callbot] {content}")


if __name__ == "__main__":
    asyncio.run(main())


# ── the served front door ───────────────────────────────────────────────
# The same pipeline behind a real socket. `[[serve]]` in operonx.toml
# boots it with `session = "per_connection"`: one websocket, one
# long-lived run, and the run always finishes — the peer disconnecting
# ends `ingress`, the graph drains, and whatever must happen after the
# caller is gone still happens.
#
# `customer_audio` is replaced by the connection itself: each websocket
# message is one audio frame, "payload@timestamp_ms". TTS chunks go back
# out the same socket through `egress`.
from operonx.core.serve import egress, ingress


@op
def unpack_frame(item=None) -> dict:
    """One websocket message → the audio chunk and when it was captured."""
    audio, _, ts = str(item).partition("@")
    return {"audio": audio, "timestamp_ms": int(ts or 0)}


@graph
def served_callbot():
    frames = ingress()
    unpack = unpack_frame(item=frames["item"])
    v = vad(audio=unpack["audio"], timestamp_ms=unpack["timestamp_ms"])
    transcribe = stt(
        segment=v["segment"],
        start_ms=v["start_ms"],
        end_ms=v["end_ms"],
    )
    router = llm_router(transcript=transcribe["transcript"])
    speak = tts(response=router["response"])
    out = egress(item=speak["audio_out"])
    START >> frames >> unpack >> v >> transcribe >> router >> speak >> out >> END

