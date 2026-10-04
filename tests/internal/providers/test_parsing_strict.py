"""Structured output that fails loudly (roadmap C6).

Every case of ``docs/roadmap/evidence/repros/parse_probe.py``, each with
what it returned on 1.14.0:

- ``xml_amp``: a bare ``&`` in text made the whole answer a parse error;
- ``xml_int_float``: ``urgency: int`` held the string ``"2.5"`` and
  ``ok: bool`` was True for ``"nah"``, with ``error: None``;
- ``json_fence_tail`` / ``json_preamble``: any text around the JSON was a
  parse error;
- ``xml_one_tag_list``: ``tags: list`` was ``"a"`` with one ``<tags>`` and
  ``['a', 'b']`` with two, so the type depended on the data.

A coercion that fails is a field error, so ``LLMOp(max_retries=...)`` asks
again instead of returning a plausible wrong value.
"""

from unittest.mock import patch

import pytest

from operonx.core import Operon
from operonx.providers.parsing import ExtractField, convert_type, parse_and_extract

from .test_extract_retry import _wf, make_mock_hub

# Mock-only: opts out of this directory's integration auto-mark (conftest.py).
pytestmark = pytest.mark.unit

FIELDS = [
    ExtractField.from_string(s) for s in ["summary: str", "urgency: int", "ok: bool", "tags: list"]
]

GOOD = {"summary": "s", "urgency": 2, "ok": True, "tags": ["a"], "error": None}


def _run(parser, text):
    return parse_and_extract(text, parser, FIELDS)


# ── parse_probe.py, case by case ───────────────────────────────────────


def test_xml_amp():
    out = _run(
        "xml", "<r><summary>Q&A broken</summary><urgency>2</urgency><ok>yes</ok><tags>a</tags></r>"
    )
    assert out == {**GOOD, "summary": "Q&A broken"}


def test_xml_preamble():
    out = _run(
        "xml",
        "Here you go:\n<r><summary>s</summary><urgency>2</urgency><ok>no</ok><tags>a</tags></r>",
    )
    assert out == {**GOOD, "ok": False}


def test_xml_int_float():
    out = _run("xml", "<r><summary>s</summary><urgency>2.5</urgency><ok>nah</ok><tags>a</tags></r>")
    assert out["error"] is not None
    assert "'urgency' is declared int" in out["error"] and "'2.5'" in out["error"]
    assert "'ok' is declared bool" in out["error"] and "'nah'" in out["error"]
    assert out["urgency"] is None and out["ok"] is None
    assert out["summary"] == "s"  # the fields that did coerce are kept


def test_json_fence_tail():
    out = _run(
        "json", '```json\n{"summary":"s","urgency":2,"ok":true,"tags":["a"]}\n```\nHope this helps'
    )
    assert out == GOOD


def test_json_preamble():
    out = _run("json", 'Result: {"summary":"s","urgency":2,"ok":true,"tags":["a"]}')
    assert out == GOOD


def test_xml_one_tag_list():
    out = _run("xml", "<r><summary>s</summary><urgency>2</urgency><ok>1</ok><tags>a</tags></r>")
    assert out == GOOD


def test_xml_two_tag_list():
    out = _run(
        "xml",
        "<r><summary>s</summary><urgency>2</urgency><ok>1</ok><tags>a</tags><tags>b</tags></r>",
    )
    assert out == {**GOOD, "tags": ["a", "b"]}


# ── The rules behind them ──────────────────────────────────────────────


@pytest.mark.parametrize("text", ["true", "TRUE", " Yes ", "1", 1, True])
def test_bool_accepts_the_true_spellings(text):
    assert convert_type(text, "bool") is True


@pytest.mark.parametrize("text", ["false", "False", "no", "NO", "0", 0, False])
def test_bool_accepts_the_false_spellings(text):
    assert convert_type(text, "bool") is False


@pytest.mark.parametrize("text", ["nah", "maybe", "", "2", 2, "y", "t"])
def test_bool_refuses_anything_else(text):
    with pytest.raises(ValueError, match="true/false/yes/no/1/0"):
        convert_type(text, "bool")


@pytest.mark.parametrize(
    "value, hint",
    [
        ("high", "int"),
        ("2.5", "int"),
        (2.5, "int"),
        (True, "int"),
        ("fast", "float"),
        (False, "float"),
    ],
)
def test_number_coercion_failure_raises(value, hint):
    with pytest.raises(ValueError):
        convert_type(value, hint)


@pytest.mark.parametrize(
    "value, hint, want",
    [("2", "int", 2), (2.0, "int", 2), ("2.5", "float", 2.5), (3, "float", 3.0)],
)
def test_number_coercion_success(value, hint, want):
    assert convert_type(value, hint) == want


def test_list_hint_wraps_a_lone_value():
    assert convert_type("a", "list") == ["a"]
    assert convert_type(["a", "b"], "list") == ["a", "b"]
    assert convert_type(None, "list") is None


def test_json_first_fenced_block_wins_over_later_text():
    text = 'Use this:\n```json\n{"summary":"s","urgency":2,"ok":true,"tags":["a"]}\n```\nnot {"summary":"x"}'
    assert _run("json", text) == GOOD


def test_json_first_balanced_object_with_braces_in_strings():
    text = 'Answer: {"summary":"a } b","urgency":2,"ok":true,"tags":["a"]} -- done {"x": 1}'
    assert _run("json", text) == {**GOOD, "summary": "a } b"}


def test_json_object_preferred_over_an_earlier_bracket():
    text = 'See note [1]: {"summary":"s","urgency":2,"ok":true,"tags":["a"]}'
    assert _run("json", text) == GOOD


def test_json_with_nothing_to_parse_is_a_parse_error():
    assert _run("json", "no json here")["error"].startswith("Parse error (json)")


def test_xml_entities_are_not_double_escaped():
    out = _run(
        "xml",
        "<r><summary>a &amp; b &lt; c</summary><urgency>2</urgency><ok>yes</ok><tags>a</tags></r>",
    )
    assert out["summary"] == "a & b < c" and out["error"] is None


def test_xml_amp_in_a_fence_after_preamble():
    text = "Sure:\n```xml\n<r><summary>R&D</summary><urgency>1</urgency><ok>no</ok><tags>a</tags></r>\n```"
    assert _run("xml", text) == {**GOOD, "summary": "R&D", "urgency": 1, "ok": False}


def test_validator_default_stands_in_for_an_uncoercible_value():
    fields = [ExtractField.from_string("ok: bool")]
    out = parse_and_extract(
        "<ok>nah</ok>", "xml", fields, validators={"ok": [True, False, "@False"]}
    )
    assert out == {"ok": "False", "error": None}


# ── The point of all this: max_retries fires ──────────────────────────


@pytest.mark.asyncio
async def test_failed_coercion_triggers_semantic_retry():
    mock_hub, calls = make_mock_hub(["<urgency>high</urgency>", "<urgency>3</urgency>"])
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            prompt="Rate: {text}",
            fields=["urgency: int"],
            parser="xml",
            max_retries=1,
            text="server down",
        )
        result = await Operon(g).run(inputs={})

    assert calls["n"] == 2
    assert result["urgency"] == 3
    assert result["error"] is None
    retry_prompt = str(calls["messages_history"][1])
    assert "declared int" in retry_prompt  # the hint tells the model what was wrong
