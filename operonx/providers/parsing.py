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

Rules that are easy to get backwards:

- **A field the payload does not contain is an error**, so ``max_retries``
  can fire. A field the payload sets to ``null`` is an answer, and is not.
  Mark a field ``"name?: type"`` when absence is expected — a *union
  schema*, where one field list covers several response shapes, needs
  this on every entry that is not always present.
- **An XML document element is stripped when it gets in the way.** XML must
  have exactly one root, so a path written against the payload
  (``"result"``) is matched inside a lone root as well as at the top — and
  when the root shares the field's name, a scalar field reads the value
  inside it. JSON and YAML get no such treatment — there a single
  top-level key is a key the author meant.
- **A structure where a single value was declared is an error.** A
  ``str`` / ``int`` / ``float`` / ``bool`` field that lands on a subtree
  reports it rather than coercing it (``str()`` of a dict is a Python
  repr). Ask for a path inside it, or declare the field ``: dict``. A
  validator ``@default`` still stands in for it, as for any unrecognised
  value.
"""

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Union

import yaml

__all__ = [
    "ParserFormat",
    "Validators",
    "ExtractField",
    "check_output_keys",
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

#: What ``validators=`` accepts.
#:
#: * ``{field: [allowed, ...]}`` — per-field allow-list, with an
#:   ``"@default"`` entry standing in for an unrecognised value (``"@@x"``
#:   is the literal ``"@x"``). An optional field the payload left out is
#:   not checked.
#: * ``Callable[[dict], bool]`` — a predicate over the **whole** parsed
#:   dict, for a shape no per-field list can express ("``result`` must
#:   be a dict containing ``violation``"). Returning False fails the
#:   parse, which is what drives the op's semantic retry.
Validators = Union[Dict[str, List[Any]], Callable[[Dict[str, Any]], bool]]


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

        The output key is the path's last segment. ``as`` names it instead,
        as ``import a.b as c`` does — needed when two paths end in the same
        leaf, which would otherwise write one key::

            "user.id as user_id: str"
            "order.id as order_id?: str"   # optional, aliased

        Raises:
            ValueError: an ``as`` with no name, or a name that is not an
                identifier.
        """
        if ":" not in schema_str:
            schema_str += ": Any"
        chain_text, type_hint = schema_str.split(":", 1)
        chain_text = chain_text.strip()
        optional = chain_text.endswith("?")
        if optional:
            chain_text = chain_text[:-1].strip()
        alias = None
        aliased = _ALIAS.fullmatch(chain_text)
        if aliased:
            chain_text, alias = aliased.group(1), (aliased.group(2) or "").strip()
            if not alias.isidentifier():
                raise ValueError(
                    f"Field {schema_str!r}: 'as' must be followed by an output "
                    f"name (an identifier), got {alias!r}."
                )
            # "user.id? as uid" reads as naturally as "user.id as uid?".
            if chain_text.endswith("?"):
                optional = True
                chain_text = chain_text[:-1].strip()
        chain_path = chain_text.split(".")
        return cls(
            output_key=alias or chain_path[-1],
            chain_path=chain_path,
            type_hint=type_hint.strip(),
            optional=optional,
        )


#: ``<path> as <name>``. Whitespace on both sides of ``as`` is required, so
#: a key that merely contains the letters (``meta.as``) is still a path.
_ALIAS = re.compile(r"(.*\S)\s+as(?:\s+(.*))?")


def check_output_keys(fields: List[ExtractField], reserved: Iterable[str] = ()) -> Optional[str]:
    """Name every output key two fields share, or that ``reserved`` holds.

    Returns None when the keys are distinct, else a message naming each
    collision and the paths behind it. Two paths ending in the same leaf
    used to write one key — last writer wins, ``error: None``.
    """
    reserved = set(reserved)
    by_key: Dict[str, List[str]] = {}
    for f in fields:
        by_key.setdefault(f.output_key, []).append(".".join(f.chain_path))
    problems = [
        f"'{key}' ← {' and '.join(paths)}" for key, paths in by_key.items() if len(paths) > 1
    ]
    problems += [
        f"'{key}' ← {paths[0]} is already an output of the op"
        for key, paths in by_key.items()
        if key in reserved
    ]
    if not problems:
        return None
    return (
        f"Field output keys collide: {'; '.join(problems)}. Name each one with "
        f"'as', e.g. 'user.id as user_id: str'."
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


#: Hints that name a single value. A dict (a parsed subtree) is never one,
#: and coercing it to one is how ``"{'type': 'greet'}"`` reached callers.
_SCALAR_HINTS = frozenset({"str", "string", "int", "float", "number", "bool", "boolean"})


def _wants_scalar(type_hint: Optional[str]) -> bool:
    return (type_hint or "").lower().strip() in _SCALAR_HINTS


def _is_subtree(value: Any) -> bool:
    """A parsed structure: a dict, or repeated siblings holding one."""
    if isinstance(value, dict):
        return True
    return isinstance(value, list) and any(isinstance(v, dict) for v in value)


def _describe_structure(value: Any) -> str:
    if isinstance(value, dict):
        return f"a structure (keys: {', '.join(map(str, value)) or '—'})"
    return f"{len(value)} repeated elements, some of them structures"


def _resolve_field(
    parsed: Any, chain_path: List[str], parser: ParserFormat, type_hint: Optional[str] = None
) -> Any:
    """Resolve one field, tolerating XML's mandatory document element.

    XML has to have exactly one root, so ``<r><result>X</result></r>``
    parses to ``{"r": {"result": "X"}}`` while the caller quite reasonably
    wrote ``fields=["result: str"]``. Descending through a lone dict root
    makes that work — including for this module's own docstring example,
    which was wrong for exactly this reason.

    The root can also share the field's name. ``<action>…</action>`` read
    at the top is then the whole document, and the root was only tried as
    a wrapper when the top-level walk *missed* — so a scalar field got the
    root's child dict. When the declared type is a scalar and the top-level
    reading is a structure, the reading inside the root wins if it is a
    value; otherwise the structure is returned and the caller reports the
    mismatch (see :func:`parse_and_extract`).

    Not applied to JSON or YAML: there a single top-level key is a real
    key the author chose, not a syntactic requirement, so descending into
    it would be a guess.
    """
    value = _walk(parsed, chain_path)
    if parser != "xml" or not (isinstance(parsed, dict) and len(parsed) == 1):
        return value
    (only,) = parsed.values()
    if not isinstance(only, dict):
        return value
    if value is MISSING:
        return _walk(only, chain_path)
    if _wants_scalar(type_hint) and _is_subtree(value):
        inner = _walk(only, chain_path)
        if inner is not MISSING and not _is_subtree(inner):
            return inner
    return value


def convert_type(value: Any, type_hint: str) -> Any:
    """Coerce ``value`` to ``type_hint`` (str / int / float / bool / …).

    A **list** is coerced element-wise. Repeated XML siblings now build a
    list, and applying a scalar hint to the list object made the output
    type depend on the data: one ``<item>`` gave ``"a"``, two gave the
    string ``"['a', 'b']"``. With ``: int`` the whole list passed through
    untouched, so a field declared ``int`` held a list. Both silent.

    Unknown type hints and unconvertible values pass through unchanged.
    Booleans handle string forms (``"true"`` / ``"1"`` / ``"yes"``).
    ``None`` stays ``None`` except for ``bool``/``string`` which normalise.
    """
    type_hint = type_hint.lower().strip()

    if isinstance(value, list):
        return [convert_type(v, type_hint) for v in value]

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


def _allowed_entry(value: Any) -> tuple:
    """Read one allow-list entry as ``(value, is_default)``.

    A leading ``@`` marks the default and ``@@`` is a literal ``@``, so the
    run of leading ``@`` decides: odd means default, and each pair left is
    one literal ``@``. ``"@x"`` is default ``x``, ``"@@x"`` is the value
    ``@x``, ``"@@@x"`` is default ``@x``. ``lstrip("@")`` used to eat the
    whole run, so no allowed value could start with ``@``.
    """
    if not isinstance(value, str) or not value.startswith("@"):
        return value, False
    run = len(value) - len(value.lstrip("@"))
    return "@" * (run // 2) + value[run:], run % 2 == 1


def apply_validators(
    result: Dict[str, Any],
    validators: "Validators",
    absent: Iterable[str] = (),
) -> Optional[str]:
    """Apply validators. Returns None on success, an error string on failure.

    Two forms, see :data:`Validators`.

    **Allow-list** — values prefixed with ``@`` act as defaults: when the
    value is unrecognised the ``@``-prefixed value is substituted (the
    marker stripped; write ``@@`` for a value that really starts with
    ``@``). ``None`` is unrecognised unless listed. Without a default, an
    invalid value returns a human-readable error and does NOT mutate
    ``result``.

    ``absent`` names optional fields the payload did not contain. Their
    allow-lists are skipped: absence was declared acceptable, and filling
    one with the default would make "absent" read as "answered".

    **Callable** — receives the whole parsed dict and returns a bool. Use
    it for cross-field or structural checks an allow-list cannot state.
    The predicate is called defensively: a raising validator is reported
    as a failed validation rather than propagating, because it runs on
    model output and a malformed answer must not crash the graph.
    """
    if callable(validators):
        name = getattr(validators, "__name__", type(validators).__name__)
        try:
            ok = validators(result)
        except Exception as e:
            return f"Validation failed: {name}() raised {type(e).__name__}: {e}"
        return None if ok else f"Validation failed: {name}() rejected the parsed output"

    absent = set(absent)
    for field_name, allowed_values in validators.items():
        if field_name in absent:
            continue
        entries = [_allowed_entry(v) for v in allowed_values]
        clean_values = [v for v, _ in entries]
        default = next((v for v, is_default in entries if is_default), MISSING)
        value = result.get(field_name)
        if value not in clean_values:
            if default is not MISSING:
                result[field_name] = default
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
    validators: Optional["Validators"] = None,
) -> Dict[str, Any]:
    """Parse ``text``, extract ``fields``, and optionally validate.

    Always returns a dict shaped as ``{**field_values, "error": None|str}``.
    Never raises — the ``error`` value tells the caller whether to retry.
    Semantics match the old ``ParserOp._process`` exactly so the surface
    LLMOp exposes is a faithful merge of what ``ask()`` provided before.
    """
    if validators is not None and not (isinstance(validators, dict) or callable(validators)):
        return {
            "error": (
                f"validators must be a dict or a callable, got "
                f"{type(validators).__name__}: {validators!r}"
            )
        }
    collision = check_output_keys(fields, reserved=("error",))
    if collision is not None:
        return {"error": collision}
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
    missing: List[ExtractField] = []
    # Optional fields the payload left out: None, and not validated.
    absent: List[str] = []
    # output_key -> the structure found where a single value was declared.
    misshapen: Dict[str, Any] = {}
    for field in fields:
        raw = _resolve_field(parsed_data, field.chain_path, parser, field.type_hint)
        if raw is MISSING:
            if field.optional:
                absent.append(field.output_key)
            else:
                missing.append(field)
            raw = None
        elif _wants_scalar(field.type_hint) and _is_subtree(raw):
            # Not coerced: ``str()`` of a dict is a Python repr, ``int()``
            # leaves it a dict and ``bool()`` makes it True — each a
            # plausible answer with ``error: None``. It stays raw so a
            # validator's ``@default`` can stand in for it like for any
            # unrecognised value, and is an error below if nothing did.
            misshapen[field.output_key] = raw
            result[field.output_key] = raw
            continue
        result[field.output_key] = convert_type(raw, field.type_hint)

    if validators:
        err = apply_validators(result, validators, absent=absent)
        if err is not None:
            return {"error": err}
        # A validator's ``@default`` counts as an answer, so a field it
        # filled is no longer missing. Looked up by output key: the path's
        # last segment is not the key once a field is aliased.
        missing = [f for f in missing if result.get(f.output_key) is None]

    # Identity, not equality: a default that replaced the structure is an
    # answer; the structure itself still sitting there is not.
    wrong_shape = [key for key, raw in misshapen.items() if result.get(key) is raw]
    if wrong_shape:
        for key in wrong_shape:
            result[key] = None
        hints = {f.output_key: f for f in fields}
        described = ", ".join(
            f"'{key}' is declared {hints[key].type_hint} but holds "
            f"{_describe_structure(misshapen[key])}"
            for key in wrong_shape
        )
        return {
            **result,
            "error": (
                f"Structure where a single value was expected in {parser} output: "
                f"{described}. Ask for a path inside it (e.g. "
                f"'{'.'.join(hints[wrong_shape[0]].chain_path)}.<child>: str') "
                f"or declare it ': dict'."
            ),
        }

    if missing:
        # Well-formed output with the wrong keys is a semantic failure, and
        # reporting it as one is what lets ``max_retries`` fire. It used to
        # come back as ``{"result": None, "error": None}`` — indistinguishable
        # from the model answering null on purpose, which still is not an
        # error here.
        return {
            **result,
            "error": (
                f"Missing field(s) in {parser} output: "
                f"{', '.join('.'.join(f.chain_path) for f in missing)}. "
                f"Parsed keys: {sorted(parsed_data) if isinstance(parsed_data, dict) else '—'}"
            ),
        }

    result["error"] = None
    return result
