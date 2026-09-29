"""Field extraction edge cases in ``parse_and_extract``.

Each class pins one way a structured answer used to come back wrong with
``error: None`` — a well-formed result the caller had no way to doubt.
"""

from __future__ import annotations

import pytest

from operonx.providers.parsing import ExtractField, parse_and_extract

pytestmark = pytest.mark.unit


def F(*specs: str):
    return [ExtractField.from_string(s) for s in specs]


class TestStructureWhereAScalarWasDeclared:
    """P4 — a subtree under a scalar hint came back as a Python repr.

    ``<action><type>greet</type></action>`` with ``action: str`` walked
    straight to the root's child dict and ``str()`` turned it into
    ``"{'type': 'greet'}"`` — a string no caller asked for, with no error.
    """

    def test_xml_root_named_like_the_field_is_an_error_not_a_repr(self):
        out = parse_and_extract("<action><type>greet</type></action>", "xml", F("action: str"))
        assert out["error"] is not None
        assert "action" in out["error"] and "str" in out["error"]
        assert out.get("action") is None
        assert "{'type'" not in repr(out.get("action"))

    def test_the_same_holds_for_json(self):
        out = parse_and_extract('{"action": {"type": "greet"}}', "json", F("action: str"))
        assert out["error"] is not None
        assert out.get("action") is None

    @pytest.mark.parametrize("hint", ["int", "float", "bool"])
    def test_every_scalar_hint(self, hint):
        """``: int`` used to pass the dict through, ``: bool`` made it True."""
        out = parse_and_extract('{"n": {"v": 1}}', "json", F(f"n: {hint}"))
        assert out["error"] is not None
        assert out.get("n") is None

    def test_a_list_of_subtrees_under_a_scalar_hint(self):
        text = "<r><item><a>1</a></item><item><a>2</a></item></r>"
        out = parse_and_extract(text, "xml", F("item: str"))
        assert out["error"] is not None

    def test_the_root_is_a_wrapper_when_the_field_is_inside_it(self):
        """``<action><action>greet</action>…</action>``: the document element
        shares the field's name, and the scalar the caller declared lives
        one level down. Reading the root as the wrapper is what the XML
        descent rule is for."""
        text = "<action><action>greet</action><why>hello</why></action>"
        out = parse_and_extract(text, "xml", F("action: str", "why: str"))
        assert out == {"action": "greet", "why": "hello", "error": None}

    def test_a_nested_path_still_reaches_the_leaf(self):
        out = parse_and_extract("<action><type>greet</type></action>", "xml", F("action.type: str"))
        assert out == {"type": "greet", "error": None}

    def test_the_lone_root_is_still_descended_for_a_missing_name(self):
        out = parse_and_extract("<action><type>greet</type></action>", "xml", F("type: str"))
        assert out == {"type": "greet", "error": None}

    @pytest.mark.parametrize("hint", ["dict", "Any", "list"])
    def test_a_structural_hint_keeps_the_subtree(self, hint):
        out = parse_and_extract("<action><type>greet</type></action>", "xml", F(f"action: {hint}"))
        assert out == {"action": {"type": "greet"}, "error": None}

    def test_an_untyped_field_keeps_the_subtree(self):
        out = parse_and_extract('{"action": {"type": "greet"}}', "json", F("action"))
        assert out == {"action": {"type": "greet"}, "error": None}

    def test_a_validator_default_stands_in_as_for_any_unrecognised_value(self):
        out = parse_and_extract(
            "<action><type>greet</type></action>",
            "xml",
            F("action: str"),
            validators={"action": ["greet", "@unknown"]},
        )
        assert out == {"action": "unknown", "error": None}

    def test_a_callable_validator_cannot_wave_the_structure_through(self):
        out = parse_and_extract(
            '{"action": {"type": "greet"}}',
            "json",
            F("action: str"),
            validators=lambda parsed: True,
        )
        assert out["error"] is not None

    def test_a_scalar_is_untouched(self):
        out = parse_and_extract("<action>greet</action>", "xml", F("action: str"))
        assert out == {"action": "greet", "error": None}

    def test_repeated_scalar_leaves_still_build_a_list(self):
        out = parse_and_extract("<r><item>a</item><item>b</item></r>", "xml", F("item: str"))
        assert out == {"item": ["a", "b"], "error": None}
