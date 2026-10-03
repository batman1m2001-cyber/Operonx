"""A provider's native citations reach ``extras["citations"]``.

Anthropic returns citations as spans of the answer's text; the provider
adapter puts them on the message (batch) or on the last chunk's delta
(stream), and LLMOp passes them through. Every other provider leaves the
key ``None``, so the extras bag has the same four keys everywhere.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from operonx.core import Operon

from .test_extract_retry import _wf
from .test_llm_stream_fallback import _LLM, MESSAGES, _op, chunk

pytestmark = pytest.mark.unit

CITES = [{"cited_text": "Lotus runs bonded warehouses.", "start": 0, "end": 29, "source": "doc-1"}]


def _hub(citations):
    from openai.types.chat.chat_completion import ChatCompletion, Choice
    from openai.types.chat.chat_completion_message import ChatCompletionMessage
    from openai.types.completion_usage import CompletionUsage

    async def generate(messages, **kwargs):
        message = ChatCompletionMessage(role="assistant", content="Lotus runs bonded warehouses.")
        if citations is not None:
            message.citations = citations
        return ChatCompletion(
            id="c",
            created=0,
            model="fake",
            object="chat.completion",
            choices=[Choice(index=0, message=message, finish_reason="stop")],
            usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    hub = Mock()
    hub.get.return_value = Mock(generate=generate)
    return hub


@pytest.mark.parametrize("citations", [CITES, None])
@pytest.mark.asyncio
async def test_a_batch_answer_carries_the_messages_citations(citations):
    with patch("operonx.providers.ops._utils.ResourceHub") as cls:
        cls.instance.return_value = _hub(citations)
        out = await Operon(_wf(resource="fake", prompt="Who runs it?")).run(inputs={})

    assert out["extras"]["citations"] == citations
    assert set(out["extras"]) == {"thinking_content", "refusal", "logprobs", "citations"}


@pytest.mark.asyncio
async def test_a_stream_carries_the_citations_of_its_last_chunk():
    last = chunk(finish_reason="stop")
    last.choices[0].delta.citations = CITES
    frames = [f async for f in _op(_LLM(chunk("Lotus"), last))._stream_core(messages=MESSAGES)]

    final = frames[-1]
    assert final["final"] is True
    assert final["extras"]["citations"] == CITES
    assert final["full_content"] == "Lotus"


@pytest.mark.asyncio
async def test_a_stream_without_citations_says_none():
    frames = [
        f
        async for f in _op(_LLM(chunk("Hi"), chunk(finish_reason="stop")))._stream_core(
            messages=MESSAGES
        )
    ]
    assert frames[-1]["extras"]["citations"] is None
