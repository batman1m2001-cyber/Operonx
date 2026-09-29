"""The Anthropic backend speaks tool calling in OpenAI's shape.

Operon passes OpenAI-shaped messages and tool definitions to every
backend and reads OpenAI-shaped completions back, which is what the
ReAct loop is built on. The Anthropic backend used to drop ``tools=``,
send assistant ``tool_calls`` and ``role: "tool"`` messages through as
if they were plain text turns, and read only the text out of a
``tool_use`` response — so an agent on Claude never called a tool.

No network: requests are built directly, and ``generate()``/``stream()``
go through a fake HTTP client that records the body it was sent.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest

from operonx.providers.llms.anthropic import AnthropicModel
from operonx.providers.llms.config import AnthropicConfig

pytestmark = pytest.mark.unit

WEATHER = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}

TOOL_TURN = [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": "Weather in Hanoi and Hue?"},
    {
        "id": "assistant-1",
        "role": "assistant",
        "content": "Checking.",
        "tool_calls": [
            {
                "id": "toolu_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "Hanoi"}'},
            },
            {
                "id": "toolu_2",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "Hue"}'},
            },
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "toolu_1",
        "name": "get_weather",
        "content": "31C",
        "status": "success",
    },
    {
        "role": "tool",
        "tool_call_id": "toolu_2",
        "name": "get_weather",
        "content": "no such city",
        "status": "error",
    },
]


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


# ── Request ────────────────────────────────────────────────────────────


def test_tools_are_sent_in_anthropic_format(model):
    body = model._build_request([{"role": "user", "content": "Hi"}], tools=[WEATHER])
    assert body["tools"] == [
        {
            "name": "get_weather",
            "description": "Weather for a city.",
            "input_schema": WEATHER["function"]["parameters"],
        }
    ]


def test_no_tools_sends_no_tools_key(model):
    body = model._build_request([{"role": "user", "content": "Hi"}])
    assert "tools" not in body
    assert "tool_choice" not in body


@pytest.mark.parametrize(
    "choice, expected",
    [
        ("auto", {"type": "auto"}),
        ("none", {"type": "none"}),
        ("required", {"type": "any"}),
        (
            {"type": "function", "function": {"name": "get_weather"}},
            {"type": "tool", "name": "get_weather"},
        ),
    ],
)
def test_tool_choice_is_translated(model, choice, expected):
    body = model._build_request(
        [{"role": "user", "content": "Hi"}], tools=[WEATHER], tool_choice=choice
    )
    assert body["tool_choice"] == expected


def test_assistant_tool_calls_become_tool_use_blocks(model):
    body = model._build_request(TOOL_TURN, tools=[WEATHER])
    assistant = body["messages"][1]
    assert assistant == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Checking."},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "get_weather",
                "input": {"city": "Hanoi"},
            },
            {"type": "tool_use", "id": "toolu_2", "name": "get_weather", "input": {"city": "Hue"}},
        ],
    }


def test_tool_messages_become_one_user_message_of_tool_results(model):
    body = model._build_request(TOOL_TURN, tools=[WEATHER])
    assert len(body["messages"]) == 3
    results = body["messages"][2]
    assert results == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "31C"},
            {
                "type": "tool_result",
                "tool_use_id": "toolu_2",
                "content": "no such city",
                "is_error": True,
            },
        ],
    }


def test_assistant_with_only_tool_calls_sends_no_empty_text_block(model):
    turn = [
        {"role": "user", "content": "Hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "t1", "function": {"name": "f", "arguments": ""}}],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "ok"},
    ]
    body = model._build_request(turn)
    assert body["messages"][1]["content"] == [
        {"type": "tool_use", "id": "t1", "name": "f", "input": {}}
    ]


# ── Response ───────────────────────────────────────────────────────────


TOOL_USE_RESPONSE = {
    "id": "msg_1",
    "model": "claude-sonnet-4-5",
    "content": [
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "toolu_9", "name": "get_weather", "input": {"city": "Hanoi"}},
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


@pytest.mark.asyncio
async def test_generate_sends_tools_and_returns_tool_calls(model):
    model.client = _FakeClient(payload=TOOL_USE_RESPONSE)
    completion = await model.generate(TOOL_TURN[:2], tools=[WEATHER])

    assert model.client.sent["tools"][0]["name"] == "get_weather"
    choice = completion.choices[0]
    assert choice.finish_reason == "tool_calls"
    assert choice.message.content == "Let me check."
    [call] = [c.model_dump() for c in choice.message.tool_calls]
    assert call["id"] == "toolu_9"
    assert call["type"] == "function"
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "Hanoi"}


@pytest.mark.asyncio
async def test_generate_forwards_tool_choice(model):
    model.client = _FakeClient(payload=TOOL_USE_RESPONSE)
    await model.generate(TOOL_TURN[:2], tools=[WEATHER], tool_choice="none")
    assert model.client.sent["tool_choice"] == {"type": "none"}


def _sse(event, data):
    return [f"event: {event}", f"data: {json.dumps(data)}", ""]


@pytest.mark.asyncio
async def test_stream_emits_tool_call_deltas(model):
    lines = [
        *_sse("message_start", {"message": {"id": "msg_1", "model": "claude-sonnet-4-5"}}),
        *_sse(
            "content_block_start",
            {
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_9",
                    "name": "get_weather",
                    "input": {},
                },
            },
        ),
        *_sse(
            "content_block_delta",
            {"index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"city": '}},
        ),
        *_sse(
            "content_block_delta",
            {"index": 1, "delta": {"type": "input_json_delta", "partial_json": '"Hanoi"}'}},
        ),
        *_sse("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {}}),
    ]
    model.client = _FakeClient(lines=lines)
    chunks = [c async for c in model.stream(TOOL_TURN[:2], tools=[WEATHER])]

    assert model.client.sent["tools"][0]["name"] == "get_weather"
    deltas = [tc for c in chunks for tc in (c.choices[0].delta.tool_calls or [])]
    assert deltas[0].id == "toolu_9"
    assert deltas[0].function.name == "get_weather"
    assert {d.index for d in deltas} == {0}
    args = "".join(d.function.arguments or "" for d in deltas)
    assert json.loads(args) == {"city": "Hanoi"}
    assert chunks[-1].choices[0].finish_reason == "tool_calls"
