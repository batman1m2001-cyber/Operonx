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


class TestExtractValueByPath:
    def test_shallow(self):
        assert extract_value_by_path({"a": 1}, ["a"]) == 1

    def test_deep(self):
        assert (
            extract_value_by_path({"a": {"b": {"c": 42}}}, ["a", "b", "c"]) == 42
        )

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
        err = apply_validators(
            result, {"intent": ["confirm", "deny", "@fallback"]}
        )
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
        err = apply_validators(
            result, {"intent": ["confirm", "@fallback"]}
        )
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

    def test_missing_field_becomes_none(self):
        result = parse_and_extract(
            text="<other>1</other>",
            parser="xml",
            fields=self._fields("missing: str"),
        )
        # No validators + missing value → convert_type(None, "str") short-
        # circuits to None (historical ParserOp behaviour).
        assert result["missing"] is None
        assert result["error"] is None

    def test_bool_coercion_from_xml(self):
        result = parse_and_extract(
            text="<flag>true</flag>",
            parser="xml",
            fields=self._fields("flag: bool"),
        )
        assert result["flag"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
