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

The function ALWAYS returns a dict with the requested field keys plus an
``error`` key that is either ``None`` (success) or a human-readable string
(failure). It never raises — LLMOp uses the ``error`` value to decide
whether to trigger a semantic-retry.
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
    """

    output_key: str
    chain_path: List[str]
    type_hint: str

    @classmethod
    def from_string(cls, schema_str: str) -> "ExtractField":
        """Parse a schema string like ``"user.address.city: str"``.

        Missing type hint defaults to ``Any``.
        """
        if ":" not in schema_str:
            schema_str += ": Any"
        chain_text, type_hint = schema_str.split(":", 1)
        chain_path = chain_text.strip().split(".")
        return cls(
            output_key=chain_path[-1],
            chain_path=chain_path,
            type_hint=type_hint.strip(),
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
            if len(child) == 0:
                result[child.tag] = child.text
            else:
                child_dict = xml_to_dict(child)
                if child.tag in result:
                    if not isinstance(result[child.tag], list):
                        result[child.tag] = [result[child.tag]]
                    result[child.tag].append(child_dict)
                else:
                    result[child.tag] = child_dict
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


def extract_value_by_path(data: Dict[str, Any], chain_path: List[str]) -> Any:
    """Walk ``data`` following ``chain_path``; return ``None`` if missing."""
    current = data
    for key in chain_path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


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
    for field in fields:
        raw = extract_value_by_path(parsed_data, field.chain_path)
        result[field.output_key] = convert_type(raw, field.type_hint)

    if validators:
        err = apply_validators(result, validators)
        if err is not None:
            return {"error": err}

    result["error"] = None
    return result
