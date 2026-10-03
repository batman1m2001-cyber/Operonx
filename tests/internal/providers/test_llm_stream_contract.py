"""The frames of ``LLMOp(stream=True)`` add up to the answer exactly once.

Up to 1.14 the closing frame (``final=True``) repeated the whole answer in
``content``, the key every delta uses, so a consumer that forwarded each
frame's ``content`` — a websocket, a voice app — sent the answer twice
unless it knew to filter on ``final`` (roadmap C10, dogfood F07). Now
``content`` is always the delta: empty on the closing frame, which carries
the whole text as ``full_content``. A batch call's one frame is both, so
``full_content`` reads the whole answer in either mode.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon
from operonx.providers.ops import LLMOp

from .test_extract_retry import _wf, make_mock_hub
from .test_llm_stream_fallback import _LLM, MESSAGES, _op, chunk

pytestmark = pytest.mark.unit


async def _frames(op):
    return [f async for f in op._stream_core(messages=MESSAGES)]


@pytest.mark.asyncio
async def test_joining_every_frame_gives_the_answer_once():
    frames = await _frames(_op(_LLM(chunk("Hel"), chunk("lo"), chunk(finish_reason="stop"))))

    assert "".join(f["content"] for f in frames) == "Hello"
    final = frames[-1]
    assert final["final"] is True
    assert final["content"] == ""
    assert final["full_content"] == "Hello"
    assert final["finish_reason"] == "stop"
    assert all(not f["final"] for f in frames[:-1])


@pytest.mark.asyncio
async def test_fallback_final_frame_follows_the_same_contract():
    primary = _LLM(fail=ConnectionError("down"))
    frames = await _frames(_op(primary, _LLM(chunk("Hi."), chunk(finish_reason="stop"))))

    assert "".join(f["content"] for f in frames) == "Hi."
    assert frames[-1]["full_content"] == "Hi."
    assert frames[-1]["model_used"] == "fb0"


@pytest.mark.asyncio
async def test_a_run_reads_the_whole_answer_from_full_content():
    with GraphOp(name="streamed") as g:
        llm = LLMOp(name="llm", resource="primary", stream=True, inputs={"messages": PARENT["m"]})
        START >> llm >> END
    llm._llms = [_LLM(chunk("Hel"), chunk("lo"), chunk(finish_reason="stop"))]
    llm._initialized = True

    out = await Operon(g).run(inputs={"m": MESSAGES})
    assert out["full_content"] == "Hello"


@pytest.mark.asyncio
async def test_a_batch_call_has_full_content_too():
    mock_hub, _ = make_mock_hub(["Hello"])
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        out = await Operon(_wf(resource="mock", prompt="hi")).run(inputs={})

    assert out["content"] == out["full_content"] == "Hello"
