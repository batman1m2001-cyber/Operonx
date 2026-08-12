"""Pure text-parsing helpers used by LLMOp's structured-output layer.

Extracted from the (now-deleted) ``ParserOp`` so that LLMOp can call them
inline instead of chaining a separate op. Users who want plain text → struct
extraction without an LLM call can import these directly.

Contract of the top-level ``parse_and_extract`` entry point:

    result = parse_and_extract(
        text="<r><result>CONFIRM</result></r>",
        parser="xml",
        fields=["result: str"],
        validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
    )
    # → {"result": "CONFIRM", "error": None}
    # OR {"result": None, "error": "Parse error (xml): ..."}
    # OR {"result": "FALLBACK", "error": None}   # @FALLBACK default applied
    # OR {"result": None, "error": "Missing field(s) in xml output: result"}

The function ALWAYS returns a dict with the requested field keys plus an
``error`` key that is either ``None`` (success) or a human-readable string
(failure). It never raises — LLMOp uses the ``error`` value to decide
whether to trigger a semantic-retry.

Two rules that are easy to get backwards:

- **A field the payload does not contain is an error**, so ``max_retries``
  can fire. A field the payload sets to ``null`` is an answer, and is not.
  Mark a field ``"name?: type"`` when absence is expected — a *union
  schema*, where one field list covers several response shapes, needs
  this on every entry that is not always present.
- **An XML document element is stripped when it gets in the way.** XML must
  have exactly one root, so a path written against the payload
  (``"result"``) is matched inside a lone root as well as at the top. JSON
  and YAML get no such treatment — there a single top-level key is a key
  the author meant.
"""

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

import yaml

__all__ = [
    "ParserFormat",
    "ExtractField",
    "parse_json",
    "parse_xml",
    "parse_yaml",
    "MISSING",
    "extract_value_by_path",
    "convert_type",
    "apply_validators",
    "parse_and_extract",
]

ParserFormat = Literal["json", "xml", "yaml"]


# ---------------------------------------------------------------------------
# Field schema
# ---------------------------------------------------------------------------


@dataclass
class ExtractField:
    """A field to pull out of parsed text.

    Attributes:
        output_key: Key under which the extracted value is returned.
        chain_path: Dot-separated path into the parsed dict.
        type_hint: Type name used for coercion (``str`` / ``int`` / ``bool`` / ...).
        optional: When True, absence is an answer rather than an error.
    """

    output_key: str
    chain_path: List[str]
    type_hint: str
    optional: bool = False

    @classmethod
    def from_string(cls, schema_str: str) -> "ExtractField":
        """Parse a schema string like ``"user.address.city: str"``.

        Missing type hint defaults to ``Any``.

        A ``?`` before the colon marks the field optional::

            "result: str"        # required — absence is a parse error
            "chosen_date?: str"  # optional — absence yields None

        Optional matters for a **union schema**, where one field list
        covers several response shapes and most entries are expected to be
        absent on any given call. Without the marker every such call would
        report missing fields and burn its retries.
        """
        if ":" not in schema_str:
            schema_str += ": Any"
        chain_text, type_hint = schema_str.split(":", 1)
        chain_text = chain_text.strip()
        optional = chain_text.endswith("?")
        if optional:
            chain_text = chain_text[:-1].strip()
        chain_path = chain_text.split(".")
        return cls(
            output_key=chain_path[-1],
            chain_path=chain_path,
            type_hint=type_hint.strip(),
            optional=optional,
        )


# ---------------------------------------------------------------------------
# Raw parsers (each strips a leading ``` fence if present).
# ---------------------------------------------------------------------------


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    return text


def parse_json(text: str) -> Dict[str, Any]:
    """Parse a JSON payload, tolerating a leading ``` fence."""
    return json.loads(_strip_fence(text))


def parse_xml(text: str) -> Dict[str, Any]:
    """Parse an XML payload into a nested dict, tolerating a leading ``` fence.

    Supports both single-root and multi-root inputs. Multi-root wraps in a
    ``<root>`` element and returns the flattened children.
    """

    def xml_to_dict(element):
        result = {}
        for child in element:
            # Leaves and branches take the same repeat handling. They used
            # not to: a leaf reassigned ``result[tag]``, so
            # ``<item>a</item><item>b</item>`` kept only ``"b"`` and lost
            # the rest without a word.
            value = child.text if len(child) == 0 else xml_to_dict(child)
            if child.tag in result:
                if not isinstance(result[child.tag], list):
                    result[child.tag] = [result[child.tag]]
                result[child.tag].append(value)
            else:
                result[child.tag] = value
        return result

    text = _strip_fence(text)
    try:
        root = ET.fromstring(text)
        return {root.tag: xml_to_dict(root)} if len(root) > 0 else {root.tag: root.text}
    except ET.ParseError:
        wrapped = f"<root>{text}</root>"
        root = ET.fromstring(wrapped)
        return xml_to_dict(root)


def parse_yaml(text: str) -> Dict[str, Any]:
    """Parse a YAML payload, tolerating a leading ``` fence."""
    return yaml.safe_load(_strip_fence(text))


_PARSER_MAP = {
    "json": parse_json,
    "xml": parse_xml,
    "yaml": parse_yaml,
}


# ---------------------------------------------------------------------------
# Extraction + coercion
# ---------------------------------------------------------------------------


#: Distinguishes "the path is not in the payload" from "the payload holds
#: null there". Both used to arrive as ``None``, which is why a model that
#: answered with the wrong keys looked like one that answered.
MISSING = object()


def _walk(data: Any, chain_path: List[str]) -> Any:
    """Follow ``chain_path`` into ``data``; return :data:`MISSING` if absent."""
    current = data
    for key in chain_path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return MISSING
    return current


def extract_value_by_path(data: Dict[str, Any], chain_path: List[str]) -> Any:
    """Walk ``data`` following ``chain_path``; return ``None`` if missing.

    Kept for callers that only need the value. Use :func:`_walk` when the
    difference between absent and null matters.
    """
    value = _walk(data, chain_path)
    return None if value is MISSING else value


def _resolve_field(parsed: Any, chain_path: List[str], parser: ParserFormat) -> Any:
    """Resolve one field, tolerating XML's mandatory document element.

    XML has to have exactly one root, so ``<r><result>X</result></r>``
    parses to ``{"r": {"result": "X"}}`` while the caller quite reasonably
    wrote ``fields=["result: str"]``. Descending through a lone dict root
    makes that work — including for this module's own docstring example,
    which was wrong for exactly this reason.

    Not applied to JSON or YAML: there a single top-level key is a real
    key the author chose, not a syntactic requirement, so descending into
    it would be a guess.
    """
    value = _walk(parsed, chain_path)
    if value is not MISSING or parser != "xml":
        return value
    if isinstance(parsed, dict) and len(parsed) == 1:
        (only,) = parsed.values()
        if isinstance(only, dict):
            return _walk(only, chain_path)
    return MISSING


def convert_type(value: Any, type_hint: str) -> Any:
    """Coerce ``value`` to ``type_hint`` (str / int / float / bool / …).

    Unknown type hints and unconvertible values pass through unchanged.
    Booleans handle string forms (``"true"`` / ``"1"`` / ``"yes"``).
    ``None`` stays ``None`` except for ``bool``/``string`` which normalise.
    """
    type_hint = type_hint.lower().strip()

    if type_hint in ("bool", "boolean"):
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            v = value.lower().strip()
            if v in ("true", "1", "yes"):
                return True
            if v in ("false", "0", "no", ""):
                return False
        return bool(value)

    if value is None:
        return None

    if type_hint == "int":
        try:
            return int(value)
        except (ValueError, TypeError):
            return value
    if type_hint in ("float", "number"):
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    if type_hint in ("str", "string"):
        return str(value).strip() if value is not None else ""
    return value


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def apply_validators(
    result: Dict[str, Any],
    validators: Dict[str, List[Any]],
) -> Optional[str]:
    """Apply per-field validators. Returns None on success, error string on fail.

    Values prefixed with ``@`` in the allowed list act as defaults — when the
    validated value is missing or unrecognised, the ``@``-prefixed value is
    substituted (with the ``@`` stripped). If no default is defined and the
    value is invalid, this returns a human-readable error string and does
    NOT mutate ``result``.
    """
    for field_name, allowed_values in validators.items():
        clean_values = [v.lstrip("@") if isinstance(v, str) else v for v in allowed_values]
        default_value = next(
            (v.lstrip("@") for v in allowed_values if isinstance(v, str) and v.startswith("@")),
            None,
        )
        value = result.get(field_name)
        if value is None or value not in clean_values:
            if default_value is not None:
                result[field_name] = default_value
            else:
                return f"Validation failed: '{field_name}' value {value!r} not in {clean_values}"
    return None


# ---------------------------------------------------------------------------
# Top-level entry point used by LLMOp
# ---------------------------------------------------------------------------


def parse_and_extract(
    text: str,
    parser: ParserFormat,
    fields: List[ExtractField],
    validators: Optional[Dict[str, List[Any]]] = None,
) -> Dict[str, Any]:
    """Parse ``text``, extract ``fields``, and optionally validate.

    Always returns a dict shaped as ``{**field_values, "error": None|str}``.
    Never raises — the ``error`` value tells the caller whether to retry.
    Semantics match the old ``ParserOp._process`` exactly so the surface
    LLMOp exposes is a faithful merge of what ``ask()`` provided before.
    """
    if validators is not None and not isinstance(validators, dict):
        return {
            "error": (f"validators must be a dict, got {type(validators).__name__}: {validators!r}")
        }
    if not text:
        return {"error": "Empty input text"}

    backend = _PARSER_MAP.get(parser)
    if backend is None:
        return {"error": f"Unknown parser format: {parser!r}"}

    try:
        parsed_data = backend(text)
    except Exception as e:
        return {"error": f"Parse error ({parser}): {e}"}

    result: Dict[str, Any] = {}
    missing: List[str] = []
    for field in fields:
        raw = _resolve_field(parsed_data, field.chain_path, parser)
        if raw is MISSING:
            if not field.optional:
                missing.append(".".join(field.chain_path))
            raw = None
        result[field.output_key] = convert_type(raw, field.type_hint)

    if validators:
        err = apply_validators(result, validators)
        if err is not None:
            return {"error": err}
        # A validator's ``@default`` counts as an answer, so a field it
        # filled is no longer missing.
        missing = [p for p in missing if result.get(p.split(".")[-1]) is None]

    if missing:
        # Well-formed output with the wrong keys is a semantic failure, and
        # reporting it as one is what lets ``max_retries`` fire. It used to
        # come back as ``{"result": None, "error": None}`` — indistinguishable
        # from the model answering null on purpose, which still is not an
        # error here.
        return {
            **result,
            "error": (
                f"Missing field(s) in {parser} output: {', '.join(missing)}. "
                f"Parsed keys: {sorted(parsed_data) if isinstance(parsed_data, dict) else '—'}"
            ),
        }

    result["error"] = None
    return result
