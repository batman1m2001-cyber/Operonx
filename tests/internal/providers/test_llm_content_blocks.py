"""A completion whose content is a list of blocks, not a string.

Newer models answer with *parts* — the text, a reasoning signature, a
refusal flag — where older ones sent one string. `LLMOp` promises
``content (str)`` and `parse_and_extract` calls ``.strip()`` on it before
anything else, so a list reaching that far raises ``'list' object has no
attribute 'strip'``.

What makes it worth a test file rather than a line: the raise is caught.
It lands in the op's ``error`` field, ``result`` goes ``None``, and every
consumer downstream reads that as "the model found nothing" — a scanner
that flagged a violation reports a clean call. The 200 was fine, the
model was right, and the pipeline was wrong, quietly.
"""

from types import SimpleNamespace

import pytest

from operonx.providers.ops.llm import _content_to_text, _is_empty_completion


#: What Gemini 3 Flash returns through Databricks' serving endpoint.
GEMINI_BLOCKS = [
    {
        "type": "text",
        "text": '{"result": {"violation": true, "category": "C8"}}',
        "thoughtSignature": "AY89a1/LuaoKl8Y3miQbcibwEkm3xPj",
    }
]


class TestContentToText:
    def test_a_plain_string_is_returned_unchanged(self):
        assert _content_to_text('{"a": 1}') == '{"a": 1}'

    def test_none_becomes_empty(self):
        assert _content_to_text(None) == ""

    def test_the_real_gemini_shape_collapses_to_its_text(self):
        assert _content_to_text(GEMINI_BLOCKS) == (
            '{"result": {"violation": true, "category": "C8"}}'
        )

    def test_the_collapsed_text_is_parseable(self):
        """The whole point: what comes out can be `.strip()`ed and parsed."""
        import json

        text = _content_to_text(GEMINI_BLOCKS)
        assert json.loads(text.strip())["result"]["violation"] is True

    def test_several_text_blocks_join_in_order(self):
        blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        assert _content_to_text(blocks) == "ab"

    def test_blocks_without_text_are_dropped_not_stringified(self):
        """A signature is not generated text; `repr`ing it would corrupt the answer."""
        blocks = [
            {"type": "thinking", "thoughtSignature": "zzz"},
            {"type": "text", "text": "answer"},
        ]
        assert _content_to_text(blocks) == "answer"

    def test_a_block_may_label_its_slot_content(self):
        assert _content_to_text([{"type": "text", "content": "hi"}]) == "hi"

    def test_bare_strings_in_the_list_are_kept(self):
        assert _content_to_text(["a", {"text": "b"}]) == "ab"

    def test_object_blocks_expose_text_as_an_attribute(self):
        assert _content_to_text([SimpleNamespace(text="hi")]) == "hi"

    def test_an_empty_list_collapses_to_empty(self):
        assert _content_to_text([]) == ""


class TestEmptyDetection:
    """`_is_empty_completion` gates the transport retry — it must see blocks too.

    A list carrying real text is not empty and must not be retried into a
    rate limit; a list carrying only a reasoning signature *is* empty, and
    retrying is exactly right.
    """

    @pytest.mark.parametrize(
        "content,expected",
        [
            (GEMINI_BLOCKS, False),
            ([{"type": "thinking", "thoughtSignature": "zzz"}], True),
            ([{"type": "text", "text": "   "}], True),
            ([], True),
            ("text", False),
            ("   ", True),
            (None, True),
        ],
    )
    def test_dict_shape(self, content, expected):
        assert _is_empty_completion({"content": content}) is expected

    def test_raw_completion_shape(self):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=GEMINI_BLOCKS))]
        )
        assert _is_empty_completion(completion) is False

    def test_an_unknown_shape_is_never_called_empty(self):
        """Retrying an unfamiliar-but-valid response would just burn quota."""
        assert _is_empty_completion({"content": 42}) is False


class TestExtractCompletion:
    """End of the path that actually broke: completion in, `content` str out."""

    def _completion(self, content):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content, tool_calls=None, refusal=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
            model="db-gemini-3-flash",
        )

    def test_blocks_are_flattened_before_anything_downstream_sees_them(self):
        from operonx.providers.ops.llm import LLMOp

        op = LLMOp.__new__(LLMOp)
        out = op._extract_completion(self._completion(GEMINI_BLOCKS), "db-gemini-3-flash")
        assert isinstance(out["content"], str), "the op declares content: str"
        assert out["content"].startswith('{"result"')

    def test_a_string_completion_is_untouched(self):
        from operonx.providers.ops.llm import LLMOp

        op = LLMOp.__new__(LLMOp)
        out = op._extract_completion(self._completion("plain"), "gpt-4o")
        assert out["content"] == "plain"
