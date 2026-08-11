"""``@tool`` registration and the LLM-facing definitions it produces.

Every rejection here guards a *silent* failure. A duplicate name shadows
a tool with no warning; an empty description leaves the model guessing
and the failure looks like a bad model; a malformed schema is rejected by
the provider with an error naming the request rather than the tool.
"""

from __future__ import annotations

import pytest

from operonx.agents.tool import (
    TOOL_REGISTRY,
    clear_registry,
    get_tool_definitions,
    tool,
)

pytestmark = pytest.mark.unit

SCHEMA = {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"]}


@pytest.fixture(autouse=True)
def _clean_registry():
    """TOOL_REGISTRY is process-wide, so a leaked tool changes later tests."""
    clear_registry()
    yield
    clear_registry()


def _make(name="t", **kw):
    @tool(name=name, description=kw.pop("description", "does a thing"), schema=SCHEMA, **kw)
    async def fn(x: float) -> dict:
        return {"y": x * 2}

    return fn


class TestRegistration:
    def test_registers_under_its_name(self):
        fn = _make("double")
        assert TOOL_REGISTRY["double"] is fn

    def test_returns_an_op_factory(self):
        """@tool must not wrap the op in something else — dispatch calls
        `.core()` on the instance to reuse its tracing and bound routing."""
        fn = _make("double")
        node = fn(x=1)
        assert hasattr(node, "core")

    def test_metadata_rides_on_the_factory(self):
        fn = _make("double", destructive=True, max_result_chars=10)
        assert fn._tool_meta["destructive"] is True
        assert fn._tool_meta["max_result_chars"] == 10
        assert fn._tool_meta["qualname"].endswith("fn")

    def test_defaults_are_conservative(self):
        meta = _make("double")._tool_meta
        assert meta["destructive"] is False, "a tool must opt in to bypassing review"
        assert meta["readonly"] is False
        assert meta["timeout"] is None

    def test_op_kwargs_are_forwarded(self):
        fn = _make("double", bound="cpu")
        # Bind before asserting: operonx names an op from the assignment
        # target, and pytest's assertion rewriting would otherwise supply
        # its own temporary (`@py_assert3`), which is not a legal op name.
        node = fn(x=1)
        assert node.bound == "cpu"


class TestRejections:
    def test_duplicate_name(self):
        _make("dup")
        with pytest.raises(ValueError, match=r"already registered"):
            _make("dup")

    def test_duplicate_message_names_the_first_definition(self):
        _make("dup")
        with pytest.raises(ValueError) as exc:
            _make("dup")
        assert "fn" in str(exc.value), "must say which function already holds the name"

    @pytest.mark.parametrize("bad", ["", "   "])
    def test_empty_name(self, bad):
        with pytest.raises(ValueError, match=r"non-empty name"):
            _make(bad)

    @pytest.mark.parametrize("bad", ["", "   "])
    def test_empty_description(self, bad):
        with pytest.raises(ValueError, match=r"needs a description"):
            _make("x", description=bad)

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param("not a dict", id="string"),
            pytest.param({"type": "string"}, id="wrong-type"),
            pytest.param({"properties": {}}, id="no-type"),
        ],
    )
    def test_bad_schema(self, bad):
        with pytest.raises(ValueError, match=r"JSON Schema object"):

            @tool(name="x", description="d", schema=bad)
            async def fn() -> dict:
                return {}

    def test_a_rejected_tool_does_not_register(self):
        with pytest.raises(ValueError):
            _make("x", description="")
        assert TOOL_REGISTRY == {}


class TestDefinitions:
    def test_shape_matches_the_provider_payload(self):
        _make("double")
        (defn,) = get_tool_definitions()
        assert defn == {
            "type": "function",
            "function": {
                "name": "double",
                "description": "does a thing",
                "parameters": SCHEMA,
            },
        }

    def test_subset_preserves_the_order_given(self):
        for n in ("a", "b", "c"):
            _make(n)
        names = [d["function"]["name"] for d in get_tool_definitions(["c", "a"])]
        assert names == ["c", "a"]

    def test_unknown_name_raises_rather_than_dropping(self):
        """Silently dropping would hand a sub-agent a narrower toolset than
        its author wrote, with nothing to explain the behaviour."""
        _make("a")
        with pytest.raises(KeyError, match=r"unregistered tool"):
            get_tool_definitions(["a", "nope"])

    def test_error_lists_what_is_available(self):
        _make("a")
        with pytest.raises(KeyError) as exc:
            get_tool_definitions(["nope"])
        assert "'a'" in str(exc.value)

    def test_empty_registry_yields_no_definitions(self):
        assert get_tool_definitions() == []
