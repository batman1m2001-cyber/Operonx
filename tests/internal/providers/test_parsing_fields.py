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


class TestOutputKeyCollisions:
    """P5 — the output key is the path's last segment, so two paths ending
    in the same leaf wrote one key: ``["user.id: str", "order.id: str"]``
    returned ``{"id": <order's>}``, last writer wins, ``error: None``."""

    def test_two_paths_with_one_leaf_are_reported(self):
        out = parse_and_extract(
            '{"user": {"id": "u1"}, "order": {"id": "o1"}}',
            "json",
            F("user.id: str", "order.id: str"),
        )
        assert out["error"] is not None
        assert "'id'" in out["error"]
        assert "user.id" in out["error"] and "order.id" in out["error"]

    def test_the_collision_is_a_build_time_error_on_the_op(self):
        from operonx.providers.ops import LLMOp

        with pytest.raises(ValueError, match=r"'id'.*user\.id.*order\.id"):
            LLMOp(name="x", resource="r", fields=["user.id: str", "order.id: str"])

    @pytest.mark.parametrize("name", ["error", "content", "usage", "final"])
    def test_a_field_cannot_shadow_an_output_of_the_op(self, name):
        """``error`` is overwritten by the parse result, ``content`` by the
        parsed field — either way one of the two values is lost."""
        from operonx.providers.ops import LLMOp

        with pytest.raises(ValueError, match=f"'{name}'"):
            LLMOp(name="x", resource="r", fields=[f"{name}: str"])

    def test_an_alias_names_the_output(self):
        f = ExtractField.from_string("user.id as user_id: str")
        assert (f.output_key, f.chain_path, f.type_hint, f.optional) == (
            "user_id",
            ["user", "id"],
            "str",
            False,
        )

    @pytest.mark.parametrize("spec", ["user.id as uid?: str", "user.id? as uid: str"])
    def test_an_aliased_field_can_be_optional(self, spec):
        f = ExtractField.from_string(spec)
        assert (f.output_key, f.chain_path, f.optional) == ("uid", ["user", "id"], True)

    def test_aliases_resolve_the_collision(self):
        out = parse_and_extract(
            '{"user": {"id": "u1"}, "order": {"id": "o1"}}',
            "json",
            F("user.id as user_id: str", "order.id as order_id: str"),
        )
        assert out == {"user_id": "u1", "order_id": "o1", "error": None}

    def test_the_op_declares_the_aliased_outputs(self):
        from operonx.providers.ops import LLMOp

        op = LLMOp(name="x", resource="r", fields=["user.id as user_id: str", "order.id as oid"])
        assert {"user_id", "oid"} <= set(op.outputs)
        assert "id" not in op.outputs

    def test_a_missing_aliased_field_is_named_by_its_path(self):
        out = parse_and_extract('{"user": {}}', "json", F("user.id as uid: str"))
        assert out["error"] is not None and "user.id" in out["error"]

    def test_a_default_fills_a_missing_aliased_field(self):
        """The strike-off from ``missing`` used the path's last segment as
        the key, which an alias makes wrong."""
        out = parse_and_extract(
            '{"user": {}}',
            "json",
            F("user.id as uid: str"),
            validators={"uid": ["a", "@anon"]},
        )
        assert out == {"uid": "anon", "error": None}

    @pytest.mark.parametrize(
        "spec", ["user.id as : str", "user.id as a.b: str", "user.id as uid extra: str"]
    )
    def test_a_malformed_alias_is_rejected(self, spec):
        with pytest.raises(ValueError):
            ExtractField.from_string(spec)

    def test_a_key_named_as_is_still_a_path(self):
        f = ExtractField.from_string("meta.as: str")
        assert (f.output_key, f.chain_path) == ("as", ["meta", "as"])
