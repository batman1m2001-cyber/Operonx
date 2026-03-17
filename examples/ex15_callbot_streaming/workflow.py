"""Shared workflow definitions for ex15_callbot_streaming.

Defines callbot pipeline ops and graph builder.
Multi-level streaming: audio → VAD → STT → LLM router → TTS.

Examples 1-2: No API keys needed (local trace tree + stream mode).
Example 3: Requires LANGFUSE_HUSH_* keys in .env.
"""

import asyncio

from hush.core import END, PARENT, START, GraphOp, graph, op

# =============================================================================
# Mock ops — simulate a voice callbot pipeline
# =============================================================================


@op
async def customer_audio(sample_count: int):
    """Simulate microphone input — yields fixed-size 32ms audio chunks."""
    for i in range(sample_count):
        await asyncio.sleep(0.005)
        yield {"audio": f"chunk_{i}", "timestamp_ms": i * 32}


@op
async def vad(audio: str, timestamp_ms: int):
    """Voice Activity Detection — N-to-M generator.

    Receives fixed 32ms chunks but yields variable-length speech segments.
    Silence chunks yield nothing (0 outputs).
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
    """Speech-to-Text — transcribe a VAD segment."""
    return {"transcript": f"Hello from {segment} [{start_ms}-{end_ms}ms]"}


@op
def classify_intent(transcript: str):
    """Classify user intent from transcript."""
    if "hello" in transcript.lower():
        return {"intent": "greeting", "confidence": 0.95}
    return {"intent": "general", "confidence": 0.8}


@op
def handle_intent(intent: str, transcript: str):
    """Generate a response based on intent."""
    if intent == "greeting":
        return {"response": "Hello! How can I help you today?"}
    return {"response": f"I understand. Let me help with: {transcript}"}


@graph
def llm_router(transcript):
    """Nested graph: classify intent → generate response."""
    c = classify_intent(transcript=transcript)
    h = handle_intent(intent=c["intent"], transcript=transcript)
    START >> c >> h >> END


@op
async def tts(response: str):
    """Text-to-Speech — yields audio chunks word by word."""
    words = response.split()
    for i, word in enumerate(words):
        await asyncio.sleep(0.003)
        yield {"audio_out": f"tts_{i}_{word}", "index": i}


# =============================================================================
# Graph builder
# =============================================================================


def build_callbot():
    """Build the full callbot pipeline.

    Pipeline:
        customer_audio (yields chunks)
            → vad (yields segments, 0 for silence)
                → stt (transcribe)
                    → llm_router (nested graph: classify → handle)
                        → tts (yields audio output)
    """
    with GraphOp(name="callbot") as g:
        audio = customer_audio(sample_count=PARENT["samples"])
        v = vad(audio=audio["audio"], timestamp_ms=audio["timestamp_ms"])
        transcribe = stt(
            segment=v["segment"],
            start_ms=v["start_ms"],
            end_ms=v["end_ms"],
        )
        router = llm_router(transcript=transcribe["transcript"])
        speak = tts(response=router["response"])

        START >> audio >> v >> transcribe >> router >> speak >> END

    return g
