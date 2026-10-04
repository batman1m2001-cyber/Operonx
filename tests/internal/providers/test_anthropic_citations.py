"""Anthropic's native citations survive the conversion to OpenAI's shape.

Given ``search_result`` or ``document`` blocks with ``citations:
{"enabled": true}``, Claude answers in several text blocks, and the ones
that rest on a source carry a ``citations`` list. The backend joined the
text and dropped the list, so grounded generation lost its grounding at
the provider boundary.

Now the completion's message carries ``citations``: one span per cited
text block, with the span's ``start``/``end`` character offsets into the
joined ``content`` (``content[start:end]`` is the cited text block), its
Anthropic ``block_index``, and the citations exactly as Anthropic sent
them. A stream ends with the same list on its last chunk.

No network: the responses below have the shapes the Messages API
documents for citations, replayed through a fake HTTP client.
"""

from __future__ import annotations

import copy
import json
from contextlib import asynccontextmanager

import pytest

from operonx.providers.llms.anthropic import AnthropicModel
from operonx.providers.llms.config import AnthropicConfig

pytestmark = pytest.mark.unit

SEARCH_RESULT = {
    "type": "search_result",
    "source": "kb://policies/refunds",
    "title": "Refund policy",
    "content": [{"type": "text", "text": "Refunds are paid within 5 business days."}],
    "citations": {"enabled": True},
}
DOCUMENT = {
    "type": "document",
    "source": {"type": "text", "media_type": "text/plain", "data": "Shipping is free over $50."},
    "title": "Shipping",
    "citations": {"enabled": True},
}
QUESTION = [
    {"role": "system", "content": "Answer from the sources only."},
    {
        "role": "user",
        "content": [SEARCH_RESULT, DOCUMENT, {"type": "text", "text": "Refunds and shipping?"}],
    },
]

SEARCH_CITATION = {
    "type": "search_result_location",
    "source": "kb://policies/refunds",
    "title": "Refund policy",
    "cited_text": "Refunds are paid within 5 business days.",
    "search_result_index": 0,
    "start_block_index": 0,
    "end_block_index": 1,
}
CHAR_CITATION = {
    "type": "char_location",
    "cited_text": "Shipping is free over $50.",
    "document_index": 1,
    "document_title": "Shipping",
    "start_char_index": 0,
    "end_char_index": 26,
}

#: A cited answer: plain text, a cited block, plain text, a cited block.
RESPONSE = {
    "id": "msg_cite",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-5",
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 120, "output_tokens": 30},
    "content": [
        {"type": "text", "text": "According to the policy, "},
        {
            "type": "text",
            "text": "refunds take 5 business days",
            "citations": [SEARCH_CITATION],
        },
        {"type": "text", "text": ", and "},
        {"type": "text", "text": "shipping is free over $50", "citations": [CHAR_CITATION]},
        {"type": "text", "text": "."},
    ],
}
ANSWER = "According to the policy, refunds take 5 business days, and shipping is free over $50."


class _Response:
    def __init__(self, payload=None, lines=None):
        self.status_code = 200
        self._payload = payload
        self._lines = lines or []
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeClient:
    """Records the JSON body; answers with a canned response."""

    def __init__(self, payload=None, lines=None):
        self.payload = payload
        self.lines = lines
        self.sent = None

    async def post(self, url, headers=None, json=None):
        self.sent = json
        return _Response(payload=self.payload)

    @asynccontextmanager
    async def stream(self, method, url, headers=None, json=None):
        self.sent = json
        yield _Response(lines=self.lines)


@pytest.fixture
def model():
    return AnthropicModel(AnthropicConfig(api_key="sk-test", model="claude-sonnet-4-5"))


def _sse(event, data):
    return [f"event: {event}", f"data: {json.dumps(data)}", ""]


def _stream_of(response: dict) -> list:
    """The SSE events the API sends for *response*: each cited block starts
    empty, gets its citations as citations_delta events, then its text."""
    lines = _sse("message_start", {"message": {"id": response["id"], "model": response["model"]}})
    for i, block in enumerate(response["content"]):
        start = {"type": "text", "text": ""}
        if "citations" in block:
            start["citations"] = []
        lines += _sse("content_block_start", {"index": i, "content_block": start})
        for citation in block.get("citations", []):
            lines += _sse(
                "content_block_delta",
                {"index": i, "delta": {"type": "citations_delta", "citation": citation}},
            )
        text = block["text"]
        for part in (text[: len(text) // 2], text[len(text) // 2 :]):
            lines += _sse(
                "content_block_delta", {"index": i, "delta": {"type": "text_delta", "text": part}}
            )
        lines += _sse("content_block_stop", {"index": i})
    lines += _sse("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {}})
    lines += _sse("message_stop", {})
    return lines


EXPECTED = [
    {
        "start": 25,
        "end": 53,
        "block_index": 1,
        "text": "refunds take 5 business days",
        "citations": [SEARCH_CITATION],
    },
    {
        "start": 59,
        "end": 84,
        "block_index": 3,
        "text": "shipping is free over $50",
        "citations": [CHAR_CITATION],
    },
]


# -- the request ------------------------------------------------------------------------


def test_search_result_and_document_blocks_reach_the_api_unchanged(model):
    sent = copy.deepcopy(QUESTION)
    body = model._build_request(sent)
    assert body["messages"] == [
        {"role": "user", "content": [SEARCH_RESULT, DOCUMENT, QUESTION[1]["content"][2]]}
    ]
    assert sent == QUESTION  # the caller's messages are not mutated


def test_search_results_returned_by_a_tool_reach_the_api_unchanged(model):
    """The other place Anthropic accepts them: a tool result's content."""
    turn = [
        {"role": "user", "content": "Refund time?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "toolu_1",
                    "type": "function",
                    "function": {"name": "kb_search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "toolu_1", "content": [SEARCH_RESULT]},
    ]
    body = model._build_request(turn)
    assert body["messages"][-1]["content"][0]["content"] == [SEARCH_RESULT]


# -- the response -----------------------------------------------------------------------


async def test_generate_keeps_citations_with_their_offsets(model):
    model.client = _FakeClient(payload=RESPONSE)
    completion = await model.generate(QUESTION)
    message = completion.choices[0].message
    assert message.content == ANSWER
    assert message.citations == EXPECTED
    for span in message.citations:
        assert message.content[span["start"] : span["end"]] == span["text"]


async def test_an_uncited_answer_carries_no_citations(model):
    model.client = _FakeClient(
        payload={**RESPONSE, "content": [{"type": "text", "text": "No sources needed."}]}
    )
    completion = await model.generate(QUESTION)
    assert getattr(completion.choices[0].message, "citations", None) is None


async def test_text_around_tool_use_and_thinking_keeps_offsets_right(model):
    """Offsets count only the text the answer joins: a thinking or tool_use
    block between text blocks adds no characters."""
    content = [
        {"type": "thinking", "thinking": "look it up", "signature": "sig"},
        {"type": "text", "text": "Per policy, "},
        {"type": "text", "text": "5 days", "citations": [SEARCH_CITATION]},
        {"type": "tool_use", "id": "toolu_2", "name": "log", "input": {}},
    ]
    model.client = _FakeClient(payload={**RESPONSE, "content": content})
    message = (await model.generate(QUESTION)).choices[0].message
    assert message.content == "Per policy, 5 days"
    assert message.citations == [
        {"start": 12, "end": 18, "block_index": 2, "text": "5 days", "citations": [SEARCH_CITATION]}
    ]


async def test_a_stream_ends_with_the_same_citations(model):
    model.client = _FakeClient(lines=_stream_of(RESPONSE))
    chunks = [c async for c in model.stream(QUESTION)]
    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text == ANSWER
    final = chunks[-1].choices[0]
    assert final.finish_reason == "stop"
    assert final.delta.citations == EXPECTED
    assert all(getattr(c.choices[0].delta, "citations", None) is None for c in chunks[:-1])


async def test_an_uncited_stream_carries_no_citations(model):
    plain = {**RESPONSE, "content": [{"type": "text", "text": "No sources needed."}]}
    model.client = _FakeClient(lines=_stream_of(plain))
    chunks = [c async for c in model.stream(QUESTION)]
    assert getattr(chunks[-1].choices[0].delta, "citations", None) is None
