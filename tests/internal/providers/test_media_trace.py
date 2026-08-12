"""Media trace extraction — LLMOp normalize + producer/consumer auto-unwrap.

The collector-based piece of the legacy test is now in
``tests/internal/core/tracing/test_engine_wiring.py`` (event-stream form).
This module covers the parts that still live in the same place under the
new pipeline: the ``normalize_trace_io`` hook on LLMOp + the consumer-side
auto-unwrap.
"""

from __future__ import annotations

import pytest

from operonx.core import END, PARENT, START, GraphOp, Media, Operon, op

# ---------------------------------------------------------------------------
# LLMOp.normalize_trace_io
# ---------------------------------------------------------------------------


class TestLLMOpNormalizeTraceIO:
    def test_image_url_wrapped_as_media(self):
        from operonx.providers.ops import LLMOp

        node = LLMOp(name="vision_test", resource="gpt-4o")
        inputs = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
                        },
                    ],
                }
            ]
        }

        normalized_in, normalized_out = node.normalize_trace_io(inputs, {})
        assert normalized_out == {}
        # Original untouched
        assert isinstance(inputs["messages"][0]["content"][1]["image_url"]["url"], str)

        wrapped = normalized_in["messages"][0]["content"][1]["image_url"]["url"]
        assert isinstance(wrapped, Media)
        assert wrapped.mime_type == "image/png"
        assert wrapped.data.startswith("data:image/png;base64,")

    def test_input_audio_wrapped(self):
        from operonx.providers.ops import LLMOp

        node = LLMOp(name="audio_test", resource="gpt-4o")
        inputs = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": "base64audio", "format": "wav"},
                        }
                    ],
                }
            ]
        }

        normalized_in, _ = node.normalize_trace_io(inputs, {})
        wrapped = normalized_in["messages"][0]["content"][0]["input_audio"]["data"]
        assert isinstance(wrapped, Media)
        assert wrapped.mime_type == "audio/wav"

    def test_no_multimodal_no_change(self):
        from operonx.providers.ops import LLMOp

        node = LLMOp(name="text_test", resource="gpt-4o")
        inputs = {"messages": [{"role": "user", "content": "hello"}]}
        out_in, _ = node.normalize_trace_io(inputs, {})
        # Prompt-formatting path always builds a fresh messages list, so
        # the returned dict is a shallow copy — but content is unchanged
        # (no multimodal blocks means _wrap_openai_media_blocks is a no-op).
        assert out_in["messages"] == inputs["messages"]


# ---------------------------------------------------------------------------
# Producer / consumer auto-unwrap (Ref binding strips Media → bytes)
# ---------------------------------------------------------------------------


@op
def produce_audio(label: str):
    return {"audio": Media(data=b"wav_bytes", mime_type="audio/wav"), "label": label}


@op
def consume_audio(audio: bytes, label: str):
    # Schema type = bytes — confirms auto-unwrap from Media → raw bytes worked.
    assert isinstance(audio, bytes), f"Expected bytes, got {type(audio).__name__}"
    assert audio == b"wav_bytes"
    return {"seen_label": label, "seen_size": len(audio)}


class TestAutoUnwrap:
    @pytest.mark.asyncio
    async def test_producer_media_consumed_as_bytes(self):
        with GraphOp(name="audio_pipeline") as g:
            producer = produce_audio(label=PARENT["label"])
            consumer = consume_audio(audio=producer["audio"], label=producer["label"])
            START >> producer >> consumer >> END

        engine = Operon(g)
        result = await engine.run(inputs={"label": "hello"})
        assert result["seen_size"] == len(b"wav_bytes")
        assert result["seen_label"] == "hello"
