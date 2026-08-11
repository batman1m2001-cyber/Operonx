"""Streamed tool calls must be reassembled, not appended.

A provider streams one tool call across many chunks, each carrying the
``index`` it belongs to and a fragment of ``arguments``. Appending the
deltas verbatim produced one broken call per chunk, each holding a
fragment of JSON that parses as nothing:

    [0] id=call_abc  name=get_weather  arguments=null
    [1] arguments='{"city": '
    [2] arguments='"Hanoi'
    [3] arguments='"'
    [4] arguments='}'

Non-streaming responses were always correct, which is why this survived:
streaming worked, tool calling worked, and only the combination — the one
an agent needs — was broken. Found against a live provider, not by any
unit test.
"""

from __future__ import annotations

import pytest

from operonx.providers.ops.llm import LLMOp

pytestmark = pytest.mark.unit


class _Delta:
    """Stands in for the SDK's streamed tool-call delta object."""

    def __init__(self, **data):
        self._data = data

    def model_dump(self):
        return dict(self._data)


def fn(name=None, arguments=None):
    return {"name": name, "arguments": arguments}


def merge(*chunks):
    """Feed chunk-groups through the accumulator, return assembled calls."""
    acc = LLMOp._new_stream_acc()
    for deltas in chunks:
        LLMOp._merge_tool_call_deltas([_Delta(**d) for d in deltas], acc)
    return acc["tool_calls"]


class TestAssembly:
    def test_one_call_across_five_chunks(self):
        calls = merge(
            [
                {
                    "index": 0,
                    "id": "call_abc",
                    "type": "function",
                    "function": fn(name="get_weather"),
                }
            ],
            [{"index": 0, "function": fn(arguments='{"city": ')}],
            [{"index": 0, "function": fn(arguments='"Hanoi')}],
            [{"index": 0, "function": fn(arguments='"')}],
            [{"index": 0, "function": fn(arguments="}")}],
        )
        assert len(calls) == 1, "fragments must merge, not accumulate"
        assert calls[0]["id"] == "call_abc"
        assert calls[0]["function"]["name"] == "get_weather"
        assert calls[0]["function"]["arguments"] == '{"city": "Hanoi"}'

    def test_arguments_parse_as_json(self):
        """The actual requirement — dispatch calls json.loads on this."""
        import json

        calls = merge(
            [{"index": 0, "id": "c1", "function": fn(name="t")}],
            [{"index": 0, "function": fn(arguments='{"a": 1,')}],
            [{"index": 0, "function": fn(arguments=' "b": 2}')}],
        )
        assert json.loads(calls[0]["function"]["arguments"]) == {"a": 1, "b": 2}

    def test_two_parallel_calls_stay_separate(self):
        """Interleaved indices must not merge into each other."""
        calls = merge(
            [
                {"index": 0, "id": "c0", "function": fn(name="first")},
                {"index": 1, "id": "c1", "function": fn(name="second")},
            ],
            [
                {"index": 0, "function": fn(arguments='{"x": 1}')},
                {"index": 1, "function": fn(arguments='{"y": 2}')},
            ],
        )
        assert len(calls) == 2
        assert calls[0]["function"]["arguments"] == '{"x": 1}'
        assert calls[1]["function"]["arguments"] == '{"y": 2}'

    def test_order_follows_first_appearance(self):
        calls = merge(
            [{"index": 1, "id": "b", "function": fn(name="B")}],
            [{"index": 0, "id": "a", "function": fn(name="A")}],
        )
        assert [c["id"] for c in calls] == ["b", "a"]

    def test_trailing_empty_delta_is_harmless(self):
        """Providers send a final delta with everything null."""
        calls = merge(
            [{"index": 0, "id": "c1", "function": fn(name="t")}],
            [{"index": 0, "function": fn(arguments="{}")}],
            [{"index": 0, "function": fn()}],
        )
        assert len(calls) == 1
        assert calls[0]["function"]["arguments"] == "{}"


class TestFieldPrecedence:
    def test_first_non_empty_id_wins(self):
        """id arrives once, on the first fragment; later nulls must not
        erase it."""
        calls = merge(
            [{"index": 0, "id": "call_abc", "function": fn(name="t")}],
            [{"index": 0, "id": None, "function": fn(arguments="{}")}],
        )
        assert calls[0]["id"] == "call_abc"

    def test_first_non_empty_name_wins(self):
        calls = merge(
            [{"index": 0, "id": "c", "function": fn(name="real_name")}],
            [{"index": 0, "function": fn(name=None, arguments="{}")}],
        )
        assert calls[0]["function"]["name"] == "real_name"

    def test_type_defaults_to_function(self):
        calls = merge([{"index": 0, "id": "c", "function": fn(name="t")}])
        assert calls[0]["type"] == "function"

    def test_name_arriving_late_is_still_taken(self):
        calls = merge(
            [{"index": 0, "id": "c", "function": fn()}],
            [{"index": 0, "function": fn(name="late")}],
        )
        assert calls[0]["function"]["name"] == "late"


class TestDegenerate:
    def test_no_index_is_kept_whole(self):
        """Nothing to merge on — keep it rather than guess which call it
        belongs to."""
        calls = merge([{"id": "c", "function": fn(name="t", arguments="{}")}])
        assert len(calls) == 1
        assert calls[0]["id"] == "c"

    def test_empty_delta_list_changes_nothing(self):
        assert merge([]) == []

    def test_scratch_state_does_not_reach_the_output(self):
        """`_tool_call_index` is bookkeeping; only `tool_calls` is read
        into the op's outputs."""
        acc = LLMOp._new_stream_acc()
        LLMOp._merge_tool_call_deltas([_Delta(index=0, id="c", function=fn(name="t"))], acc)
        assert "_tool_call_index" in acc
        assert all("index" not in call for call in acc["tool_calls"])

    def test_fresh_accumulator_per_stream(self):
        """Two streams must not merge into each other — the index map is
        per-accumulator, and a shared one would splice unrelated calls."""
        first = merge([{"index": 0, "id": "a", "function": fn(name="A", arguments="{}")}])
        second = merge([{"index": 0, "id": "b", "function": fn(name="B", arguments="{}")}])
        assert first[0]["id"] == "a" and second[0]["id"] == "b"
        assert len(first) == len(second) == 1
