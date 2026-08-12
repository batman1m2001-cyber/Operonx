"""Unit tests for ``operonx.providers.parsing`` — the pure text→struct
helpers used inline by LLMOp (and formerly by the deleted ParserOp).

Covers:
- Raw format parsers (json / xml / yaml) + fence stripping
- ExtractField schema parsing
- Value extraction + type coercion
- Validator application (allow-list + @-prefixed defaults)
- Top-level ``parse_and_extract`` entry point (success + failure paths)
"""

import pytest

from operonx.providers.parsing import (
    ExtractField,
    apply_validators,
    convert_type,
    extract_value_by_path,
    parse_and_extract,
    parse_json,
    parse_xml,
    parse_yaml,
)


class TestRawParsers:
    def test_json_basic(self):
        assert parse_json('{"a": 1}') == {"a": 1}

    def test_json_strips_fence(self):
        assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_xml_single_root(self):
        assert parse_xml("<result>hello</result>") == {"result": "hello"}

    def test_xml_multi_root_wraps(self):
        assert parse_xml("<a>1</a><b>2</b>") == {"a": "1", "b": "2"}

    def test_xml_nested(self):
        assert parse_xml("<r><a>1</a><b>2</b></r>") == {"r": {"a": "1", "b": "2"}}

    def test_xml_strips_fence(self):
        assert parse_xml("```xml\n<r>hi</r>\n```") == {"r": "hi"}

    def test_yaml_basic(self):
        assert parse_yaml("a: 1\nb: two") == {"a": 1, "b": "two"}

    def test_yaml_strips_fence(self):
        assert parse_yaml("```yaml\na: 1\n```") == {"a": 1}


class TestExtractField:
    def test_default_type_when_absent(self):
        f = ExtractField.from_string("result")
        assert f.output_key == "result"
        assert f.chain_path == ["result"]
        assert f.type_hint == "Any"

    def test_typed_field(self):
        f = ExtractField.from_string("result: str")
        assert f.output_key == "result"
        assert f.type_hint == "str"

    def test_nested_path(self):
        f = ExtractField.from_string("user.address.city: str")
        assert f.chain_path == ["user", "address", "city"]
        assert f.output_key == "city"

    def test_required_by_default(self):
        assert ExtractField.from_string("result: str").optional is False

    def test_question_mark_marks_optional(self):
        f = ExtractField.from_string("result?: str")
        assert f.optional is True
        assert f.output_key == "result", "the marker must not leak into the key"
        assert f.chain_path == ["result"]

    def test_optional_on_a_nested_path(self):
        f = ExtractField.from_string("user.city?: str")
        assert f.optional is True
        assert f.chain_path == ["user", "city"]
        assert f.output_key == "city"

    def test_optional_without_a_type_hint(self):
        f = ExtractField.from_string("result?")
        assert f.optional is True
        assert f.output_key == "result"
        assert f.type_hint == "Any"


class TestExtractValueByPath:
    def test_shallow(self):
        assert extract_value_by_path({"a": 1}, ["a"]) == 1

    def test_deep(self):
        assert extract_value_by_path({"a": {"b": {"c": 42}}}, ["a", "b", "c"]) == 42

    def test_missing_returns_none(self):
        assert extract_value_by_path({"a": 1}, ["b"]) is None

    def test_missing_deep_returns_none(self):
        assert extract_value_by_path({"a": 1}, ["a", "b"]) is None


class TestConvertType:
    def test_bool_true_strings(self):
        for s in ("true", "TRUE", "1", "yes"):
            assert convert_type(s, "bool") is True

    def test_bool_false_strings(self):
        for s in ("false", "FALSE", "0", "no", ""):
            assert convert_type(s, "bool") is False

    def test_bool_none(self):
        assert convert_type(None, "bool") is None

    def test_int_ok(self):
        assert convert_type("42", "int") == 42

    def test_int_bad_returns_original(self):
        assert convert_type("abc", "int") == "abc"

    def test_float_ok(self):
        assert convert_type("3.14", "float") == 3.14

    def test_str_strips(self):
        assert convert_type("  hello  ", "str") == "hello"

    def test_none_stays_none(self):
        assert convert_type(None, "int") is None
        assert convert_type(None, "float") is None

    def test_str_none_stays_none(self):
        # Matches historical ParserOp semantics — the early ``if value is
        # None: return None`` short-circuit runs before the str branch.
        assert convert_type(None, "str") is None

    def test_unknown_hint_passes_through(self):
        assert convert_type({"x": 1}, "dict") == {"x": 1}
        assert convert_type([1, 2], "list") == [1, 2]


class TestApplyValidators:
    def test_success_no_change(self):
        result = {"intent": "confirm"}
        err = apply_validators(result, {"intent": ["confirm", "deny"]})
        assert err is None
        assert result == {"intent": "confirm"}

    def test_default_applied_on_mismatch(self):
        result = {"intent": "unknown"}
        err = apply_validators(result, {"intent": ["confirm", "deny", "@fallback"]})
        assert err is None
        assert result["intent"] == "fallback"

    def test_no_default_returns_error(self):
        result = {"intent": "unknown"}
        err = apply_validators(result, {"intent": ["confirm", "deny"]})
        assert err is not None
        assert "not in" in err
        assert result["intent"] == "unknown"  # not mutated on failure

    def test_none_value_uses_default_when_available(self):
        result = {"intent": None}
        err = apply_validators(result, {"intent": ["confirm", "@fallback"]})
        assert err is None
        assert result["intent"] == "fallback"


class TestParseAndExtract:
    def _fields(self, *specs):
        return [ExtractField.from_string(s) for s in specs]

    def test_happy_path_xml(self):
        result = parse_and_extract(
            text="<result>CONFIRM</result>",
            parser="xml",
            fields=self._fields("result: str"),
        )
        assert result == {"result": "CONFIRM", "error": None}

    def test_happy_path_json(self):
        result = parse_and_extract(
            text='{"result": "CONFIRM"}',
            parser="json",
            fields=self._fields("result: str"),
        )
        assert result == {"result": "CONFIRM", "error": None}

    def test_multi_field_type_coercion(self):
        result = parse_and_extract(
            text="<intent>DENY</intent><confidence>0.9</confidence>",
            parser="xml",
            fields=self._fields("intent: str", "confidence: float"),
        )
        assert result["intent"] == "DENY"
        assert result["confidence"] == 0.9
        assert result["error"] is None

    def test_empty_text_returns_error(self):
        result = parse_and_extract(
            text="",
            parser="xml",
            fields=self._fields("x: str"),
        )
        assert result["error"] == "Empty input text"

    def test_bad_xml_returns_parse_error(self):
        # ``parse_xml`` wraps multi-root fragments in ``<root>...</root>`` so
        # plain text like "not xml" accidentally parses. Use unbalanced tags
        # to force a genuine ParseError even after the wrapping retry.
        result = parse_and_extract(
            text="<unclosed>",
            parser="xml",
            fields=self._fields("x: str"),
        )
        assert result["error"] is not None
        assert "Parse error" in result["error"]

    def test_bad_json_returns_parse_error(self):
        result = parse_and_extract(
            text="{not json",
            parser="json",
            fields=self._fields("x: str"),
        )
        assert result["error"] is not None
        assert "Parse error" in result["error"]

    def test_unknown_parser_returns_error(self):
        result = parse_and_extract(
            text="whatever",
            parser="toml",  # type: ignore[arg-type]
            fields=self._fields("x: str"),
        )
        assert result["error"] is not None
        assert "Unknown parser" in result["error"]

    def test_validators_reject_without_default_returns_error(self):
        result = parse_and_extract(
            text="<intent>MAYBE</intent>",
            parser="xml",
            fields=self._fields("intent: str"),
            validators={"intent": ["YES", "NO"]},
        )
        assert result["error"] is not None
        assert "not in" in result["error"]

    def test_validators_with_default_applies_it(self):
        result = parse_and_extract(
            text="<intent>MAYBE</intent>",
            parser="xml",
            fields=self._fields("intent: str"),
            validators={"intent": ["YES", "NO", "@FALLBACK"]},
        )
        assert result["error"] is None
        assert result["intent"] == "FALLBACK"

    def test_validators_wrong_type_returns_error(self):
        result = parse_and_extract(
            text="<x>1</x>",
            parser="xml",
            fields=self._fields("x: str"),
            validators=["not", "a", "dict"],  # type: ignore[arg-type]
        )
        assert result["error"] is not None
        assert "must be a dict" in result["error"]

    def test_missing_field_is_an_error(self):
        """Well-formed output with the wrong keys is a semantic failure.

        It used to return ``{"missing": None, "error": None}``, so
        ``max_retries`` never fired and the caller could not tell "the
        model answered wrongly" from "the model answered null". An earlier
        version of this test asserted the old behaviour and locked it in.
        """
        result = parse_and_extract(
            text="<other>1</other>",
            parser="xml",
            fields=self._fields("missing: str"),
        )
        assert result["missing"] is None
        assert result["error"] is not None
        assert "missing" in result["error"]

    def test_the_error_names_the_keys_that_were_there(self):
        """Without them the message cannot be acted on — the whole point is
        telling the model what it actually produced."""
        result = parse_and_extract(
            text='{"bad": 1}',
            parser="json",
            fields=self._fields("result: str"),
        )
        assert "bad" in result["error"]

    def test_an_explicit_null_is_an_answer_not_an_error(self):
        """The model was asked and said nothing — that is a value, and
        conflating it with a wrong-shaped reply is what F7 was about."""
        result = parse_and_extract(
            text='{"result": null}',
            parser="json",
            fields=self._fields("result: str"),
        )
        assert result["result"] is None
        assert result["error"] is None

    def test_json_with_the_wrong_keys_is_an_error(self):
        result = parse_and_extract(
            text='{"bad": 1}',
            parser="json",
            fields=self._fields("result: str"),
        )
        assert result["error"] is not None

    def test_a_validator_default_counts_as_an_answer(self):
        """An ``@default`` exists so a shaky model still yields a usable
        value; reporting the field as missing would defeat it."""
        result = parse_and_extract(
            text="<other>1</other>",
            parser="xml",
            fields=self._fields("intent: str"),
            validators={"intent": ["YES", "NO", "@FALLBACK"]},
        )
        assert result["intent"] == "FALLBACK"
        assert result["error"] is None

    def test_an_optional_field_may_be_absent(self):
        result = parse_and_extract(
            text="<a>1</a>",
            parser="xml",
            fields=self._fields("a: str", "b?: str"),
        )
        assert result["a"] == "1"
        assert result["b"] is None
        assert result["error"] is None

    def test_a_union_schema_does_not_report_every_call(self):
        """One field list covering several response shapes is a real
        pattern — callbot's ahamove_hr extractor declares twelve fields and
        expects one on a non-compound turn. Without ``?`` every call would
        report missing fields and burn its retries."""
        union = self._fields(
            "result: str",
            "has_cccd?: bool",
            "chosen_date?: str",
            "terminal_cue?: str",
        )
        result = parse_and_extract("<result>CONFIRM</result>", "xml", union)
        assert result["result"] == "CONFIRM"
        assert result["error"] is None
        assert result["has_cccd"] is None

    def test_a_required_field_in_a_union_still_errors(self):
        union = self._fields("result: str", "has_cccd?: bool")
        result = parse_and_extract("<has_cccd>true</has_cccd>", "xml", union)
        assert result["error"] is not None
        assert "result" in result["error"]

    def test_only_some_fields_missing_still_errors(self):
        result = parse_and_extract(
            text="<a>1</a>",
            parser="xml",
            fields=self._fields("a: str", "b: str"),
        )
        assert result["a"] == "1"
        assert result["error"] is not None
        assert "b" in result["error"]


class TestXmlDocumentElement:
    """F6 — XML must have one root, so a field path misses by one level."""

    def _fields(self, *specs):
        return [ExtractField.from_string(s) for s in specs]

    def test_a_wrapped_field_resolves(self):
        """This is the module docstring's own example, which returned
        ``None`` for as long as the docstring has existed."""
        result = parse_and_extract(
            text="<r><result>CONFIRM</result></r>",
            parser="xml",
            fields=self._fields("result: str"),
        )
        assert result == {"result": "CONFIRM", "error": None}

    def test_the_explicit_path_still_works(self):
        result = parse_and_extract(
            text="<r><result>CONFIRM</result></r>",
            parser="xml",
            fields=self._fields("r.result: str"),
        )
        assert result["result"] == "CONFIRM"
        assert result["error"] is None

    def test_a_flat_document_is_unaffected(self):
        result = parse_and_extract(
            text="<result>CONFIRM</result>",
            parser="xml",
            fields=self._fields("result: str"),
        )
        assert result["result"] == "CONFIRM"

    def test_two_roots_are_not_descended_into(self):
        """Descending is only safe for the *one* root XML forces on you."""
        result = parse_and_extract(
            text="<a><x>1</x></a><b><y>2</y></b>",
            parser="xml",
            fields=self._fields("x: str"),
        )
        assert result["error"] is not None

    def test_json_is_not_unwrapped(self):
        """A lone top-level key in JSON is a key the author chose."""
        result = parse_and_extract(
            text='{"data": {"result": "X"}}',
            parser="json",
            fields=self._fields("result: str"),
        )
        assert result["error"] is not None

    def test_repeated_leaf_siblings_are_kept(self):
        """They used to overwrite each other, so only the last survived."""
        assert parse_xml("<items><item>a</item><item>b</item></items>") == {
            "items": {"item": ["a", "b"]}
        }

    def test_a_single_leaf_is_still_a_scalar(self):
        assert parse_xml("<items><item>a</item></items>") == {"items": {"item": "a"}}

    def test_repeated_branch_siblings_still_work(self):
        assert parse_xml("<r><i><a>1</a></i><i><a>2</a></i></r>") == {
            "r": {"i": [{"a": "1"}, {"a": "2"}]}
        }

    def test_bool_coercion_from_xml(self):
        result = parse_and_extract(
            text="<flag>true</flag>",
            parser="xml",
            fields=self._fields("flag: bool"),
        )
        assert result["flag"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
