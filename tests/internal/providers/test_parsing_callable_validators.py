"""``validators=`` as a predicate over the whole parsed dict.

The allow-list form answers "is this field one of these values". Some
checks are structural instead — "``result`` must be a dict carrying a
``violation`` key" — and cannot be stated per field. A callable covers
those, and a rejection drives the same semantic retry an allow-list
failure does.
"""

import pytest

from operonx.providers.parsing import ExtractField, apply_validators, parse_and_extract


def _scanner_shape_ok(parsed: dict) -> bool:
    """The real shape check this feature exists for."""
    result = parsed.get("result")
    return isinstance(result, dict) and "violation" in result


class TestCallableValidator:
    def test_accepts(self):
        assert apply_validators({"result": {"violation": True}}, _scanner_shape_ok) is None

    def test_rejects_with_a_named_error(self):
        err = apply_validators({"result": "not a dict"}, _scanner_shape_ok)
        assert err is not None
        assert "_scanner_shape_ok" in err

    def test_missing_key_rejected(self):
        err = apply_validators({"result": {"reason": "x"}}, _scanner_shape_ok)
        assert err is not None

    def test_a_raising_validator_is_a_rejection_not_a_crash(self):
        """It runs on model output; a malformed answer must not take the
        graph down with it."""

        def explodes(parsed):
            return parsed["nope"]["deeper"]

        err = apply_validators({}, explodes)
        assert err is not None
        assert "KeyError" in err
        assert "explodes" in err

    def test_lambda_is_accepted(self):
        assert apply_validators({"n": 5}, lambda d: d["n"] > 1) is None
        assert apply_validators({"n": 0}, lambda d: d["n"] > 1) is not None

    def test_does_not_mutate_the_parsed_dict(self):
        parsed = {"result": {"violation": False}}
        apply_validators(parsed, _scanner_shape_ok)
        assert parsed == {"result": {"violation": False}}


class TestAllowListStillWorks:
    def test_value_in_list(self):
        assert apply_validators({"a": "X"}, {"a": ["X", "Y"]}) is None

    def test_value_not_in_list(self):
        assert apply_validators({"a": "Z"}, {"a": ["X", "Y"]}) is not None

    def test_at_prefixed_default_substituted(self):
        result = {"a": "Z"}
        assert apply_validators(result, {"a": ["X", "@Y"]}) is None
        assert result["a"] == "Y"


class TestParseAndExtractIntegration:
    def test_callable_rejection_surfaces_as_error(self):
        out = parse_and_extract(
            text='{"result": "wrong type"}',
            parser="json",
            fields=[ExtractField.from_string("result: dict")],
            validators=_scanner_shape_ok,
        )
        assert out["error"] is not None

    def test_callable_acceptance_returns_fields(self):
        out = parse_and_extract(
            text='{"result": {"violation": true, "reason": "r"}}',
            parser="json",
            fields=[ExtractField.from_string("result: dict")],
            validators=_scanner_shape_ok,
        )
        assert out["error"] is None
        assert out["result"]["violation"] is True

    @pytest.mark.parametrize("bad", ["a string", 42, ["a", "list"]])
    def test_non_dict_non_callable_is_rejected_by_the_guard(self, bad):
        out = parse_and_extract(
            text='{"a": 1}',
            parser="json",
            fields=[ExtractField.from_string("a: int")],
            validators=bad,
        )
        assert out["error"] is not None
        assert "dict or a callable" in out["error"]
