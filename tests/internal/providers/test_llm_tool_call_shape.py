"""One tool-call shape out of every LLM call: ``{"id", "name", "args"}``.

``LLMOp`` handed each tool call on as the provider's wire object,
``{"id", "type", "function": {"name", "arguments": "<JSON text>"}}``, and
every consumer re-derived the name and parsed the arguments itself, each
its own way: the agents' dispatch read both a flat and a nested form
(``call_identity``), the evals' ``TraceView`` did the same again, and the
compaction summary read only the flat ``name`` — so a real model's calls
were summarised as ``[called: None]``. A streamed call whose fragments
never carried an id came out with ``id: None``.

Now the op parses once, at the provider edge: ``args`` is the arguments
as a dict, or the model's raw text when it is not a JSON object (never a
silent ``{}``, so a consumer can tell the model it wrote bad JSON). The
backends turn the shape back into each provider's wire form when an
assistant message carrying it is sent again.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from operonx.providers.llms.base import normalize_tool_call, openai_message, openai_tool_call
from operonx.providers.ops.llm import LLMOp

pytestmark = pytest.mark.unit

WIRE = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "get_weather", "arguments": '{"city": "Hanoi"}'},
}
NORMAL = {"id": "call_1", "name": "get_weather", "args": {"city": "Hanoi"}}


class _ToolCall:
    """Stands in for the SDK's ``ChatCompletionMessageToolCall``."""

    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return json.loads(json.dumps(self._data))


def _completion(*calls):
    message = SimpleNamespace(
        content="",
        tool_calls=[_ToolCall(c) for c in calls] or None,
        refusal=None,
        reasoning_content=None,
        citations=None,
    )
    choice = SimpleNamespace(message=message, finish_reason="tool_calls", logprobs=None)
    return SimpleNamespace(choices=[choice], usage=None, model="m")


def _op() -> LLMOp:
    op = LLMOp(name="llm", resource="r")
    op._llms = [SimpleNamespace(config=SimpleNamespace())]
    op._fallback_llms = []
    op._initialized = True
    return op


class TestTheShape:
    def test_openai_wire_call_becomes_id_name_args(self):
        assert normalize_tool_call(WIRE) == NORMAL

    def test_already_normal_is_unchanged(self):
        assert normalize_tool_call(NORMAL) == NORMAL

    def test_anthropic_tool_use_block_reads_its_input(self):
        block = {"type": "tool_use", "id": "toolu_1", "name": "f", "input": {"x": 1}}
        assert normalize_tool_call(block) == {"id": "toolu_1", "name": "f", "args": {"x": 1}}

    def test_empty_arguments_are_an_empty_object(self):
        call = {"id": "c", "function": {"name": "now", "arguments": ""}}
        assert normalize_tool_call(call)["args"] == {}

    def test_arguments_that_are_not_json_stay_the_models_text(self):
        """Never a silent ``{}``: the consumer must be able to say it was bad JSON."""
        call = {"id": "c", "function": {"name": "f", "arguments": '{"city": "Han'}}
        assert normalize_tool_call(call)["args"] == '{"city": "Han'

    def test_a_json_array_is_not_arguments(self):
        call = {"id": "c", "function": {"name": "f", "arguments": "[1, 2]"}}
        assert normalize_tool_call(call)["args"] == "[1, 2]"

    def test_wire_form_round_trips(self):
        assert openai_tool_call(NORMAL) == {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Hanoi"}'},
        }
        assert openai_tool_call(WIRE) == WIRE

    def test_raw_text_arguments_go_back_as_written(self):
        bad = {"id": "c", "name": "f", "args": '{"a": '}
        assert openai_tool_call(bad)["function"]["arguments"] == '{"a": '


class TestLLMOpEmitsIt:
    def test_generate_returns_the_one_shape(self):
        result = _op()._extract_completion(_completion(WIRE), "r")
        assert result["tool_calls"] == [NORMAL]

    def test_stream_final_frame_returns_the_one_shape(self):
        acc = LLMOp._new_stream_acc()
        deltas = [
            {"index": 0, "id": "call_1", "type": "function", "function": {"name": "get_weather"}},
            {"index": 0, "function": {"arguments": '{"city": '}},
            {"index": 0, "function": {"arguments": '"Hanoi"}'}},
        ]
        LLMOp._merge_tool_call_deltas([_ToolCall(d) for d in deltas], acc)
        final = _op()._stream_final(acc, "r")
        assert final["tool_calls"] == [NORMAL]


class TestSentBackInTheWireForm:
    def test_openai_message_turns_the_shape_back_into_the_wire_form(self):
        message = {"role": "assistant", "content": "", "tool_calls": [NORMAL]}
        assert openai_message(message)["tool_calls"] == [openai_tool_call(NORMAL)]

    def test_the_callers_message_is_not_mutated(self):
        message = {"role": "assistant", "content": "", "tool_calls": [NORMAL]}
        openai_message(message)
        assert message["tool_calls"] == [NORMAL]

    def test_anthropic_tool_use_block_reads_args(self):
        from operonx.providers.llms.anthropic import _tool_use_block

        assert _tool_use_block(NORMAL) == {
            "type": "tool_use",
            "id": "call_1",
            "name": "get_weather",
            "input": {"city": "Hanoi"},
        }
