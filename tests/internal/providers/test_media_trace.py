"""End-to-end tests for Media trace extraction.

Covers:
  - LLMOp.normalize_trace_io wraps OpenAI multimodal blocks as Media
  - BaseOp auto-unwrap: consumer ops receive raw bytes when producer returns Media
  - Collector extracts Media into node.media with correct field_path
"""

from __future__ import annotations

import pytest
from operonx.core import END, PARENT, START, GraphOp, Operon, Media, op
from operonx.core.tracing.collector import TraceCollector

# --------------------------------------------------------------------------- #
# LLMOp.normalize_trace_io unit test (no real LLM call)
# --------------------------------------------------------------------------- #


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
        assert out_in is inputs  # cheap identity check — no copy

    def test_mixed_content_no_image(self):
        from operonx.providers.ops import LLMOp

        node = LLMOp(name="mixed_test", resource="gpt-4o")
        inputs = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ]
        }
        out_in, _ = node.normalize_trace_io(inputs, {})
        assert out_in is inputs  # no change


# --------------------------------------------------------------------------- #
# Producer/consumer auto-unwrap
# --------------------------------------------------------------------------- #


@op
def produce_audio(label: str):
    return {"audio": Media(data=b"wav_bytes", mime_type="audio/wav"), "label": label}


@op
def consume_audio(audio: bytes, label: str):
    # Consumer schema type is bytes — confirms auto-unwrap worked.
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


# --------------------------------------------------------------------------- #
# Collector media extraction
# --------------------------------------------------------------------------- #


class TestCollectorExtractsMedia:
    @pytest.mark.asyncio
    async def test_media_surfaces_in_node_media(self):
        with GraphOp(name="media_graph") as g:
            producer = produce_audio(label=PARENT["label"])
            consumer = consume_audio(audio=producer["audio"], label=producer["label"])
            START >> producer >> consumer >> END

        engine = Operon(g)
        handle = engine.start(inputs={"label": "hi"})
        await handle.collect()
        state = handle.state

        collector = TraceCollector(g)
        trace_data = collector.collect(state)

        # Find the producer node in the collected trace — it returned a
        # top-level Media in outputs, which should appear on node.media.
        producer_nodes = [
            n for n in trace_data["nodes"] if n.get("op_name", "").endswith("producer")
        ]
        assert producer_nodes, "producer node not found in trace"
        prod = producer_nodes[0]

        # outputs should carry a placeholder, NOT the raw bytes
        assert prod["outputs"]["audio"].startswith("<media:")
        # media list should have the extracted blob
        media_list = prod["media"]
        assert len(media_list) == 1
        blob = media_list[0]
        assert blob["field_path"] == "outputs.audio"
        assert blob["data"] == b"wav_bytes"
        assert blob["mime_type"] == "audio/wav"
