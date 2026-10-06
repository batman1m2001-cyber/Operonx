"""``@tool`` derives the schema and the validation from one signature.

25 signature shapes (A2: "schema generation across 25 signature shapes"):
each must give a schema both providers accept and validate the arguments
the schema describes. "Accepted" offline means: valid JSON Schema 2020-12
(what Anthropic checks ``input_schema`` against) and the shape OpenAI wants
for function parameters (an object at the top, no ``$ref`` it would have to
resolve). ``tests/live/test_live_models.py`` sends all 25 to a live
OpenAI-compatible gateway once.
"""

from __future__ import annotations

import datetime as dt
import enum
from decimal import Decimal
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Union

import jsonschema
import pydantic
import pytest
from pydantic import BaseModel, Field

from operonx_agents import RunContext, ToolDefinitionError, tool


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Size(int, enum.Enum):
    S = 1
    L = 3


class Address(BaseModel):
    street: str
    city: str = "Hanoi"


class Customer(BaseModel):
    name: str
    address: Address
    tags: List[str] = []


class Node(BaseModel):
    value: int
    children: List["Node"] = []


# ── the 25 shapes ────────────────────────────────────────────────────────
# Each is (tool, an argument dict the schema allows, one it forbids).


def s01(city: str) -> str:
    """Plain string."""


def s02(n: int, x: float, flag: bool) -> str:
    """Scalars."""


def s03(note: Optional[str] = None) -> str:
    """Optional with a None default."""


def s04(days: int = 3) -> str:
    """A default value."""


def s05(unit: Literal["c", "f"]) -> str:
    """A Literal."""


def s06(color: Color) -> str:
    """A string Enum."""


def s07(size: Size) -> str:
    """An int Enum."""


def s08(address: Address) -> str:
    """A nested model."""


def s09(customer: Customer) -> str:
    """A model nested two deep."""


def s10(cities: List[str]) -> str:
    """A list."""


def s11(scores: Dict[str, float]) -> str:
    """A mapping."""


def s12(point: Tuple[float, float]) -> str:
    """A fixed tuple."""


def s13(value: Union[int, str]) -> str:
    """A union."""


def s14(amount: Annotated[Decimal, Field(gt=0)]) -> str:
    """Annotated with a constraint."""


def s15(code: Annotated[str, Field(min_length=8, max_length=8, pattern=r"^[A-Z0-9]+$")]) -> str:
    """A constrained string."""


def s16(when: dt.date) -> str:
    """A date."""


def s17(addresses: List[Address]) -> str:
    """A list of models."""


def s18(ctx: RunContext, order_id: str) -> str:
    """A context parameter, which the model never sees."""


def s19(tree: Node) -> str:
    """A self-referencing model."""


def s20(order_id: str, reason: str = "") -> str:
    """Google style.

    Args:
        order_id: The 8-character order code
            the customer read out.
        reason (str): Why the refund is asked.
    """


def s21(order_id: str) -> str:
    """NumPy style.

    Parameters
    ----------
    order_id : str
        The 8-character order code.
    """


def s22(order_id: str) -> str:
    """Sphinx style.

    :param order_id: The 8-character order code.
    :returns: the order.
    """


def s23(k: Annotated[int, Field(description="How many results.", ge=1, le=10)] = 5) -> str:
    """A description from Field."""


def s24(payload: Any) -> str:
    """No annotation that narrows anything."""


def s25(filters: Optional[List[Dict[str, Union[int, str]]]] = None) -> str:
    """Optional list of mappings of unions."""


SHAPES = [
    (s01, {"city": "Hanoi"}, {}),
    (s02, {"n": 1, "x": 1.5, "flag": True}, {"n": "x", "x": 1, "flag": True}),
    (s03, {}, {"note": 5}),
    (s04, {"days": 7}, {"days": "many"}),
    (s05, {"unit": "c"}, {"unit": "k"}),
    (s06, {"color": "red"}, {"color": "green"}),
    (s07, {"size": 3}, {"size": 2}),
    (s08, {"address": {"street": "1 Trang Tien"}}, {"address": {"city": "Hue"}}),
    (s09, {"customer": {"name": "An", "address": {"street": "x"}}}, {"customer": {"name": "An"}}),
    (s10, {"cities": ["Hue"]}, {"cities": "Hue"}),
    (s11, {"scores": {"a": 1.0}}, {"scores": {"a": "high"}}),
    (s12, {"point": [1.0, 2.0]}, {"point": [1.0]}),
    (s13, {"value": "x"}, {"value": [1]}),
    (s14, {"amount": 10}, {"amount": -1}),
    (s15, {"code": "A1B2C3D4"}, {"code": "short"}),
    (s16, {"when": "2026-10-04"}, {"when": "yesterday"}),
    (s17, {"addresses": [{"street": "x"}]}, {"addresses": [{}]}),
    (s18, {"order_id": "A1"}, {}),
    (s19, {"tree": {"value": 1, "children": [{"value": 2}]}}, {"tree": {"children": []}}),
    (s20, {"order_id": "A1"}, {}),
    (s21, {"order_id": "A1"}, {}),
    (s22, {"order_id": "A1"}, {}),
    (s23, {"k": 3}, {"k": 11}),
    (s24, {"payload": {"anything": [1]}}, {}),
    (s25, {"filters": [{"a": 1, "b": "x"}]}, {"filters": [{"a": [1]}]}),
]
assert len(SHAPES) == 25

TOOLS = [tool(fn) for fn, _, _ in SHAPES]
IDS = [fn.__name__ for fn, _, _ in SHAPES]


def openai_shape_problems(schema: Dict[str, Any]) -> List[str]:
    """What OpenAI's function ``parameters`` rejects: a top level that is
    not an object, or one built from combinators."""
    problems = []
    if schema.get("type") != "object":
        problems.append("top level is not type: object")
    for key in ("anyOf", "oneOf", "allOf", "not", "enum"):
        if key in schema:
            problems.append(f"top-level {key}")
    if "properties" not in schema:
        problems.append("no properties")
    return problems


@pytest.mark.parametrize("t", TOOLS, ids=IDS)
def test_schema_is_valid_json_schema_2020_12(t):
    jsonschema.Draft202012Validator.check_schema(t.spec.params_schema)


@pytest.mark.parametrize("t", TOOLS, ids=IDS)
def test_schema_has_the_shape_openai_takes(t):
    assert openai_shape_problems(t.spec.params_schema) == []


@pytest.mark.parametrize("t", TOOLS, ids=IDS)
def test_schema_carries_no_titles(t):
    assert "title" not in str(t.spec.params_schema).replace("'title': {", "")


@pytest.mark.parametrize("fn,good,bad", SHAPES, ids=IDS)
def test_schema_and_validation_agree(fn, good, bad):
    """What the schema allows validates; what it forbids does not — and
    the JSON Schema says the same as pydantic."""
    t = tool(fn)
    t.validate(good)
    jsonschema.validate(good, t.spec.params_schema)
    if bad or t.spec.params_schema.get("required"):
        with pytest.raises(pydantic.ValidationError):
            t.validate(bad)


def test_refs_are_inlined_unless_recursive():
    assert "$defs" not in tool(s09).spec.params_schema
    assert "$ref" not in str(tool(s09).spec.params_schema)
    assert "$defs" in tool(s19).spec.params_schema  # cannot be inlined


def test_context_parameter_is_not_in_the_schema():
    t = tool(s18)
    assert t.takes_context
    assert list(t.spec.params_schema["properties"]) == ["order_id"]


@pytest.mark.parametrize("fn", [s20, s21, s22], ids=["google", "numpy", "sphinx"])
def test_docstring_styles_describe_the_argument(fn):
    t = tool(fn)
    desc = t.spec.params_schema["properties"]["order_id"]["description"]
    assert desc.startswith("The 8-character order code")
    assert t.spec.description.endswith("style.")


def test_google_continuation_lines_join():
    props = tool(s20).spec.params_schema["properties"]
    assert props["order_id"]["description"] == "The 8-character order code the customer read out."
    assert props["reason"]["description"] == "Why the refund is asked."


def test_field_description_is_kept():
    assert tool(s23).spec.params_schema["properties"]["k"]["description"] == "How many results."


def test_a_property_named_title_survives():
    def f(title: str) -> str:
        """Set a title."""

    assert list(tool(f).spec.params_schema["properties"]) == ["title"]


def test_nested_model_arrives_as_a_model():
    args = tool(s08).validate({"address": {"street": "x"}})
    assert isinstance(args["address"], Address) and args["address"].city == "Hanoi"


def test_unknown_argument_is_refused():
    with pytest.raises(pydantic.ValidationError, match="extra"):
        tool(s01).validate({"city": "Hue", "country": "VN"})


def test_explicit_schema_overrides():
    schema = {"type": "object", "properties": {"city": {"type": "string", "enum": ["Hue"]}}}
    assert tool(s01, schema=schema).spec.params_schema is schema


def test_name_and_description_defaults_and_overrides():
    t = tool(s01)
    assert (t.name, t.spec.description) == ("s01", "Plain string.")
    t = tool(s01, name="weather", description="Get the weather.")
    assert (t.name, t.spec.description) == ("weather", "Get the weather.")


def test_sequential_defaults_to_not_readonly():
    assert tool(s01).spec.sequential is True
    assert tool(s01, readonly=True).spec.sequential is False
    assert tool(s01, readonly=True, sequential=True).spec.sequential is True


def test_the_tool_is_still_its_function():
    @tool
    def add(a: int, b: int) -> int:
        """Add."""
        return a + b

    assert add(2, 3) == 5


class TestDefinitionErrors:
    def test_varargs(self):
        def f(*items: str) -> str:
            """Bad."""

        with pytest.raises(ToolDefinitionError, match=r"\*items"):
            tool(f)

    def test_context_not_first(self):
        def f(order_id: str, ctx: RunContext) -> str:
            """Bad."""

        with pytest.raises(ToolDefinitionError, match="must come first"):
            tool(f)

    def test_no_description(self):
        def f(x: int) -> int:
            return x

        with pytest.raises(ToolDefinitionError, match="no description"):
            tool(f)

    def test_bad_approval(self):
        with pytest.raises(ToolDefinitionError, match="approval='sometimes'"):
            tool(s01, approval="sometimes")
