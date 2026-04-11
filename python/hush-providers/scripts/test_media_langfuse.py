"""End-to-end Langfuse media upload smoke test.

Runs a Hush workflow with two kinds of media producers, attaches
``LangfuseTracer(resource="langfuse:default")``, and waits for the
background flush to finish so you can open the trace in Langfuse and
verify media was uploaded as previewable attachments.

Scenarios:
  1. ``generate_tts`` — custom op returns raw ``Media(wav_bytes, "audio/wav")``.
     Exercises the generic producer path (no LLMOp involvement).
  2. ``vision`` — LLMOp call with an inline 1x1 PNG in OpenAI chat format.
     Exercises ``LLMOp.normalize_trace_io`` wrapping + Langfuse base64
     decode + upload.

What to verify in the Langfuse UI:
  - The trace appears under the workflow name.
  - The TTS output shows an ``audio/wav`` attachment (playable or downloadable).
  - The vision input shows an ``image/png`` attachment (rendered inline).
  - The trace body does NOT contain the raw base64 blob of the PNG.

Usage:
    cd python/hush-providers
    uv run --with langfuse --with anthropic python scripts/test_media_langfuse.py

Requires ``LANGFUSE_PUBLIC_KEY``, ``LANGFUSE_SECRET_KEY``, ``LANGFUSE_HOST``,
and ``OPENAI_API_KEY`` in the repo-root .env file.
"""

from __future__ import annotations

import asyncio
import os
import struct
import sys
from pathlib import Path

# scripts/ -> hush-providers/ -> python/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))
from _common import load_env, section  # noqa: E402


def tiny_png_data_url() -> str:
    """A canonical valid 1x1 transparent PNG as a data URL.

    Small enough (~95 bytes base64) that the trace would also fit inline,
    but the point of the test is that the tracer extracts and uploads it
    as a media attachment instead.
    """
    b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII="
    return "data:image/png;base64," + b64


def tiny_wav_bytes(duration_ms: int = 100) -> bytes:
    """Build a minimal silent WAV (8kHz mono 16-bit) — a few hundred bytes."""
    sample_rate = 8000
    num_samples = sample_rate * duration_ms // 1000
    data = b"\x00\x00" * num_samples
    riff = b"RIFF"
    wave = b"WAVE"
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,
        1,  # PCM
        1,  # mono
        sample_rate,
        sample_rate * 2,
        2,  # block align
        16,  # bits per sample
    )
    data_chunk = struct.pack("<4sI", b"data", len(data)) + data
    body = wave + fmt_chunk + data_chunk
    return riff + struct.pack("<I", len(body)) + body


async def main() -> None:
    load_env()
    for var in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        if not os.environ.get(var):
            raise SystemExit(f"{var} not set in .env")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set in .env (needed for the vision op)")

    # Load resources.yaml so langfuse:default + llm:gpt-4o are available.
    from hush.core import END, PARENT, START, GraphOp, Hush, Media, op
    from hush.core.registry import ResourceHub, set_global_hub

    config_path = REPO_ROOT / "resources.yaml"
    if not config_path.exists():
        raise SystemExit(f"resources.yaml not found at {config_path}")
    hub = ResourceHub.from_yaml(config_path)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    from hush.telemetry.tracers.langfuse import LangfuseTracer

    from hush.providers.ops import LLMOp

    # ------------------------------------------------------------------ ops
    @op
    def generate_tts(text: str):
        """Custom producer — returns raw bytes wrapped in Media."""
        return {
            "audio": Media(data=tiny_wav_bytes(), mime_type="audio/wav"),
            "text": text,
        }

    @op
    def receive_audio(audio: bytes, text: str):
        """Consumer — must see raw bytes, not Media."""
        assert isinstance(audio, bytes), f"auto-unwrap broken: {type(audio).__name__}"
        return {"ok": True, "size": len(audio), "echo": text}

    # ---------------------------------------------------------------- graph
    section("Building workflow: TTS producer + LLM vision")
    with GraphOp(name="media_langfuse_smoke") as workflow:
        tts = generate_tts(text=PARENT["prompt"])
        echo = receive_audio(audio=tts["audio"], text=tts["text"])

        vision = LLMOp(
            name="vision",
            resource="gpt-4o",
            inputs={
                "messages": PARENT["vision_messages"],
                "max_tokens": PARENT["max_tokens"],
            },
            outputs={"content": PARENT["vision_answer"]},
        )

        START >> tts >> echo >> END
        START >> vision >> END
    workflow.build()

    # ------------------------------------------------------------- tracing
    tracer = LangfuseTracer(resource="langfuse:default", tags=["media-smoke"])
    engine = Hush(workflow, tracer=tracer)

    vision_messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image in five words."},
                {
                    "type": "image_url",
                    "image_url": {"url": tiny_png_data_url()},
                },
            ],
        }
    ]

    section("Running workflow")
    result = await engine.run(
        inputs={
            "prompt": "hello from hush media smoke test",
            "vision_messages": vision_messages,
            "max_tokens": 20,
        }
    )
    print(f"  TTS consumer result: {result.get('ok')}, size={result.get('size')}B")
    print(f"  Vision answer: {result.get('vision_answer')!r}")

    # Langfuse tracer flushes on a background thread via FlushWorker. Wait a
    # bit so uploads finish before the process exits.
    section("Waiting for background flush + media upload")
    await asyncio.sleep(6)

    print("\nDone. Open Langfuse and look for trace tagged 'media-smoke'.")
    print("Expected:")
    print("  - TTS node: audio/wav attachment on the output")
    print("  - Vision node: image/png attachment on the input")
    print("  - Raw base64 should NOT appear in the trace body")


if __name__ == "__main__":
    asyncio.run(main())
